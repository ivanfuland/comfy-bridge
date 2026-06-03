"""inventory 守护（Codex 三轮 #2 + 四轮 #2/#3 + 五轮 #2 + plan #4/#5）。
oracle 用节点 schema/AST 行为判定「后端节点」，独立于 adapter registry，
确保每个后端节点都被某 backend supported / denylist / 显式豁免覆盖，
防 registry 漏列让 ByteDanceCreateAsset 类 bug 复现。
不静默 skip：COMFYUI_REQUIRE_INVENTORY=1 时 import 失败 → fail（plan #4）。"""
import importlib
import importlib.util
import inspect
import os
import pathlib
import pytest

_REQUIRE = bool(os.environ.get("COMFYUI_REQUIRE_INVENTORY"))
_CORE_PATH = (pathlib.Path(__file__).resolve().parent.parent
              / "custom_nodes" / "comfy-bridge-gating" / "_gating_core.py")


def _load_core():
    spec = importlib.util.spec_from_file_location("gating_core", _CORE_PATH)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


core = _load_core()
PURE_HELPER_EXEMPT: set[str] = set()      # 已知纯本地 helper；下方对每条断言「无后端执行路径」
_BACKEND_SYMBOLS = {"sync_op", "poll_op", "ApiEndpoint", "upload", "proxy"}


def _need(modname):
    """import 上游模块；失败按 COMFYUI_REQUIRE_INVENTORY 决定 fail / skip（不静默假绿，plan #4）。"""
    try:
        return importlib.import_module(modname)
    except Exception as e:
        msg = f"无法 import {modname}（设 COMFYUI_PATH 或在 ComfyUI venv 跑）：{e}"
        if _REQUIRE:
            pytest.fail(msg)
        pytest.skip(msg)


def _load_nodes_with_api():
    """import nodes 并确保 api nodes 已注册（Codex 最终审 #1：init_builtin_api_nodes 是 async，
    仅 import nodes 不触发，导致 NODE_CLASS_MAPPINGS 仅含核心节点 → 假绿）。

    comfy_api_nodes/util/client.py 在模块级 `from server import PromptServer`，
    而 server.py 需要完整 ComfyUI 运行时（aiohttp/PIL/app.frontend_management 等）。
    解决方案：在调用 init_builtin_api_nodes 之前，把一个轻量 stub 注入
    sys.modules['server']，阻断该重依赖链，同时不影响节点元数据注册逻辑。"""
    import sys, types, asyncio

    # CI/无 GPU 环境:ComfyUI model_management 在 import 期调 torch.cuda.current_device()，
    # cpu-only torch 会抛 "Torch not compiled with CUDA enabled"。先强制 ComfyUI CPU 模式
    # （args.cpu=True，让 get_torch_device 返回 cpu，不碰 CUDA）。
    _saved_argv = sys.argv
    sys.argv = [sys.argv[0] if sys.argv else "pytest", "--cpu"]
    try:
        from comfy.cli_args import args as _comfy_args
        _comfy_args.cpu = True
    except Exception:
        pass
    finally:
        sys.argv = _saved_argv

    nodes = _need("nodes")
    have_api = any(
        isinstance(getattr(c, "RELATIVE_PYTHON_MODULE", None), str)
        and getattr(c, "RELATIVE_PYTHON_MODULE", "").startswith("comfy_api_nodes")
        for c in nodes.NODE_CLASS_MAPPINGS.values()
    )
    if not have_api and hasattr(nodes, "init_builtin_api_nodes"):
        # 注入 server stub，防止 client.py 触发完整 server.py import 链（Codex 最终审 #1）
        _server_stub = types.ModuleType("server")
        _server_stub.PromptServer = type("PromptServer", (), {"instance": None})
        _prev_server = sys.modules.get("server")
        sys.modules.setdefault("server", _server_stub)
        try:
            asyncio.run(nodes.init_builtin_api_nodes())
        except Exception as e:
            msg = f"init_builtin_api_nodes 失败：{e}"
            if _REQUIRE:
                pytest.fail(msg)
            else:
                pytest.skip(msg)
        finally:
            # 恢复（若之前有真 server 模块则还原，否则保留 stub 以供后续 import 复用）
            if _prev_server is not None:
                sys.modules["server"] = _prev_server
    return nodes


def _auth_enum_keys():
    """从 comfy_api.latest.io.Hidden 枚举提取认证字段名集合。
    上游结构：io.Hidden 是 StrEnum，成员名为小写（auth_token_comfy_org / api_key_comfy_org），
    .value 为大写字符串（AUTH_TOKEN_COMFY_ORG / API_KEY_COMFY_ORG）。
    core.AUTH_KEYS 存储小写成员名，与 has_api_auth_input 检查 hidden dict key 一致。"""
    mod = _need("comfy_api.latest")
    io = getattr(mod, "io", mod)
    hidden_cls = getattr(io, "Hidden", None)
    if hidden_cls is None:
        return set()
    found = set()
    for name in ("auth_token_comfy_org", "api_key_comfy_org"):
        member = getattr(hidden_cls, name, None)
        if member is not None and hasattr(member, "value"):
            found.add(name)   # 存小写名，与 core.AUTH_KEYS 对齐
    return found


