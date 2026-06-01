"""Adapter registry + env-driven multi-backend dispatcher (spec §4).

_REGISTRY:                既有「route_key → adapter instance」运行时表
_LOADED:                  既有 idempotency 守卫
_LOADED_BACKEND_CHOICES:  新增「logical vendor → 实际加载的 backend name」运行时表
_BACKEND_REGISTRY:        新增「logical vendor → VendorSpec」配置表（见 §4.1）"""
import importlib
import logging
import os
from typing import TypedDict

_log = logging.getLogger("comfy-bridge.adapters")

_REGISTRY: dict[str, object] = {}
_LOADED = False
_LOADED_BACKEND_CHOICES: dict[str, str] = {}


def _missing_is_ancestor_or_self(missing: str | None, target: str) -> bool:
    """True when `missing` (from ModuleNotFoundError.name) is either the target
    module itself or any ancestor package on the path to it. Used by
    load_adapters() to distinguish 'target/ancestor absent' (graceful when
    required=False) from 'target loaded but its internals reference something
    missing' (real bug — re-raise regardless of required).

    Examples for target='app.adapters.fal_ai.bytedance':
      missing='app.adapters.fal_ai.bytedance' (leaf)        → True
      missing='app.adapters.fal_ai'           (parent)      → True
      missing='app.adaptrs.base'              (internal)    → False
    """
    return missing is not None and (
        missing == target or target.startswith(missing + ".")
    )


def register(name: str, adapter) -> None:
    _REGISTRY[name] = adapter


def get_adapter(name: str):
    return _REGISTRY.get(name)


class BackendSpec(TypedDict):
    module: str
    required: bool
    supported_node_classes: list[str]


class VendorSpec(TypedDict):
    python_module_segment: str
    expected_route_keys: list[str]
    default_backend: str
    backends: dict[str, BackendSpec]


_NATIVE_OPENAI_NODES = [
    "OpenAIChatNode", "OpenAIGPTImage1", "OpenAIGPTImageNodeV2",
    "OpenAIDalle2", "OpenAIDalle3",
]
_NATIVE_ANTHROPIC_NODES = ["ClaudeNode"]
_NATIVE_GEMINI_NODES = [
    "GeminiNode", "GeminiImageNode", "GeminiImage2Node",
    "GeminiNanoBanana2", "GeminiNanoBanana2V2",
]
# Full native capability = every api_node=True class of vendor `tripo`
# (comfy_api_nodes.nodes_tripo). Must be the backend's full served set, NOT the
# e2e-verified subset (that subset lives in config.DEFAULT_ALLOWED_NODE_CLASSES
# and only controls the grey "[未适配]" layer). Keeping it the full set makes the
# JS capability layer a no-op under the default (native) backend — preserving the
# existing grey-not-hide behavior for unverified Tripo nodes (zero regression).
# A narrower backend (e.g. fal-ai) declares its own smaller set to trigger hiding.
_NATIVE_TRIPO_NODES = [
    "TripoTextToModelNode", "TripoImageToModelNode", "TripoMultiviewToModelNode",
    "TripoTextureNode", "TripoRefineNode", "TripoRigNode",
    "TripoRetargetNode", "TripoConversionNode",
]
_NATIVE_BYTEPLUS_NODES = [
    "ByteDanceImageNode", "ByteDanceSeedreamNode", "ByteDanceSeedreamNodeV2",
    "ByteDanceTextToVideoNode", "ByteDanceImageToVideoNode",
    "ByteDanceFirstLastFrameNode", "ByteDanceImageReferenceNode",
    "ByteDance2TextToVideoNode", "ByteDance2FirstLastFrameNode", "ByteDance2ReferenceNode",
    "ByteDanceCreateImageAsset", "ByteDanceCreateVideoAsset",
]

