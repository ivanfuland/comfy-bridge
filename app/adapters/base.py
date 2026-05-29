"""Adapter protocol + asset-reference rewrite helpers (spec §5 field-path matrix).

Adapter contract: `async def handle(path, request, raw) -> fastapi.Response`.
Rewrite helpers locate bridge asset references (…/asset/{id}) and resolve them to bytes,
NEVER sending the 127.0.0.1 download_url to a vendor (spec §5 step 3)."""
import base64
from typing import Callable, Protocol
from urllib.parse import urlparse

from fastapi import Request, Response

from app import assets as assets_mod


class AssetNotFound(Exception):
    pass


class Adapter(Protocol):
    async def handle(self, path: str, request: Request, raw: bytes) -> Response: ...


def is_bridge_asset_url(url: str) -> bool:
    """True if url is a bridge-local download_url (…/asset/{id}). Uses urlparse for
    consistency with assets.lookup_by_url — checks hostname (not substring) and path."""
    if not isinstance(url, str):
        return False
    p = urlparse(url)
    return p.hostname in ("127.0.0.1", "localhost") and "/asset/" in p.path


def resolve_asset_bytes(url: str) -> tuple[bytes, str]:
    rec = assets_mod.lookup_by_url(url)
    if rec is None:
        raise AssetNotFound(f"comfy-bridge: asset not found for {url}")
    return rec.data, (rec.media_type or "image/png")


def resolve_asset_to_base64(url: str) -> tuple[str, str]:
    """Returns (base64_str, media_type)."""
    data, media_type = resolve_asset_bytes(url)
    return base64.b64encode(data).decode("ascii"), media_type


def rewrite_list(items: list, rewrite_one: Callable[[dict, str, str], dict]) -> list:
    """Rewrite a list of file references in place, preserving order. Empty {} elements
    (TripoFileEmptyReference) are kept as-is. rewrite_one(el, b64, media_type) -> new dict."""
    out = []
    for el in items:
        if not isinstance(el, dict) or not el or "url" not in el:
            out.append(el)
            continue
        url = el["url"]
        if is_bridge_asset_url(url):
            b64, mt = resolve_asset_to_base64(url)
            out.append(rewrite_one(el, b64, mt))
        else:
            out.append(el)
    return out


import logging
import os
import re

import httpx
from app import config as config_mod


_HTTP_CLIENT: httpx.AsyncClient | None = None
_io_log = logging.getLogger("comfy-bridge.io")

# Collapse long base64 / token runs so image blobs don't flood the log.
_B64_RUN = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")


def _summarize(data: bytes, limit: int = 1500) -> str:
    """Compact, log-safe view of a request/response body: decode, collapse base64
    blobs, single-line, truncate. Never includes headers (so no auth leak)."""
    if not data:
        return "(empty)"
    try:
        s = data.decode("utf-8", "replace")
    except Exception:
        return f"<{len(data)} bytes binary>"
    s = _B64_RUN.sub(lambda m: f"<{len(m.group(0))}B64>", s).replace("\n", " ")
    if len(s) > limit:
        s = s[:limit] + f"...(+{len(s) - limit} chars)"
    return s


async def _log_request(request: httpx.Request) -> None:
    try:
        _io_log.info("→ %s %s  %s", request.method, request.url, _summarize(request.content))
    except Exception:
        pass


async def _log_response(response: httpx.Response) -> None:
    try:
        await response.aread()  # body not read yet at hook time; cache it for the adapter too
        _io_log.info("← %s %s  %s", response.status_code, response.request.url, _summarize(response.content))
    except Exception:
        pass


def http_client() -> httpx.AsyncClient:
    """Process-shared httpx.AsyncClient. Lazy-init; lifecycle tied to process.

    Attaches request/response event hooks that log every upstream call's input/output
    body to bridge.log (truncated, base64 collapsed, headers excluded). Disable with
    BRIDGE_LOG_IO=off."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        hooks = {}
        if os.getenv("BRIDGE_LOG_IO", "on").strip().lower() != "off":
            hooks = {"request": [_log_request], "response": [_log_response]}
        _HTTP_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), event_hooks=hooks)
    return _HTTP_CLIENT


class BaseAdapter:
    """Shared scaffold for per-provider adapters: base url + key resolution + httpx client.

    Subclasses implement `handle(path, request, raw) -> Response`. Use `self.cfg`,
    `self.base()`, `self.key()`, `http_client()` for vendor calls.
    """
    provider: str = ""  # subclass sets

    def __init__(self) -> None:
        self.cfg = config_mod.load_config()

    def base(self) -> str:
        return self.cfg.base_url(self.provider)

    def key(self) -> str:
        return self.cfg.require_key(self.provider)

    async def handle(self, path: str, request, raw: bytes):  # noqa: ANN001
        raise NotImplementedError
