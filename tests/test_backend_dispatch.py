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
