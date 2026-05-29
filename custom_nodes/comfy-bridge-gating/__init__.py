"""comfy-bridge gating extension. Two interventions:

1) Web extension (web/comfy-bridge-gating.js) -- class-tier grey [未适配] for
   api_nodes whose vendor is allowed but class isn't on the per-class allowlist.

2) Python side (this file) -- at custom_nodes load time, remove disallowed-
   vendor api_node classes from nodes.NODE_CLASS_MAPPINGS so the new Vue
   "合作伙伴节点" panel (which reads /object_info) and the LiteGraph registry
   both lose them in one shot. Loading order is fine: init_builtin_api_nodes()
   has already populated NODE_CLASS_MAPPINGS by the time custom_nodes load
   (see ComfyUI/nodes.py:init_extra_nodes).

Single source of truth for the allowlist: http://127.0.0.1:8190/comfy-bridge/gating
(returns {gating_enabled, allowed_vendors, allowed_node_classes}). On bridge
unreachable: fail-open -- no pruning (don't lock the user out)."""
import json
import logging
import time
import urllib.request

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_BRIDGE_GATING_URL = "http://127.0.0.1:8190/comfy-bridge/gating"
_log = logging.getLogger("comfy-bridge-gating")


def _fetch_gating(retries=8, delay=2.0):
    """Fetch the gating allowlist, retrying while the bridge comes up.

    ComfyUI is usually launched after the bridge (Task Scheduler @logon), but there's a
    startup race: if ComfyUI loads custom_nodes before the bridge's uvicorn is ready, a
    single 2s probe fails -> fail-open -> the menu shows ALL ~192 api_nodes for the whole
    session. Retrying (~8 x 2s ≈ 16s) lets ComfyUI wait out a slow/just-starting bridge.
    Note: this only affects menu gating; credit protection comes from --comfy-api-base
    (request routing), so fail-open never leaks to comfy.org billing."""
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_BRIDGE_GATING_URL, timeout=2) as r:
                if attempt:
                    _log.info(f"gating reachable after {attempt} retr{'y' if attempt == 1 else 'ies'}")
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)
    _log.warning(f"gating fetch failed after {retries} attempts - fail-open: {last_err}")
    return None


def _vendor_from_module(mod):
    if isinstance(mod, str) and mod.startswith("comfy_api_nodes.nodes_"):
        return mod[len("comfy_api_nodes.nodes_"):].lower()
    return None


def _prune_disallowed_api_nodes():
    gating = _fetch_gating()
    if not gating or not gating.get("gating_enabled"):
        _log.info("gating disabled or unreachable - no pruning")
        return
    allowed_vendors = set(gating.get("allowed_vendors", []))
    if not allowed_vendors:
        _log.warning("allowed_vendors is empty - skipping prune to avoid lockout")
        return
    # Per-class hard-hide denylist: classes of an ALLOWED vendor that the gateway can't
    # serve (e.g. dall-e on a gpt-image-only gateway). Pruned here server-side so the Vue
    # panel (reads /object_info) loses them too -- the web JS hideClass only touches the
    # LiteGraph registry, which the new node-library panel does NOT read from.
    hidden_classes = set(gating.get("hidden_node_classes", []))
    try:
        import nodes
    except Exception as e:
        _log.warning(f"cannot import nodes module: {e}")
        return
    removed = 0
    removed_hidden = 0
    kept_vendors = set()
    for name in list(nodes.NODE_CLASS_MAPPINGS.keys()):
        cls = nodes.NODE_CLASS_MAPPINGS[name]
        if not getattr(cls, "API_NODE", False):
            continue
        # use RELATIVE_PYTHON_MODULE (set by nodes.py:2245 + 2273), NOT __module__:
        # server.py:721 reads exactly this attr to populate /object_info["python_module"].
        vendor = _vendor_from_module(getattr(cls, "RELATIVE_PYTHON_MODULE", None))
        if name in hidden_classes:
            del nodes.NODE_CLASS_MAPPINGS[name]
            nodes.NODE_DISPLAY_NAME_MAPPINGS.pop(name, None)
            removed_hidden += 1
            continue
        if vendor in allowed_vendors:
            kept_vendors.add(vendor)
            continue
        del nodes.NODE_CLASS_MAPPINGS[name]
        nodes.NODE_DISPLAY_NAME_MAPPINGS.pop(name, None)
        removed += 1
    _log.info(
        f"pruned {removed} api_node classes from disallowed vendors "
        f"+ {removed_hidden} hard-hidden classes (kept vendors: {sorted(kept_vendors)})"
    )


_prune_disallowed_api_nodes()
