"""Tests for backend dispatcher refactor (spec §8)."""
import sys
import pytest


@pytest.fixture(autouse=True)
def _reset_adapter_state():
    """Reset adapter registry + module cache between tests so adapter
    modules re-execute their top-level register() calls on next import."""
    from app import adapters as adapters_mod
    adapters_mod._REGISTRY.clear()
    adapters_mod._LOADED = False
    if hasattr(adapters_mod, "_LOADED_BACKEND_CHOICES"):
        adapters_mod._LOADED_BACKEND_CHOICES.clear()
    for name in list(sys.modules):
        if name.startswith("app.adapters.") and name != "app.adapters.base":
            del sys.modules[name]
    yield


def test_backend_registry_schema_consistency():
    """spec §8 #12: schema sanity + global route key uniqueness.

    Each vendor must have:
      - non-empty python_module_segment, default_backend, expected_route_keys
      - default_backend exists in backends + required=True (codex v6 P1-1)
      - each backend: non-empty module, required is bool, non-empty supported_node_classes
    Global: ⋃ expected_route_keys is unique (codex v6 P1-2)."""
    from app.adapters import _BACKEND_REGISTRY

    all_route_keys: list[str] = []
    for vendor, vspec in _BACKEND_REGISTRY.items():
        assert isinstance(vspec["python_module_segment"], str) and vspec["python_module_segment"], \
            f"vendor {vendor}: python_module_segment must be non-empty"
        assert isinstance(vspec["default_backend"], str) and vspec["default_backend"], \
            f"vendor {vendor}: default_backend must be non-empty"
        assert isinstance(vspec["expected_route_keys"], list) and vspec["expected_route_keys"], \
            f"vendor {vendor}: expected_route_keys must be non-empty list"
        assert isinstance(vspec["backends"], dict) and vspec["backends"], \
            f"vendor {vendor}: backends must be non-empty dict"
        default = vspec["default_backend"]
        assert default in vspec["backends"], \
            f"vendor {vendor}: default_backend {default!r} not in backends"
        assert vspec["backends"][default]["required"] is True, \
            f"vendor {vendor}: default backend {default!r} must have required=True"
        for backend_name, backend_spec in vspec["backends"].items():
            assert isinstance(backend_spec["module"], str) and backend_spec["module"]
            assert isinstance(backend_spec["required"], bool)
            assert isinstance(backend_spec["supported_node_classes"], list) \
                and backend_spec["supported_node_classes"]
        all_route_keys.extend(vspec["expected_route_keys"])

    assert len(all_route_keys) == len(set(all_route_keys)), \
        f"Cross-vendor route key collision: {all_route_keys}"


@pytest.mark.parametrize("missing,target,expected", [
    # 自身缺失
    ("app.adapters.fal_ai.bytedance", "app.adapters.fal_ai.bytedance", True),
    # 父包缺失（codex v2 P1-1 核心场景）
    ("app.adapters.fal_ai", "app.adapters.fal_ai.bytedance", True),
    # 更高祖先
    ("app.adapters", "app.adapters.fal_ai.bytedance", True),
    ("app", "app.adapters.fal_ai.bytedance", True),
    # 内部 typo（非祖先）→ 不命中
    ("app.adaptrs.base", "app.adapters.fal_ai.bytedance", False),
    # 命名相似但不真是祖先
    ("app.adapters_v2", "app.adapters.fal_ai.bytedance", False),
    # None
    (None, "app.adapters.fal_ai.bytedance", False),
])
def test_missing_is_ancestor_or_self(missing, target, expected):
    """spec §4.2 helper: distinguishes target/ancestor missing from internal bug."""
    from app.adapters import _missing_is_ancestor_or_self
    assert _missing_is_ancestor_or_self(missing, target) is expected


def test_default_behavior_no_env(monkeypatch):
    """spec §8 #1: env 全不设 → 5 个 native 注册，_REGISTRY 含 7 个 route keys."""
    from app.adapters import load_adapters, _REGISTRY, _LOADED_BACKEND_CHOICES
    load_adapters()
    assert set(_REGISTRY.keys()) == {
        "anthropic", "byteplus", "byteplus-seedance2",
        "openai", "seedance", "tripo", "vertexai",
    }
    assert _LOADED_BACKEND_CHOICES == {
        "openai": "native", "anthropic": "native", "gemini": "native",
        "tripo": "native", "byteplus": "native",
    }


def test_explicit_native(monkeypatch):
    """spec §8 #2: 显式 BYTEPLUS_BACKEND=native → 跟默认一致."""
    monkeypatch.setenv("BYTEPLUS_BACKEND", "native")
    from app.adapters import load_adapters, _REGISTRY
    load_adapters()
    assert {"byteplus", "byteplus-seedance2", "seedance"} <= set(_REGISTRY.keys())


