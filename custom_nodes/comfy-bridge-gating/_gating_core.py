"""comfy-bridge gating 纯逻辑（无副作用，可单测）。
__init__.py import 本模块并在 import 期调用 prune/fail_closed_prune。
本模块不 import `nodes`、不读环境、不发请求。"""
import logging
import math

_log = logging.getLogger("comfy-bridge-gating")

_SEG_PREFIX = "comfy_api_nodes.nodes_"
# 钉死上游认证字段契约（Codex 五轮 #3）。test_gating_inventory.py 会断言其与
# comfy_api/latest 实际枚举一致，漂移即失败。
AUTH_KEYS = frozenset({"auth_token_comfy_org", "api_key_comfy_org"})


def segment_from_module(mod) -> str | None:
    if isinstance(mod, str) and mod.startswith(_SEG_PREFIX):
        return mod[len(_SEG_PREFIX):].lower()
    return None


def has_api_auth_input(cls) -> bool:
    """信号③弱兜底：节点 INPUT_TYPES 的 hidden 是否含 comfy api 认证字段。
    ComfyUI 仅对 is_api_node=True 自动注入这些字段，故对 api_node=false 节点不保证命中。"""
    try:
        spec = cls.INPUT_TYPES()
    except Exception:
        return False
    hidden = (spec or {}).get("hidden", {}) or {}
    names = set(hidden.keys())
    if AUTH_KEYS & names:
        return True
    return any(isinstance(v, str) and v in AUTH_KEYS for v in hidden.values())


class GatingCtx:
    __slots__ = ("hidden_classes", "allowed_segments", "backend_segments",
                 "capability_managed", "loaded_segments", "loaded_node_classes")

    def __init__(self, hidden_classes, allowed_segments, backend_segments,
                 capability_managed, loaded_segments, loaded_node_classes):
        self.hidden_classes = set(hidden_classes)
        self.allowed_segments = set(allowed_segments)
        self.backend_segments = set(backend_segments)
        self.capability_managed = set(capability_managed)
        self.loaded_segments = set(loaded_segments)
        self.loaded_node_classes = set(loaded_node_classes)


def build_ctx(gating: dict) -> GatingCtx:
    vendor_meta = gating.get("vendor_meta") or {}
    backend_segments = {
        m.get("python_module_segment")
        for m in vendor_meta.values() if isinstance(m, dict)
    }
    backend_segments.discard(None)
    return GatingCtx(
        hidden_classes=gating.get("hidden_node_classes", []),
        allowed_segments=gating.get("allowed_vendors", []),
        backend_segments=backend_segments,
        capability_managed=gating.get("capability_managed_node_classes", []),
        loaded_segments=gating.get("loaded_segments", []),
        loaded_node_classes=gating.get("loaded_node_classes", []),
    )


def decide_hide(name, cls_module, segment, is_api, has_auth, ctx: GatingCtx) -> bool:
    """True=隐藏。见 spec §3.1。"""
    if segment is None and not str(cls_module).startswith("comfy_api_nodes."):
        return False                                   # 真本地节点 → 保留
    if segment is None:
        _log.warning("comfy_api node %s 无法解析 segment(%s)，fail-closed", name, cls_module)
        return True                                    # 元数据漂移
    if name in ctx.hidden_classes:
        return True                                    # denylist
    if not ctx.allowed_segments:
        return True                                    # 空 allowlist
    if segment not in ctx.allowed_segments:
        return True                                    # 禁用厂商
    if segment not in ctx.backend_segments:
        _log.warning("allowed segment %s 无 backend，fail-closed %s", segment, name)
        return True                                    # 允许但无 backend
    is_backend_node = is_api or (name in ctx.capability_managed) or has_auth
    if is_backend_node:
        if segment not in ctx.loaded_segments:
            _log.warning("segment %s backend 未加载，fail-closed %s", segment, name)
            return True                                # backend 未 positively loaded
        if name not in ctx.loaded_node_classes:
            return True                                # 型号不支持
    return False                                        # 纯 helper → 保留


def prune(mappings, display, gating, *, cls_meta):
    """对 NODE_CLASS_MAPPINGS 执行剪枝。cls_meta(name, cls) -> (cls_module, segment, is_api, has_auth)。
    返回被删 name 列表。"""
    ctx = build_ctx(gating)
    removed = []
    for name in list(mappings.keys()):
        cls = mappings[name]
        cls_module, segment, is_api, has_auth = cls_meta(name, cls)
        if decide_hide(name, cls_module, segment, is_api, has_auth, ctx):
            del mappings[name]
            display.pop(name, None)
            removed.append(name)
    return removed


def fail_closed_prune(mappings, display, *, cls_meta):
    """超时/不可达兜底：删除所有 comfy_api_nodes 节点（segment 可解析 或 __module__ 兜底）。
    cls_meta(name, cls) -> (cls_module, segment)。返回被删 name 列表。"""
    removed = []
    for name in list(mappings.keys()):
        cls_module, segment = cls_meta(name, mappings[name])
        if segment is not None or str(cls_module).startswith("comfy_api_nodes."):
            del mappings[name]
            display.pop(name, None)
            removed.append(name)
    return removed


