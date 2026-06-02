"""Env-backed config. secrets only in .env (not in git)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env unless explicitly disabled (tests set BRIDGE_SKIP_DOTENV=1 via conftest
# to prevent the developer's real .env from leaking into respx-mocked tests).
if not os.getenv("BRIDGE_SKIP_DOTENV"):
    load_dotenv()


class MissingConfig(Exception):
    """Raised when a required provider key/config is absent. Mapped to HTTP 424 by errors.py."""


_PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "tripo": "TRIPO_API_KEY",
    # ByteDance/Seedance: the adapter registers three route vendor segments
    # (byteplus / byteplus-seedance2 / seedance) but they all share one gateway
    # base/key — resolved under the single provider name "byteplus".
    "byteplus": "BYTEPLUS_API_KEY",
}
_PROVIDER_BASE = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "gemini": "GEMINI_BASE_URL",
    "tripo": "TRIPO_BASE_URL",
    "byteplus": "BYTEPLUS_BASE_URL",
}
_PROVIDER_DEFAULT_BASE = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "tripo": "https://api.tripo3d.ai",
    "byteplus": "https://ai.leihuo.netease.com",
}

# ── Gating defaults (policy baseline; override per-deployment via .env) ──
# Vendor allowlist: vendors the bridge has adapters for; nodes from any other vendor are
# hidden from the menu. Override with BRIDGE_ALLOWED_VENDORS (comma-separated) in .env —
# keeps node enable/disable out of code (no git-pull conflicts).
DEFAULT_ALLOWED_VENDORS = ["openai", "anthropic", "gemini", "tripo", "bytedance"]
# Per-class hard hide (denylist): classes of an ALLOWED vendor to remove from the menu
# entirely. Use for nodes the gateway can't serve at all (e.g. dall-e on a gpt-image-only
# gateway) or simply unwanted nodes. Empty by default; set BRIDGE_HIDDEN_NODE_CLASSES in
# .env. (There is no per-class allowlist / "未适配" grey state: a node is either shown or
# hidden — capability-unsupported classes are hidden automatically by the gating node.)
DEFAULT_HIDDEN_NODE_CLASSES: list[str] = []


def _csv_env(name: str, default: list[str]) -> list[str]:
    """Parse a comma-separated env override; fall back to default when unset/empty.
    Override semantics (replace, not append): the .env value, when present, is the
    full source of truth for that list."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]


@dataclass
class Config:
    host: str
    port: int
    asset_dir: str
    gating_enabled: bool
    cors_origins: list[str]
    anthropic_version: str
    allowed_vendors: list[str]
    hidden_node_classes: list[str]

    def require_key(self, provider: str) -> str:
        if provider not in _PROVIDER_KEYS:
            raise MissingConfig(f"comfy-bridge: unknown provider {provider!r}")
        env_name = _PROVIDER_KEYS[provider]
        val = os.getenv(env_name, "").strip()
        if not val:
            raise MissingConfig(f"comfy-bridge: {provider} {env_name} 未配置")
        return val

    def base_url(self, provider: str) -> str:
        """Returns configured origin-root base, else official default. No trailing slash."""
        if provider not in _PROVIDER_BASE:
            raise MissingConfig(f"comfy-bridge: unknown provider {provider!r}")
        configured = os.getenv(_PROVIDER_BASE[provider], "").strip()
        base = configured or _PROVIDER_DEFAULT_BASE[provider]
        return base.rstrip("/")


def _default_asset_dir() -> str:
    """Default asset cache: <cwd>/asset-cache. CWD is well-defined under systemd
    (WorkingDirectory=) and Windows launcher (Set-Location). Portable across platforms
    via os.path.join; user can override via BRIDGE_ASSET_DIR for non-standard layouts."""
    return os.path.join(os.getcwd(), "asset-cache")


def load_config() -> Config:
    origins = [o.strip() for o in os.getenv("BRIDGE_CORS_ORIGINS", "http://127.0.0.1:8188,http://localhost:8188").split(",") if o.strip()]
    asset_dir = os.getenv("BRIDGE_ASSET_DIR", "").strip() or _default_asset_dir()
    return Config(
        host=os.getenv("BRIDGE_HOST", "127.0.0.1"),
        port=int(os.getenv("BRIDGE_PORT", "8190")),
        asset_dir=asset_dir,
        gating_enabled=os.getenv("BRIDGE_GATING", "on").strip().lower() != "off",
        cors_origins=origins,
        anthropic_version=os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
        allowed_vendors=_csv_env("BRIDGE_ALLOWED_VENDORS", DEFAULT_ALLOWED_VENDORS),
        hidden_node_classes=_csv_env("BRIDGE_HIDDEN_NODE_CLASSES", DEFAULT_HIDDEN_NODE_CLASSES),
    )