_BACKEND_REGISTRY: dict[str, VendorSpec] = {
    "openai": {
        "python_module_segment": "openai",
        "expected_route_keys": ["openai"],
        "default_backend": "native",
        "backends": {
            "native": {
                "module": "app.adapters.openai",
                "required": True,
                "supported_node_classes": _NATIVE_OPENAI_NODES,
            },
        },
    },
    "anthropic": {
        "python_module_segment": "anthropic",
        "expected_route_keys": ["anthropic"],
        "default_backend": "native",
        "backends": {
            "native": {
                "module": "app.adapters.anthropic",
                "required": True,
                "supported_node_classes": _NATIVE_ANTHROPIC_NODES,
            },
        },
    },
    "gemini": {
        "python_module_segment": "gemini",
        "expected_route_keys": ["vertexai"],
        "default_backend": "native",
        "backends": {
            "native": {
                "module": "app.adapters.gemini",
                "required": True,
                "supported_node_classes": _NATIVE_GEMINI_NODES,
            },
        },
    },
    "tripo": {
        "python_module_segment": "tripo",
        "expected_route_keys": ["tripo"],
        "default_backend": "native",
        "backends": {
            "native": {
                "module": "app.adapters.tripo",
                "required": True,
                "supported_node_classes": _NATIVE_TRIPO_NODES,
            },
        },
    },
    "byteplus": {
        "python_module_segment": "bytedance",
        "expected_route_keys": ["byteplus", "byteplus-seedance2", "seedance"],
        "default_backend": "native",
        "backends": {
            "native": {
                "module": "app.adapters.byteplus",
                "required": True,
                "supported_node_classes": _NATIVE_BYTEPLUS_NODES,
            },
            "fal-ai": {
                "module": "app.adapters.fal_ai.bytedance",
                "required": False,
                "supported_node_classes": [
                    "ByteDance2TextToVideoNode",
                    "ByteDance2FirstLastFrameNode",
                    "ByteDance2ReferenceNode",
                    "ByteDanceSeedreamNode",
                    "ByteDanceSeedreamNodeV2",
                ],
            },
        },
    },
}


def load_adapters() -> None:
    """Import each logical vendor's selected backend module so its register()
    runs. Idempotent. Backend chosen per vendor by env `{VENDOR}_BACKEND`
    (fallback to VendorSpec.default_backend, typically 'native').

    Behavior matrix (spec §4.2):
      env value matches a declared backend     → load it
      env value NOT in backends dict           → warn log + skip this vendor
      module/ancestor missing + required=False → info log + skip
      module/ancestor missing + required=True  → raise (hard fail)
      module loads but internal bug            → raise (regardless of required)
      hard fail at any vendor                  → clear _REGISTRY + re-raise"""
    global _LOADED
    if _LOADED:
        return
    try:
        for vendor, vspec in _BACKEND_REGISTRY.items():
            default = vspec["default_backend"]
            # Empty/whitespace-only env value falls back to default (not treated
            # as an unknown backend that would silently skip the vendor). codex P2-2.
            choice = (os.getenv(f"{vendor.upper()}_BACKEND") or "").strip().lower() or default
            backend_spec = vspec["backends"].get(choice)
            if backend_spec is None:
                _log.warning(
                    "vendor %r: backend %r not declared in _BACKEND_REGISTRY "
                    "(available: %s) — skipping",
                    vendor, choice, sorted(vspec["backends"].keys()) or "[]",
                )
                continue
            module_path = backend_spec["module"]
            try:
                importlib.import_module(module_path)
            except ModuleNotFoundError as e:
                ancestor_missing = _missing_is_ancestor_or_self(e.name, module_path)
                if ancestor_missing and not backend_spec["required"]:
                    _log.info(
                        "vendor %r: optional backend %r module %s not importable (%s) — skipping",
                        vendor, choice, module_path, e.name,
                    )
                    continue
                raise
            _LOADED_BACKEND_CHOICES[vendor] = choice
    except Exception:
        # Hard fail before all vendors loaded → clear _REGISTRY so it doesn't
        # carry half-populated state (codex v4 P1).
        _REGISTRY.clear()
        _LOADED_BACKEND_CHOICES.clear()
        raise
    _LOADED = True