def _module_of(cls):
    # RELATIVE_PYTHON_MODULE 优先（server.py 据它产 /object_info）；缺失/解析不出时兜底 __module__
    # ——comfy_api_nodes 节点的 __module__ 通常即 comfy_api_nodes.nodes_<seg>（Codex plan #1/#2）。
    return getattr(cls, "RELATIVE_PYTHON_MODULE", None) or getattr(cls, "__module__", None)


def node_meta(cls):
    """(cls_module, segment, is_api, has_auth)。非 comfy_api_nodes 节点短路返回，
    绝不对本地/三方节点调 API_NODE / INPUT_TYPES（Codex plan #1：本地节点 INPUT_TYPES 可能抛/有副作用）。"""
    mod = _module_of(cls)
    segment = segment_from_module(mod)
    if segment is None and not (isinstance(mod, str) and mod.startswith("comfy_api_nodes.")):
        return mod, None, False, False
    return mod, segment, bool(getattr(cls, "API_NODE", False)), has_api_auth_input(cls)


def node_meta_min(cls):
    """(cls_module, segment) for fail_closed_prune；__module__ 兜底，漂移时 fail-closed 路径不漏（Codex plan #2）。"""
    mod = _module_of(cls)
    return mod, segment_from_module(mod)


def reaches_symbols(module_source, entry_name, symbols, max_depth=3, class_name=None):
    """AST 跟随：从 entry_name 函数出发跟进同模块内 helper 调用，判断是否触达 symbols 任一名字
    （Name 或 Attribute）。源码解析失败 → True（fail-closed）。inventory oracle 与其 fixture
    共用本函数，避免测试自写一份逻辑（Codex plan #5）。

    class_name: 若指定，优先在该类体内查找 entry_name 方法（避免同模块多个同名方法覆盖，
    Codex 最终审 #1 oracle 修正）。找不到时回退到模块级函数字典。"""
    import ast
    try:
        tree = ast.parse(module_source)
    except Exception:
        return True

    # 收集模块级函数（含类内方法，后者可能覆盖同名模块级函数，但 helper 跟踪仍需）
    # 同时收集 async def（AsyncFunctionDef）以支持 async def execute（Codex 收尾审 #2）
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    # 若指定 class_name，优先在目标类体内查找 entry_name（唯一性保证）
    entry = None
    if class_name:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in ast.walk(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == entry_name:
                        entry = item
                        break
                break

    if entry is None:
        entry = funcs.get(entry_name)

    def hits(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in symbols:
                return True
            if isinstance(sub, ast.Attribute) and sub.attr in symbols:
                return True
        return False

    def follows(node, depth):
        if depth > max_depth:
            return False
        if hits(node):
            return True
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if fn in funcs and follows(funcs[fn], depth + 1):
                    return True
        return False

    return follows(entry, 0) if entry else hits(tree)


def classify_payload(status, gating):
    """决定动作：'fail_closed' | 'skip' | 'prune'。
    畸形/无效 schema 一律 fail_closed（不信任），仅显式 gating_enabled=False 才 skip（用户主动关）。
    （Codex 收尾审 #1：恶意/畸形 payload 不得 fail-open）。"""
    if status != "ok":
        return "fail_closed"
    if not isinstance(gating, dict):
        return "fail_closed"
    enabled = gating.get("gating_enabled")
    if not isinstance(enabled, bool):        # 缺失 / 非布尔 → schema 偏差 → fail_closed
        return "fail_closed"
    if enabled is False:                     # 显式关闭 → 不剪
        return "skip"
    return "prune"


def parse_timeout(raw, default=180.0):
    """解析 BRIDGE_GATING_STARTUP_TIMEOUT；畸形/非有限/负值回退默认（Codex 收尾审 #3）。0=无限等待。"""
    if raw is None:
        return default
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        _log.warning("BRIDGE_GATING_STARTUP_TIMEOUT 无效值 %r，回退默认 %s", raw, default)
        return default
    if not math.isfinite(v) or v < 0:
        _log.warning("BRIDGE_GATING_STARTUP_TIMEOUT 非有限/负值 %r，回退默认 %s", raw, default)
        return default
    return v


def fetch_gating_blocking(fetch_fn, timeout_s, *, clock, sleep, log_every_s=10.0):
    """阻塞重试直到 fetch_fn() 成功或超时。timeout_s==0 → 无限阻塞。
    fetch_fn 抛异常视为未就绪。返回 ("ok", payload) 或 ("timeout", None)。
    clock/sleep 注入以便测试（生产传 time.monotonic / time.sleep）。"""
    start = clock()
    last_log = start
    while True:
        try:
            return ("ok", fetch_fn())
        except Exception:
            now = clock()
            if timeout_s and (now - start) >= timeout_s:
                return ("timeout", None)
            if (now - last_log) >= log_every_s:
                _log.info("等待 bridge gating 就绪…")
                last_log = now
            sleep(2.0)