def test_unknown_backend_warns_and_skips(monkeypatch, caplog):
    """spec §8 #3: BYTEPLUS_BACKEND=fal-ai 但表里只有 native → warn + skip."""
    import logging
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    with caplog.at_level(logging.WARNING, logger="comfy-bridge.adapters"):
        from app.adapters import load_adapters, _REGISTRY
        load_adapters()
    assert not any(k in _REGISTRY for k in ("byteplus", "byteplus-seedance2", "seedance"))
    assert {"openai", "anthropic", "vertexai", "tripo"} <= set(_REGISTRY.keys())
    assert any("byteplus" in r.message and "fal-ai" in r.message for r in caplog.records)


def test_case_insensitive_backend_value(monkeypatch):
    """spec §8 #4: BYTEPLUS_BACKEND=Native → 走 native."""
    monkeypatch.setenv("BYTEPLUS_BACKEND", "Native")
    from app.adapters import load_adapters, _REGISTRY
    load_adapters()
    assert "byteplus" in _REGISTRY


def test_leaf_module_missing_required_false(monkeypatch):
    """spec §8 #5: leaf module 不存在 + required=False → info log + skip."""
    from app import adapters as adapters_mod
    fake_registry = dict(adapters_mod._BACKEND_REGISTRY)
    fake_byteplus = dict(fake_registry["byteplus"])
    fake_byteplus["backends"] = {
        **fake_byteplus["backends"],
        "fal-ai": {
            "module": "app.adapters.byteplus_does_not_exist",
            "required": False,
            "supported_node_classes": ["FakeNode"],
        },
    }
    fake_registry["byteplus"] = fake_byteplus
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", fake_registry)
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    adapters_mod.load_adapters()  # 不抛
    assert "byteplus" not in adapters_mod._REGISTRY


def test_parent_package_missing_required_false(monkeypatch):
    """spec §8 #6 (codex v2 P1-1 核心): parent package 不存在 + required=False → skip."""
    from app import adapters as adapters_mod
    fake_registry = dict(adapters_mod._BACKEND_REGISTRY)
    fake_byteplus = dict(fake_registry["byteplus"])
    fake_byteplus["backends"] = {
        **fake_byteplus["backends"],
        "fal-ai": {
            "module": "app.adapters.fal_ai.bytedance",  # fal_ai/ 包根本不存在
            "required": False,
            "supported_node_classes": ["FakeNode"],
        },
    }
    fake_registry["byteplus"] = fake_byteplus
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", fake_registry)
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    adapters_mod.load_adapters()  # 不抛
    assert "byteplus" not in adapters_mod._REGISTRY


def test_module_missing_required_true_hard_fails(monkeypatch):
    """spec §8 #7 (codex v3 P1-3 核心): required=True + module 缺失 → 真抛."""
    from app import adapters as adapters_mod
    fake_registry = dict(adapters_mod._BACKEND_REGISTRY)
    fake_byteplus = dict(fake_registry["byteplus"])
    fake_byteplus["backends"] = {
        "native": {
            "module": "app.adapters.does_not_exist",
            "required": True,
            "supported_node_classes": ["FakeNode"],
        },
    }
    fake_registry["byteplus"] = fake_byteplus
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", fake_registry)
    with pytest.raises(ModuleNotFoundError):
        adapters_mod.load_adapters()


