"""Async fal.ai HTTP client: queue API (submit/status/result/run_sync) + storage upload.

The adapter's handle() is awaited by the async FastAPI router, so this client
MUST be async (httpx.AsyncClient + await) — a sync httpx call would block the
bridge event loop (mirrors the native app/adapters/byteplus.py pattern, which
uses httpx.AsyncClient). This module does NOT import from or touch byteplus.py.

CONFIRMED fal API facts (spike 2026-05-31, via fal docs MCP search_docs +
the fal MCP upload_file tool contract; no real fal credits spent — submit
response shape was confirmed from docs, not a live generation):

  Auth header (all calls):
    Authorization: Key {FAL_KEY}

  Queue submit:
    POST https://queue.fal.run/{endpoint_id}
    body = model input JSON
    -> 200 JSON includes "request_id" plus convenience URLs
       ("status_url", "response_url", "cancel_url"). We RETURN the full dict and
       use fal's RETURNED status_url/response_url verbatim — we do NOT reconstruct
       them. For multi-segment endpoints (e.g. bytedance/seedance-2.0/text-to-video)
       fal's poll URLs use the *app-id* (the model path WITHOUT the operation
       segment): the returned status_url is .../bytedance/seedance-2.0/requests/{id}
       /status, NOT .../text-to-video/requests/.../status. Reconstructing from the
       full endpoint yields 405 Method Not Allowed (LIVE-CONFIRMED 2026-05-31).

  Queue status:
    GET {status_url}  (the status_url fal returned from submit)
    -> {"status": "IN_QUEUE" | "IN_PROGRESS" | "COMPLETED" | ...}
       (terminal failure surfaces as a non-COMPLETED status string such as
       "FAILED"/"ERROR", or a non-2xx HTTP code.)

  Queue result:
    GET {response_url}  (the response_url fal returned from submit)
    -> model output JSON (e.g. {"video": {"url": ...}} or {"images": [...]}).

  Storage upload (3-step REST, per the fal MCP upload_file tool contract):
    1. POST https://rest.alpha.fal.ai/storage/upload/initiate
         header  Authorization: Key {FAL_KEY}
         body    {"file_name": ..., "content_type": ...}
       -> 200 JSON {"upload_url": ..., "file_url": ...}
    2. PUT  <upload_url>  with the raw bytes (Content-Type: <content_type>)
    3. use <file_url> as the fal-hosted CDN URL.

  Media/object expiry:
    Header X-Fal-Object-Lifecycle-Preference: {"expiration_duration_seconds": N}
    controls CDN object retention. We attach it to the upload initiate request
    so the uploaded media outlives a single generation.

RESIDUAL UNCERTAINTY (flagged; honest, not fabricated):
  * Whether X-Fal-Object-Lifecycle-Preference is honored on the storage
    *upload* initiate call (vs. only on queue submit) was not confirmed by a
    live upload. It is sent best-effort; if fal ignores it on uploads the file
    falls back to the account default retention (>= 7 days per docs), which is
    still well above the bridge's 300s timeout, so MIN_EXPIRY_SECONDS holds.
"""
import asyncio
import json
import logging
import os

import httpx

_log = logging.getLogger("comfy-bridge.adapters.fal_ai.client")

_QUEUE_BASE = "https://queue.fal.run"
_UPLOAD_INITIATE = "https://rest.alpha.fal.ai/storage/upload/initiate"

# Uploaded media must outlive a full generation. The bridge HTTP timeout is
# 300s; clamp expiry to at least this (with margin) so an uploaded input URL
# never expires mid-generation.
MIN_EXPIRY_SECONDS = 3600


class FalConfigError(RuntimeError):
    """FAL_KEY missing / misconfigured (maps to a bridge-side config error)."""


class FalUpstreamError(RuntimeError):
    """A fal HTTP call failed (>=400) or polling exceeded budget.

    Carries .status_code (HTTP status, or 502/504 for synthetic poll errors)
    and .body (parsed JSON / text from fal, or a synthetic detail dict).
    """

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"fal upstream error {status_code}: {body!r}")


def _key() -> str:
    key = os.environ.get("FAL_KEY", "").strip()
    if not key:
        raise FalConfigError("FAL_KEY is not set")
    return key


def _headers() -> dict:
    return {"Authorization": f"Key {_key()}"}


