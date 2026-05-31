"""fal-ai backend for the ByteDance/Seedance vendor (spec M2).

Speaks the inbound byteplus protocol (same paths/bodies the ComfyUI bytedance
nodes POST); proxies outbound to fal.ai queue/storage. Registered under the
same 3 route segments as the native byteplus adapter:
  byteplus            : video create + seedream image
  byteplus-seedance2  : video poll
  seedance            : asset shim
Native app/adapters/byteplus.py is NOT modified (spec §2)."""
import json
import logging
import uuid
from urllib.parse import urlparse

from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import (
    AssetNotFound,
    BaseAdapter,
    is_bridge_asset_url,
    resolve_asset_bytes,
)
from app.adapters.fal_ai import _fal_client, _models

_log = logging.getLogger("comfy-bridge.adapters.fal_ai.bytedance")

# asset_id -> {"url": <bridge asset url>, "asset_type": "Image|Video|Audio"}.
# fal-side mirror of byteplus._SEEDANCE_ASSETS. The seedance virtual-library shim
# only maps an asset_id to a bridge asset url (the media itself already lives in the
# bridge asset cache, app.assets._REGISTRY, uploaded by the node via /customers/storage
# + PUT before this POST). Process-local: a bridge restart drops it (so GET on a stale
# id reports Failed, mirroring native semantics). We do NOT import byteplus.py (spec §2
# zero-diff); this is a thin fal-side equivalent of its _SEEDANCE_ASSETS registry.
_FAL_SEEDANCE_ASSETS: dict[str, dict] = {}


def _json_response(obj, status_code: int = 200) -> Response:
    return Response(content=json.dumps(obj), media_type="application/json",
                    status_code=status_code)


def _content_text(content) -> str:
    """Concatenate the text fields of text items in an inbound `content` list."""
    parts: list[str] = []
    for item in content or []:
        if isinstance(item, dict):
            text = item.get("text")
            if text:
                parts.append(text)
    return " ".join(parts).strip()


def _asset_id_from_url(url: str) -> str:
    """Reuse the bridge asset id as the seedance asset_id when possible (keeps
    asset://{id} resolvable without a second indirection); else mint a uuid.
    Mirrors byteplus._asset_id_from_url (kept fal-local for the zero-diff constraint)."""
    if is_bridge_asset_url(url):
        aid = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        if aid:
            return aid
    return uuid.uuid4().hex


def _content_has_media(content) -> bool:
    """True if any content item carries an image/video/audio reference (i2v/ref)."""
    for item in content or []:
        if isinstance(item, dict) and item.get("type") in (
            "image_url", "video_url", "audio_url",
        ):
            return True
    return False


def _asset_response(asset_id: str, *, status: str, url=None, asset_type: str = "Image", error=None) -> Response:
    return _json_response({
        "id": asset_id, "name": None, "url": url,
        "asset_type": asset_type, "group_id": "comfy-bridge",
        "status": status, "error": error,
    })


