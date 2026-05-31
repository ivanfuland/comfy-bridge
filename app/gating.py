"""Gating endpoint (spec §6). Two-tier service-level allowlist:
- allowed_vendors: vendors the bridge has adapters for. Nodes from other vendors get
  hidden entirely from the menu (~173 nodes out of 192 in upstream comfy_api_nodes).
- allowed_node_classes: per-class allowlist for end-to-end-verified node classes within
  an allowed vendor. Nodes whose vendor is allowed but class isn't get greyed "未适配".

Both lists are POLICY/CONFIG, not code: the baseline defaults ship in config.py
(DEFAULT_ALLOWED_VENDORS / DEFAULT_ALLOWED_NODE_CLASSES) and each deployment overrides
them via .env (BRIDGE_ALLOWED_VENDORS / BRIDGE_ALLOWED_NODE_CLASSES, comma-separated) —
so enabling/disabling nodes never requires editing this file (and never conflicts on
`git pull`). Vendor is derived client-side from each node's python_module
(e.g. `comfy_api_nodes.nodes_openai` -> vendor "openai")."""
from fastapi import APIRouter

from app.config import load_config

gating_router = APIRouter()


@gating_router.get("/comfy-bridge/gating")
async def gating() -> dict:
    cfg = load_config()
    from app.adapters import _REGISTRY, _BACKEND_REGISTRY, _LOADED_BACKEND_CHOICES

    loaded_route_keys = sorted(_REGISTRY.keys())

    vendor_meta = {
        vendor: {
            "python_module_segment": vspec["python_module_segment"],
            "expected_route_keys": vspec["expected_route_keys"],
        }
        for vendor, vspec in _BACKEND_REGISTRY.items()
    }

    loaded_node_classes: set[str] = set()
    for vendor, choice in _LOADED_BACKEND_CHOICES.items():
        vspec = _BACKEND_REGISTRY.get(vendor)
        if vspec is None:
            continue
        backend_spec = vspec["backends"].get(choice)
        if backend_spec is not None:
            loaded_node_classes.update(backend_spec["supported_node_classes"])

    return {
        # 既有 4 字段不动
        "gating_enabled": cfg.gating_enabled,
        "allowed_vendors": cfg.allowed_vendors,
        "allowed_node_classes": cfg.allowed_node_classes,
        "hidden_node_classes": cfg.hidden_node_classes,
        # 新增 3 字段
        "loaded_route_keys": loaded_route_keys,
        "vendor_meta": vendor_meta,
        "loaded_node_classes": sorted(loaded_node_classes),
    }