def test_internal_typo_raises_regardless_of_required(monkeypatch, tmp_path):
    """spec §8 #8: module 存在但内部 from app.adaptrs.base 拼错 → 真抛."""
    fake_dir = tmp_path / "fake_pkg"
    fake_dir.mkdir()
    (fake_dir / "__init__.py").write_text("")
    (fake_dir / "fake_typo_adapter.py").write_text(
        "from app.adaptrs.base import nothing\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from app import adapters as adapters_mod
    fake_registry = dict(adapters_mod._BACKEND_REGISTRY)
    fake_byteplus = dict(fake_registry["byteplus"])
    fake_byteplus["backends"] = {
        "native": {
            "module": "fake_pkg.fake_typo_adapter",
            "required": False,
            "supported_node_classes": ["FakeNode"],
        },
    }
    fake_registry["byteplus"] = fake_byteplus
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", fake_registry)
    with pytest.raises(ModuleNotFoundError, match="app.adaptrs"):
        adapters_mod.load_adapters()


def test_idempotent(monkeypatch):
    """spec §8 #9: 连续调两次，第二次直接 return（_LOADED=True 守卫）."""
    from app.adapters import load_adapters, _REGISTRY
    load_adapters()
    snapshot = dict(_REGISTRY)
    load_adapters()
    assert dict(_REGISTRY) == snapshot


def test_hard_fail_rolls_back_registry(monkeypatch):
    """spec §8 #13 (codex v4 P1): hard fail 时 _REGISTRY 必须被清空."""
    from app import adapters as adapters_mod
    fake_registry = {
        "openai": adapters_mod._BACKEND_REGISTRY["openai"],
        "anthropic": adapters_mod._BACKEND_REGISTRY["anthropic"],
        "byteplus": {
            "python_module_segment": "bytedance",
            "expected_route_keys": ["byteplus"],
            "default_backend": "native",
            "backends": {
                "native": {
                    "module": "app.adapters.does_not_exist_for_rollback_test",
                    "required": True,
                    "supported_node_classes": ["FakeNode"],
                },
            },
        },
    }
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", fake_registry)
    with pytest.raises(ModuleNotFoundError):
        adapters_mod.load_adapters()
    assert adapters_mod._REGISTRY == {}
    assert adapters_mod._LOADED_BACKEND_CHOICES == {}


def _iter_vendor_backend_pairs():
    """Generate (vendor, backend_name) tuples from _BACKEND_REGISTRY for parametrize."""
    from app.adapters import _BACKEND_REGISTRY
    for vendor, vspec in _BACKEND_REGISTRY.items():
        for backend_name in vspec["backends"]:
            yield (vendor, backend_name)


@pytest.mark.parametrize("vendor,backend_name",
                         list(_iter_vendor_backend_pairs()),
                         ids=lambda x: x)
def test_expected_route_keys_contract(monkeypatch, vendor, backend_name):
    """spec §8 #11 (codex v3 P1-1, v4 P2): 每个声明的 backend 必须 register
    完整 expected_route_keys 集合。隔离单 (vendor, backend) 通过 monkeypatch
    整个 _BACKEND_REGISTRY 只剩一对。

    当前 5 vendor × 1 backend = 5 sub-test。M2 加 fal-ai 后参数化自动多
    sub-test 覆盖；漏 register 段则 fail，M2 PR 阻塞。"""
    from app import adapters as adapters_mod
    vspec = adapters_mod._BACKEND_REGISTRY[vendor]
    backend_spec = vspec["backends"][backend_name]
    isolated = {
        vendor: {
            **vspec,
            "backends": {backend_name: backend_spec},
            "default_backend": backend_name,
        },
    }
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", isolated)
    monkeypatch.setenv(f"{vendor.upper()}_BACKEND", backend_name)
    adapters_mod.load_adapters()
    assert set(adapters_mod._REGISTRY.keys()) == set(vspec["expected_route_keys"]), (
        f"vendor {vendor!r} backend {backend_name!r}: route keys mismatch")


def _make_app_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_gating_endpoint_reflects_loaded_vendors(monkeypatch):
    """spec §8 #10: 不存在的 backend → load_adapters skip → gating
    返回里 loaded_route_keys 不含该 vendor 的 expected_route_keys."""
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    from app.adapters import load_adapters
    load_adapters()
    client = _make_app_client()
    body = client.get("/comfy-bridge/gating").json()
    for rk in ("byteplus", "byteplus-seedance2", "seedance"):
        assert rk not in body["loaded_route_keys"]
    for rk in ("openai", "anthropic", "vertexai", "tripo"):
        assert rk in body["loaded_route_keys"]
    assert set(body["vendor_meta"].keys()) == {"openai", "anthropic", "gemini", "tripo", "byteplus"}
    assert body["vendor_meta"]["byteplus"]["python_module_segment"] == "bytedance"
    assert body["vendor_meta"]["gemini"]["expected_route_keys"] == ["vertexai"]


def test_gating_loaded_node_classes_reflects_capability(monkeypatch):
    """spec §8 #14 (codex v6 P1-3): fal-ai backend supported_node_classes
    不含 Seedance 1.x 4 节点 → loaded_node_classes 自动少这 4 个."""
    from app import adapters as adapters_mod
    fake_registry = dict(adapters_mod._BACKEND_REGISTRY)
    fake_byteplus = dict(fake_registry["byteplus"])
    fake_byteplus["backends"] = {
        "native": fake_byteplus["backends"]["native"],
        "fal-ai": {
            "module": "app.adapters.byteplus",  # fake 用现有 module 保证 register 成功
            "required": False,
            "supported_node_classes": [
                "ByteDance2TextToVideoNode", "ByteDanceSeedreamNodeV2",
            ],  # 故意只声明 fal-ai 真实支持的 2 个
        },
    }
    fake_registry["byteplus"] = fake_byteplus
    monkeypatch.setattr(adapters_mod, "_BACKEND_REGISTRY", fake_registry)
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    adapters_mod.load_adapters()
    body = _make_app_client().get("/comfy-bridge/gating").json()

    assert "ByteDance2TextToVideoNode" in body["loaded_node_classes"]
    assert "ByteDanceSeedreamNodeV2" in body["loaded_node_classes"]
    for n in ("ByteDanceTextToVideoNode", "ByteDanceImageToVideoNode",
              "ByteDanceFirstLastFrameNode", "ByteDanceImageReferenceNode"):
        assert n not in body["loaded_node_classes"], f"{n} should NOT be loaded under fal-ai"
    assert "OpenAIChatNode" in body["loaded_node_classes"]
    assert "ClaudeNode" in body["loaded_node_classes"]