def _safe_body(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return resp.text


def _check(resp: httpx.Response):
    if resp.status_code >= 400:
        raise FalUpstreamError(resp.status_code, _safe_body(resp))


async def _get_json(url: str) -> dict:
    """GET a fal url and return parsed JSON. Raises FalUpstreamError >=400."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=_headers())
    _check(resp)
    return resp.json()


async def submit(endpoint_id: str, payload: dict) -> dict:
    """POST to the queue; return the full response dict.

    The dict carries "request_id" plus fal's convenience URLs ("status_url",
    "response_url", "cancel_url"). Callers MUST use the returned status_url/
    response_url for polling — they are NOT reconstructable from endpoint_id+id
    (see module docstring: multi-segment endpoints drop the operation segment).
    Raises FalUpstreamError >=400.
    """
    headers = {**_headers(), "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{_QUEUE_BASE}/{endpoint_id}", json=payload, headers=headers)
    _check(resp)
    data = resp.json()
    req_id = data.get("request_id")
    if not req_id:
        raise FalUpstreamError(502, {"error": "fal submit response missing request_id", "body": data})
    if not data.get("status_url") or not data.get("response_url"):
        raise FalUpstreamError(502, {"error": "fal submit response missing status/response url", "body": data})
    return data


async def status(status_url: str) -> dict:
    """GET queue status from the status_url fal returned on submit."""
    return await _get_json(status_url)


async def result(response_url: str) -> dict:
    """GET the model output from the response_url fal returned on submit."""
    return await _get_json(response_url)


async def upload_bytes(data: bytes, content_type: str, *,
                       expiry_seconds: int = MIN_EXPIRY_SECONDS) -> str:
    """Upload raw bytes to fal storage; return the fal-hosted CDN URL.

    expiry_seconds is clamped up to MIN_EXPIRY_SECONDS so an uploaded input
    URL always outlives a generation. Best-effort sets the object lifecycle
    header on the initiate call (see module docstring residual-uncertainty).
    """
    expiry_seconds = max(expiry_seconds, MIN_EXPIRY_SECONDS)
    ext = (content_type.split("/", 1)[-1] or "bin").split(";", 1)[0] or "bin"
    file_name = f"comfy-bridge-upload.{ext}"
    init_headers = {
        **_headers(),
        "Content-Type": "application/json",
        "X-Fal-Object-Lifecycle-Preference": json.dumps(
            {"expiration_duration_seconds": expiry_seconds}
        ),
    }
    async with httpx.AsyncClient(timeout=60) as client:
        init = await client.post(
            _UPLOAD_INITIATE,
            json={"file_name": file_name, "content_type": content_type},
            headers=init_headers,
        )
        _check(init)
        info = init.json()
        upload_url = info.get("upload_url")
        file_url = info.get("file_url")
        if not upload_url or not file_url:
            raise FalUpstreamError(502, {"error": "fal upload initiate missing urls", "body": info})

        # pre-signed upload_url: NO Authorization header (signature is in the URL;
        # adding auth would break the S3-style PUT).
        put = await client.put(upload_url, content=data, headers={"Content-Type": content_type})
        _check(put)
    return file_url


async def run_sync(endpoint_id: str, payload: dict, *,
                   poll_interval: float = 1.0, max_wait: float = 280.0) -> dict:
    """Submit + poll to completion (async sleep, never blocks the loop).

    On COMPLETED -> result(). On FAILED/ERROR -> FalUpstreamError(502, status).
    On exceeding max_wait -> FalUpstreamError(504, ...). max_wait is kept below
    the bridge's 300s HTTP timeout.
    """
    sub = await submit(endpoint_id, payload)
    status_url = sub["status_url"]
    response_url = sub["response_url"]
    waited = 0.0
    while True:
        st = await status(status_url)
        state = (st.get("status") or "").upper()
        if state == "COMPLETED":
            return await result(response_url)
        if state in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
            raise FalUpstreamError(502, st)
        if state not in ("IN_QUEUE", "IN_PROGRESS"):
            _log.warning("fal run_sync: unrecognized status %r, continuing to poll", state)
        await asyncio.sleep(poll_interval)
        waited += poll_interval
        if waited >= max_wait:
            raise FalUpstreamError(504, {"error": "fal sync timeout", "endpoint": endpoint_id})
