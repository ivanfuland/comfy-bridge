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

from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import BaseAdapter
from app.adapters.fal_ai import _fal_client, _models

_log = logging.getLogger("comfy-bridge.adapters.fal_ai.bytedance")


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


def _content_has_media(content) -> bool:
    """True if any content item carries an image/video/audio reference (i2v/ref)."""
    for item in content or []:
        if isinstance(item, dict) and item.get("type") in (
            "image_url", "video_url", "audio_url",
        ):
            return True
    return False


class FalBytedanceAdapter(BaseAdapter):
    # Do NOT use provider="byteplus" — BaseAdapter.base()/key() would look up
    # BYTEPLUS_BASE_URL/KEY via config; fal uses FAL_KEY (read directly later).
    # fal adapter never calls self.base()/self.key().
    provider = "fal"  # not in config map; identifier only, never used for credential lookup

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        # Video CREATE: POST .../contents/generations/tasks
        if request.method == "POST" and path.rstrip("/").endswith(
            "contents/generations/tasks"
        ):
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


_adapter = FalBytedanceAdapter()
register("byteplus", _adapter)
register("byteplus-seedance2", _adapter)
register("seedance", _adapter)
