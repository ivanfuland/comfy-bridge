"""Test isolation: prevent a developer's .env from leaking into tests.

config.py calls load_dotenv() at module load, which pulls real keys/base URLs
from the local .env when present. That makes respx-mocked adapter tests hit
the real upstream (or rather, the configured base URL) instead of the mock
target, breaking parity. This conftest:

1) Tells config.py to skip load_dotenv (BRIDGE_SKIP_DOTENV=1).
2) Clears any pre-existing bridge env vars so each test starts from a clean
   slate and opts in via its own monkeypatch.setenv.

Tests that need specific env values still set them via monkeypatch.setenv as
before; this fixture only removes the .env leakage path.
"""
import pytest


_BRIDGE_VARS = [
    "BRIDGE_HOST", "BRIDGE_PORT", "BRIDGE_ASSET_DIR", "BRIDGE_GATING",
    "BRIDGE_CORS_ORIGINS", "BRIDGE_CAPTURE", "BRIDGE_CAPTURE_DIR",
    "OPENAI_BASE_URL", "OPENAI_API_KEY",
    "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_VERSION",
    "GEMINI_BASE_URL", "GEMINI_API_KEY",
    "TRIPO_BASE_URL", "TRIPO_API_KEY",
    "BYTEPLUS_BASE_URL", "BYTEPLUS_API_KEY",
]


@pytest.fixture(autouse=True)
def _isolate_bridge_env(monkeypatch):
    monkeypatch.setenv("BRIDGE_SKIP_DOTENV", "1")
    for v in _BRIDGE_VARS:
        monkeypatch.delenv(v, raising=False)