class FalBytedanceAdapter(BaseAdapter):
    # Do NOT use provider="byteplus" — BaseAdapter.base()/key() would look up
    # BYTEPLUS_BASE_URL/KEY via config; fal uses FAL_KEY (read directly later).
    # fal adapter never calls self.base()/self.key().
    provider = "fal"  # not in config map; identifier only, never used for credential lookup

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        p = path.strip("/")
        method = request.method

        # ── seedance asset shim (no fal call on upload/GET; media already in the
        #    bridge asset cache — the node uploads it via /customers/storage first) ──
        if method == "POST" and p in ("virtual-library/assets", "assets"):
            return self._asset_create(raw)
        if method == "GET" and p.startswith("assets/"):
            return self._asset_get(p.rsplit("/", 1)[-1])

        # Video CREATE: POST .../contents/generations/tasks
        if method == "POST" and p.endswith("contents/generations/tasks"):
            return await self._video_create(raw)
        # Skeleton: poll / image / i2v / reference land in later tasks.
        return _json_response(
            {"error": {"code": "not_implemented",
                       "message": f"fal-ai byteplus: no handler yet for {request.method} {path}"}},
            status_code=424,
        )

    async def _video_create(self, raw: bytes) -> Response:
        try:
            body = json.loads(raw) if raw else {}
            content = body.get("content") or []
            # i2v/reference (content carries media) deferred to a later task.
            if _content_has_media(content):
                return _json_response(
                    {"error": {"code": "not_implemented",
                               "message": "fal-ai byteplus: image/reference video not yet supported"}},
                    status_code=424,
                )
            prompt0, params = _models.parse_prompt_suffix(_content_text(content))
            endpoint = _models.video_endpoint(body.get("model", ""), False)
            payload = _models.build_video_payload(
                "t2v", prompt0, params, generate_audio=body.get("generate_audio"),
            )
            req_id = await _fal_client.submit(endpoint, payload)
            task_id = _models.encode_task_id(endpoint, req_id)
            return _json_response(
                {"id": task_id, "model": body.get("model"), "status": "queued"}
            )
        except _models.UnsupportedModel as e:
            return _json_response(
                {"error": {"code": "unsupported_model", "message": str(e)}},
                status_code=424,
            )
        except _fal_client.FalConfigError as e:
            return _json_response(
                {"error": {"code": "config_error", "message": str(e)}},
                status_code=424,
            )
        except _fal_client.FalUpstreamError as e:
            body_obj = e.body if isinstance(e.body, dict) else {"detail": e.body}
            return _json_response({"error": body_obj}, status_code=e.status_code)

    # ── seedance asset shim handlers ──
    def _asset_create(self, raw: bytes) -> Response:
        """virtual-library/assets (and the helper /assets) upload: the node has already
        PUT the media into the bridge asset cache and passes its bridge download_url here.
        We map an asset_id -> that bridge url; the bytes are pulled on demand at resolve
        time (asset:// -> fal upload). Returns {"asset_id": ...}."""
        body = json.loads(raw) if raw else {}
        url = body.get("url", "")
        if not url:
            return _json_response({"error": "url required"}, status_code=400)
        asset_id = _asset_id_from_url(url)
        _FAL_SEEDANCE_ASSETS[asset_id] = {"url": url, "asset_type": body.get("asset_type") or "Image"}
        return _json_response({"asset_id": asset_id})

    def _asset_get(self, asset_id: str) -> Response:
        """GET assets/{id}: Active for a known asset, Failed for an unknown one (stale id
        or a bridge restart wiped the registry). MIRROR native semantics — do NOT fake
        Active for unknown ids, so the node surfaces the error up front instead of failing
        later at asset:// resolution."""
        rec = _FAL_SEEDANCE_ASSETS.get(asset_id)
        if rec is None:
            return _asset_response(
                asset_id,
                status="Failed",
                error={
                    "code": "comfy_bridge_asset_unknown",
                    "message": f"unknown seedance asset {asset_id} (not in this bridge's registry)",
                },
            )
        return _asset_response(
            asset_id,
            status="Active",
            url=rec.get("url"),
            asset_type=rec.get("asset_type", "Image"),
        )

    async def _resolve_to_fal_url(self, ref: str) -> str:
        """Resolve an inbound media ref to a fal-hosted CDN url (used by the i2v/reference
        create path in the next task).

        Accepts either an `asset://{id}` ref (seedance 2.0 virtual-library) or a bridge
        asset download_url (…/asset/{id}). Loads the bytes from the bridge asset cache and
        uploads them to fal storage. Public urls (already fal/CDN-reachable) pass through.

        Raises AssetNotFound when the ref points at an unknown asset_id or a missing cache
        entry (propagated like native byteplus -> the create path surfaces a 400)."""
        if not isinstance(ref, str) or not ref:
            raise AssetNotFound(f"comfy-bridge: empty seedance media ref {ref!r}")
        if ref.startswith("asset://"):
            asset_id = ref[len("asset://"):]
            rec = _FAL_SEEDANCE_ASSETS.get(asset_id)
            if rec is None:
                raise AssetNotFound(f"comfy-bridge: seedance asset not found for {ref}")
            url = rec.get("url", "")
        else:
            url = ref
        if not is_bridge_asset_url(url):
            # A public/already-reachable url — fal can fetch it directly; no re-upload.
            return url
        data, content_type = resolve_asset_bytes(url)  # raises AssetNotFound if cache-miss
        return await _fal_client.upload_bytes(data, content_type)


_adapter = FalBytedanceAdapter()
register("byteplus", _adapter)
register("byteplus-seedance2", _adapter)
register("seedance", _adapter)
