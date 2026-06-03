import importlib.util
import pathlib
import pytest

_CORE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_nodes" / "comfy-bridge-gating" / "_gating_core.py"
)


def _load_core():
    spec = importlib.util.spec_from_file_location("gating_core", _CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_core()


def _ctx(**over):
    base = dict(
        hidden_classes=set(),
        allowed_segments={"openai", "bytedance"},
        backend_segments={"openai", "bytedance"},
        capability_managed={"ByteDanceCreateImageAsset", "ByteDanceSeedreamNode"},
        loaded_segments={"openai", "bytedance"},
        loaded_node_classes={"OpenAIGPTImage1", "ByteDanceSeedreamNode"},
    )
    base.update(over)
    return core.GatingCtx(**base)


def _h(name, segment, *, mod="comfy_api_nodes.nodes_x", is_api=False, auth=False, ctx=None):
    return core.decide_hide(name, mod, segment, is_api, auth, ctx or _ctx())


def test_disallowed_vendor_helper_hidden():
    assert _h("RecraftColorRGB", "recraft") is True

def test_denylist_helper_hidden():
    assert _h("X", "openai", ctx=_ctx(hidden_classes={"X"})) is True

def test_local_node_kept():
    assert core.decide_hide("MyLocal", "my.pkg.nodes", None, False, False, _ctx()) is False

def test_allowed_api_unsupported_hidden():
    assert _h("OpenAIDalle3", "openai", is_api=True) is True

def test_bytedance_asset_falai_hidden():
    assert _h("ByteDanceCreateImageAsset", "bytedance") is True

def test_bytedance_asset_native_kept():
    ctx = _ctx(loaded_node_classes={"ByteDanceCreateImageAsset"})
    assert _h("ByteDanceCreateImageAsset", "bytedance", ctx=ctx) is False

def test_generic_helper_kept():
    assert _h("OpenAIInputFiles", "openai") is False

def test_single_backend_new_api_node_hidden():
    assert _h("OpenAIBrandNewNode", "openai", is_api=True) is True

def test_backend_not_loaded_backend_node_hidden():
    ctx = _ctx(loaded_segments=set())
    assert _h("ByteDanceSeedreamNode", "bytedance", is_api=True, ctx=ctx) is True

def test_backend_not_loaded_pure_helper_kept():
    ctx = _ctx(loaded_segments=set())
    assert _h("SomePureHelper", "openai", ctx=ctx) is False

def test_empty_allowlist_fail_closed():
    assert _h("OpenAIGPTImage1", "openai", is_api=True, ctx=_ctx(allowed_segments=set())) is True

def test_allowed_segment_without_backend_fail_closed():
    ctx = _ctx(allowed_segments={"recraft"}, backend_segments={"openai"})
    assert _h("RecraftAnything", "recraft", ctx=ctx) is True

def test_metadata_drift_fail_closed():
    assert core.decide_hide("Weird", "comfy_api_nodes.weird", None, False, False, _ctx()) is True

def test_auth_input_signal_hides_unsupported():
    assert _h("LeakedAsset", "bytedance", auth=True) is True


def test_prune_removes_and_keeps():
    mappings = {"Keep": "ck", "Drop": "cd", "Local": "cl"}
    display = {"Keep": "k", "Drop": "d", "Local": "l"}
    meta = {
        "Keep": ("comfy_api_nodes.nodes_openai", "openai", True, False),
        "Drop": ("comfy_api_nodes.nodes_recraft", "recraft", False, False),
        "Local": ("my.pkg", None, False, False),
    }
    gating = dict(
        hidden_node_classes=[], allowed_vendors=["openai"],
        vendor_meta={"openai": {"python_module_segment": "openai", "expected_route_keys": ["openai"]}},
        capability_managed_node_classes=[], loaded_segments=["openai"],
        loaded_node_classes=["Keep"],
    )
    removed = core.prune(mappings, display, gating, cls_meta=lambda name, cls: meta[name])
    assert set(removed) == {"Drop"}
    assert set(mappings.keys()) == {"Keep", "Local"}
    assert "Drop" not in display


def test_fail_closed_prune_removes_all_comfy_api():
    mappings = {"A": "x", "B": "y", "Local": "z"}
    display = dict(mappings)
    meta = {
        "A": ("comfy_api_nodes.nodes_openai", "openai"),
        "B": ("comfy_api_nodes.weird", None),
        "Local": ("my.pkg", None),
    }
    removed = core.fail_closed_prune(mappings, display, cls_meta=lambda name, cls: meta[name])
    assert set(removed) == {"A", "B"}
    assert set(mappings.keys()) == {"Local"}


def test_node_meta_local_node_not_probed():
    probed = {"input_types": 0}

    class Boom:
        __module__ = "my.local.pack"
        @classmethod
        def INPUT_TYPES(cls):
            probed["input_types"] += 1
            raise RuntimeError("local node side effect")
    mod, segment, is_api, has_auth = core.node_meta(Boom)
    assert (segment, is_api, has_auth) == (None, False, False)
    assert probed["input_types"] == 0  # 短路：未探测


def test_node_meta_min_module_fallback():
    Fake = type("Fake", (), {"__module__": "comfy_api_nodes.weird"})
    mod, segment = core.node_meta_min(Fake)
    assert mod == "comfy_api_nodes.weird" and segment is None
    mappings = {"Fake": Fake}; display = {"Fake": "f"}
    removed = core.fail_closed_prune(mappings, display, cls_meta=lambda n, c: core.node_meta_min(c))
    assert removed == ["Fake"] and "Fake" not in mappings


def test_reaches_symbols_follows_indirect_helper():
    src = ("def _do(x):\n    return sync_op(x)\n"
           "def run(self, x):\n    return _do(x)\n")
    assert core.reaches_symbols(src, "run", {"sync_op"}) is True
    assert core.reaches_symbols("def run(self):\n    return 1\n", "run", {"sync_op"}) is False
    assert core.reaches_symbols("def run(:::\n", "run", {"sync_op"}) is True  # 解析失败→fail-closed


def test_reaches_symbols_class_name_disambiguates():
    """class_name 参数确保同模块多个同名 execute 方法按类精确定位（Codex 最终审 #1 oracle 修正）。"""
    src = (
        "class Bad:\n"
        "    def execute(self):\n"
        "        return sync_op()\n"
        "class Good:\n"
        "    def execute(self):\n"
        "        return 1\n"
    )
    # 无 class_name → 最后一个 execute 胜出（Bad.execute 被 Good.execute 覆盖 → False）
    assert core.reaches_symbols(src, "execute", {"sync_op"}) is False
    # class_name="Bad" → 精确定位到 Bad.execute → True
    assert core.reaches_symbols(src, "execute", {"sync_op"}, class_name="Bad") is True
    # class_name="Good" → 精确定位到 Good.execute → False
    assert core.reaches_symbols(src, "execute", {"sync_op"}, class_name="Good") is False


def test_fetch_blocking_returns_ok_immediately():
    calls = {"n": 0}
    def fetch():
        calls["n"] += 1
        return {"gating_enabled": True}
    status, payload = core.fetch_gating_blocking(fetch, timeout_s=10, clock=lambda: 0.0, sleep=lambda s: None)
    assert status == "ok" and payload == {"gating_enabled": True} and calls["n"] == 1


def test_fetch_blocking_times_out():
    t = {"v": 0.0}
    def clock():
        return t["v"]
    def sleep(s):
        t["v"] += s
    def fetch():
        raise OSError("connection refused")
    status, payload = core.fetch_gating_blocking(fetch, timeout_s=6, clock=clock, sleep=sleep)
    assert status == "timeout" and payload is None


def test_parse_timeout_valid():
    assert core.parse_timeout("60") == 60.0
    assert core.parse_timeout("0") == 0.0


def test_parse_timeout_invalid_falls_back():
    assert core.parse_timeout("abc") == 180.0
    assert core.parse_timeout(None) == 180.0
    assert core.parse_timeout("") == 180.0


def test_parse_timeout_non_finite_and_negative():
    """非有限/负值回退默认（Codex 收尾审 #3）。"""
    assert core.parse_timeout("nan") == 180.0
    assert core.parse_timeout("inf") == 180.0
    assert core.parse_timeout("-5") == 180.0
    assert core.parse_timeout("  90  ") == 90.0


def test_classify_payload():
    """payload 分类器：畸形→fail_closed，显式 False→skip，True→prune（Codex 收尾审 #1）。"""
    assert core.classify_payload("timeout", None) == "fail_closed"
    assert core.classify_payload("ok", []) == "fail_closed"
    assert core.classify_payload("ok", "x") == "fail_closed"
    assert core.classify_payload("ok", {}) == "fail_closed"                    # 缺 gating_enabled
    assert core.classify_payload("ok", {"gating_enabled": "yes"}) == "fail_closed"  # 非 bool
    assert core.classify_payload("ok", {"gating_enabled": False}) == "skip"
    assert core.classify_payload("ok", {"gating_enabled": True}) == "prune"


def test_reaches_symbols_async_entry():
    """async def execute 应被 oracle 识别（Codex 收尾审 #2）。"""
    src = "async def execute(self, x):\n    return await sync_op(x)\n"
    assert core.reaches_symbols(src, "execute", {"sync_op"}) is True
    src2 = "async def execute(self, x):\n    return x + 1\n"
    assert core.reaches_symbols(src2, "execute", {"sync_op"}) is False


def test_reaches_symbols_async_indirect_helper():
    """async helper 调用链同样可跟踪（Codex 收尾审 #2）。"""
    src = ("async def _do(x):\n    return await sync_op(x)\n"
           "async def execute(self, x):\n    return await _do(x)\n")
    assert core.reaches_symbols(src, "execute", {"sync_op"}) is True


def test_fetch_blocking_recovers_after_retries():
    t = {"v": 0.0}
    attempts = {"n": 0}
    def fetch():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("not ready")
        return {"gating_enabled": True}
    status, payload = core.fetch_gating_blocking(
        fetch, timeout_s=60, clock=lambda: t["v"], sleep=lambda s: t.__setitem__("v", t["v"] + s))
    assert status == "ok" and attempts["n"] == 3
