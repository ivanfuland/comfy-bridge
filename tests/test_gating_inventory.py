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
    try:
        src = inspect.getsource(inspect.getmodule(cls))
    except Exception:
        return True
    entry = getattr(cls, "FUNCTION", "execute")
    return core.reaches_symbols(src, entry, _BACKEND_SYMBOLS)


def _iter_allowed_vendor_nodes():
    nodes = _need("nodes")
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
    offenders = []
    for name, cls in _iter_allowed_vendor_nodes():
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
    nodes = _need("nodes")
    bad = [n for n in PURE_HELPER_EXEMPT
           if n in nodes.NODE_CLASS_MAPPINGS and _reaches_backend(nodes.NODE_CLASS_MAPPINGS[n])]
    assert not bad, f"以下豁免 helper 实际触达后端路径：{bad}"


def test_reaches_symbols_indirect_helper_via_core():
    # fixture 直接调被测的 core.reaches_symbols（不再自写一份 AST walker，Codex 五轮 #2 + plan #5）
    src = ("def _do(x):\n    return sync_op(x)\n"
           "def run(self, x):\n    return _do(x)\n")
    assert core.reaches_symbols(src, "run", _BACKEND_SYMBOLS) is True
    assert core.reaches_symbols("def run(self):\n    return 1\n", "run", _BACKEND_SYMBOLS) is False
