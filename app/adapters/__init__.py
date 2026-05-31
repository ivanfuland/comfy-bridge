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
_NATIVE_TRIPO_NODES = ["TripoImageToModelNode", "TripoMultiviewToModelNode"]
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
        },
    },
}


# Loader placeholder — Task 5 重写为真正实现
def load_adapters() -> None:
    """PLACEHOLDER: Task 5 will rewrite as env-driven dispatcher."""
    global _LOADED
    if _LOADED:
        return
    for name in ("openai", "anthropic", "gemini", "tripo", "byteplus"):
        try:
            importlib.import_module(f"app.adapters.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"app.adapters.{name}":
                continue
            raise
    _LOADED = True
