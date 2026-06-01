"""Gating endpoint (spec §6). Service-level vendor allowlist + class denylist:
- allowed_vendors: vendors the bridge has adapters for. Nodes from other vendors are
  hidden entirely from the menu (~173 nodes out of 192 in upstream comfy_api_nodes).
- hidden_node_classes: per-class denylist — specific classes of an allowed vendor to
  hide outright (e.g. dall-e on a gpt-image-only gateway, or unwanted nodes). There is
  no per-class allowlist / "未适配" grey state: a node is either shown or hidden.

Both lists are POLICY/CONFIG, not code: the baseline defaults ship in config.py
(DEFAULT_ALLOWED_VENDORS / DEFAULT_HIDDEN_NODE_CLASSES) and each deployment overrides
them via .env (BRIDGE_ALLOWED_VENDORS / BRIDGE_HIDDEN_NODE_CLASSES, comma-separated) —
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
        # vendor allowlist + class denylist (no per-class allowlist / grey state)
        "gating_enabled": cfg.gating_enabled,
        "allowed_vendors": cfg.allowed_vendors,
        "hidden_node_classes": cfg.hidden_node_classes,
        # backend capability authority (client hides classes not loaded)
        "loaded_route_keys": loaded_route_keys,
        "vendor_meta": vendor_meta,
        "loaded_node_classes": sorted(loaded_node_classes),
    }
