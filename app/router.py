"""Router: /proxy/{provider}/{path:path}. In capture mode just records and echoes the
request (spec §10 capture-first). Otherwise dispatches to the per-provider adapter."""
import json
import logging
import os
import time
import uuid

from fastapi import APIRouter, Request, Response

from app.adapters import get_adapter
from app.errors import missing_config_response

router = APIRouter()
logger = logging.getLogger("comfy-bridge")

_REDACT_HEADERS = {"authorization", "x-api-key", "x-goog-api-key"}


def _redact(headers: dict) -> dict:
    return {k: ("***" if k.lower() in _REDACT_HEADERS else v) for k, v in headers.items()}


def _capture_enabled() -> bool:
    return os.getenv("BRIDGE_CAPTURE", "").strip() in ("1", "true", "on")


async def _record(provider: str, path: str, request: Request, raw: bytes) -> dict:
    try:
        body = json.loads(raw) if raw else None
    except Exception:
        body = f"[{len(raw)} bytes non-json]"
    captured = {
        "ts": time.time(),
        "provider": provider,
        "method": request.method,
        "path": path,
        "query": dict(request.query_params),
        "headers": _redact(dict(request.headers)),
        "body": body,
    }
    cap_dir = os.getenv("BRIDGE_CAPTURE_DIR")
    if cap_dir:
        os.makedirs(cap_dir, exist_ok=True)
        fn = os.path.join(cap_dir, f"{int(captured['ts']*1000)}_{provider}_{uuid.uuid4().hex[:6]}.json")
        with open(fn, "w") as f:
            json.dump(captured, f, indent=2, default=str)
    logger.info("capture %s %s/%s", request.method, provider, path)
    return captured


@router.api_route("/proxy/{provider}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(provider: str, path: str, request: Request) -> Response:
    raw = await request.body()
    if _capture_enabled():
        captured = await _record(provider, path, request, raw)
        # 503: capture mode is for offline request inspection only; nodes get clear stop.
        # Body still includes captured payload for debug curl/inspection.
        return Response(
            content=json.dumps({
                "error": {
                    "message": "comfy-bridge: capture mode enabled, request recorded but not forwarded",
                    "type": "comfy_bridge_capture",
                },
                "captured": captured,
            }, default=str),
            status_code=503,
            media_type="application/json",
        )
    adapter = get_adapter(provider)
    if adapter is None:
        # distinct from missing key: this is a dev error (adapter module not yet implemented/registered)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=424,
            content={"error": {"message": f"comfy-bridge: adapter for {provider} not registered (Task 5-8 pending or import failed)", "type": "comfy_bridge_adapter_unregistered"}},
        )
    return await adapter.handle(path=path, request=request, raw=raw)
