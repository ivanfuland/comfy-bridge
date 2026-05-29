"""Error helpers. Missing key/config => 424 (never 401/402/5xx, which the comfy client
mangles into 'Please login first' or puts into the retry set _RETRY_STATUS={408,500,502,503,504}
at util/client.py:86)."""
from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import MissingConfig


def missing_config_response(detail: str) -> JSONResponse:
    return JSONResponse(status_code=424, content={"error": {"message": detail, "type": "comfy_bridge_config"}})


def asset_not_found_response(detail: str) -> JSONResponse:
    """Stale/unknown bridge asset reference (process restart, stale URL, etc.). 400 = permanent
    client error so ComfyUI's retry set _RETRY_STATUS={408,500,502,503,504} does NOT retry it."""
    return JSONResponse(status_code=400, content={"error": {"message": detail, "type": "comfy_bridge_asset_not_found"}})


def vendor_error_response(status_code: int, body) -> JSONResponse:
    """Normalize vendor 4xx/5xx, prefix with comfy-bridge for locating. Pass through original status."""
    if isinstance(body, dict):
        content = {"error": {"message": f"comfy-bridge upstream: {body}", "type": "comfy_bridge_upstream"}}
    else:
        content = {"error": {"message": f"comfy-bridge upstream: {str(body)[:500]}", "type": "comfy_bridge_upstream"}}
    return JSONResponse(status_code=status_code, content=content)


def install_exception_handlers(app):
    # Lazy import to avoid circular dependency: adapters/base imports assets which imports config;
    # errors is imported by main.py which imports adapters. Importing AssetNotFound at module top
    # would create a cycle. Importing inside the installer keeps errors.py independent of adapters.
    from app.adapters.base import AssetNotFound

    @app.exception_handler(MissingConfig)
    async def _missing(_: Request, exc: MissingConfig):
        return missing_config_response(str(exc))

    @app.exception_handler(AssetNotFound)
    async def _asset_missing(_: Request, exc: AssetNotFound):
        return asset_not_found_response(str(exc))
