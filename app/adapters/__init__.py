"""Adapter registry. Real adapters registered in Task 5-8 via load_adapters()."""
from typing import Optional
import importlib

_REGISTRY: dict[str, object] = {}
_LOADED = False


def register(name: str, adapter) -> None:
    _REGISTRY[name] = adapter


def get_adapter(name: str):
    return _REGISTRY.get(name)


def load_adapters() -> None:
    """Import each provider adapter module so its register() runs. Idempotent."""
    global _LOADED
    if _LOADED:
        return
    for name in ("openai", "anthropic", "gemini", "tripo"):
        try:
            importlib.import_module(f"app.adapters.{name}")
        except ModuleNotFoundError as e:
            # Only swallow when the adapter module itself doesn't exist (allows progressive build).
            # If a typo'd internal import (e.g. `from app.adaptrs.base import ...`) raises
            # ModuleNotFoundError, let it bubble — silently swallowing it would hide the bug as
            # an opaque 424 "adapter not registered" at request time.
            if e.name == f"app.adapters.{name}":
                continue
            raise
    _LOADED = True