def test_auth_key_contract_matches_upstream():
    keys = _auth_enum_keys()
    assert keys, "未能从 comfy_api.latest 定位认证字段枚举——实现时核对上游结构并更新本定位逻辑"
    assert keys == set(core.AUTH_KEYS), (
        f"上游认证字段 {keys} 与 _gating_core.AUTH_KEYS {set(core.AUTH_KEYS)} 不一致")


def _reaches_backend(cls) -> bool:
    """判断节点类是否触达后端符号。
    传入 class_name 以正确定位模块内同名方法（Codex 最终审 #1 oracle 修正：
    同模块多个类各有 execute 时，dict 覆盖导致错误命中）。
    FUNCTION 可能是框架动态注入的名字（如 EXECUTE_NORMALIZED），不在源码中出现；
    此时回退到 'execute'，仍限定在 class_name 范围内。
    只有当入口函数真正在源码中找到时，reaches_symbols 结果才可信；
    否则其 hits(tree) 回退会误判整个模块（含无关类的 sync_op）。"""
    import ast
    fn_attr = getattr(cls, "FUNCTION", "execute")
    # 候选 entry name 列表：先尝试 FUNCTION 属性值，再尝试 "execute"
    candidates = [fn_attr] if fn_attr == "execute" else [fn_attr, "execute"]
    try:
        src = inspect.getsource(inspect.getmodule(cls))
    except Exception:
        return True  # fail-closed
    # 解析模块 AST，确认入口函数是否真实存在于该类内
    try:
        tree = ast.parse(src)
    except Exception:
        return True  # fail-closed（解析失败）
    class_method_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
            for item in node.body:
                # 同时识别 async def execute（Codex 收尾审 #2）
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_method_names.add(item.name)
            break
    for entry in candidates:
        if entry not in class_method_names:
            continue  # 跳过不存在的入口，避免 hits(tree) 误判全模块
        if core.reaches_symbols(src, entry, _BACKEND_SYMBOLS, class_name=cls.__name__):
            return True
    # 若所有候选入口均不在类内，fail-open（保守：非预期情况，报告 False 避免误报）
    return False


def _iter_allowed_vendor_nodes():
    nodes = _load_nodes_with_api()   # 保证 api nodes 已注册（Codex 最终审 #1）
    from app.config import DEFAULT_ALLOWED_VENDORS
    allowed = set(DEFAULT_ALLOWED_VENDORS)
    for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
        if core.segment_from_module(getattr(cls, "RELATIVE_PYTHON_MODULE", None)) in allowed:
            yield name, cls


def _all_supported():
    from app.adapters import _BACKEND_REGISTRY
    s = set()
    for v in _BACKEND_REGISTRY.values():
        for b in v["backends"].values():
            s.update(b["supported_node_classes"])
    return s


def test_every_backend_node_is_governed():
    from app.config import DEFAULT_HIDDEN_NODE_CLASSES
    supported = _all_supported()
    denylist = set(DEFAULT_HIDDEN_NODE_CLASSES)
    # 先实体化，避免两次遍历生成器（_load_nodes_with_api 有副作用 asyncio.run，只应触发一次）
    items = list(_iter_allowed_vendor_nodes())
    names = {name for name, _ in items}
    # 防假绿（Codex 最终审 #1）：必须真正扫到上游 api 节点；若仅扫到 0 个允许厂商节点，
    # 这两条 assert 也会失败并给出明确诊断。
    assert "OpenAIGPTImage1" in names, (
        f"inventory 未扫到 api nodes（仅 {len(names)} 个允许厂商节点），"
        "请确认在 ComfyUI venv 下运行并设置 COMFYUI_REQUIRE_INVENTORY=1")
    assert "ByteDanceCreateImageAsset" in names, (
        f"inventory 未扫到 ByteDance api nodes（names={sorted(names)[:10]}…）")
    offenders = []
    for name, cls in items:
        is_backend = (bool(getattr(cls, "API_NODE", False))
                      or core.has_api_auth_input(cls)
                      or _reaches_backend(cls))
        if is_backend and name not in supported and name not in denylist and name not in PURE_HELPER_EXEMPT:
            offenders.append(name)
    assert not offenders, (
        f"未被治理的后端节点（应入某 backend supported / denylist / PURE_HELPER_EXEMPT）：{offenders}")


def test_exempt_helpers_have_no_backend_path():
    if not PURE_HELPER_EXEMPT:
        pytest.skip("PURE_HELPER_EXEMPT 为空")
    nodes = _load_nodes_with_api()
    bad = [n for n in PURE_HELPER_EXEMPT
           if n in nodes.NODE_CLASS_MAPPINGS and _reaches_backend(nodes.NODE_CLASS_MAPPINGS[n])]
    assert not bad, f"以下豁免 helper 实际触达后端路径：{bad}"


def test_reaches_symbols_indirect_helper_via_core():
    # fixture 直接调被测的 core.reaches_symbols（不再自写一份 AST walker，Codex 五轮 #2 + plan #5）
    src = ("def _do(x):\n    return sync_op(x)\n"
           "def run(self, x):\n    return _do(x)\n")
    assert core.reaches_symbols(src, "run", _BACKEND_SYMBOLS) is True
    assert core.reaches_symbols("def run(self):\n    return 1\n", "run", _BACKEND_SYMBOLS) is False
