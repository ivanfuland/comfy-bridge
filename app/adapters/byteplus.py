"""ByteDance / Seedance adapter — covers all 10 ComfyUI generation nodes (3 Seedream
image + 4 Seedance 1.x video + 3 Seedance 2.0 video) plus the optional asset-helper
nodes, by translating the Volcengine-Ark dialect the nodes speak into the gateway's
`/v1/{video,images}/generations` dialect (网易雷火).

One adapter instance is registered under THREE route vendor segments (the ComfyUI
nodes derive these from each endpoint's path):
  - byteplus            : video create (1.x + 2.0) + 1.x poll + seedream image
  - byteplus-seedance2  : 2.0 poll (different prefix, SAME upstream task id space)
  - seedance            : asset / auth shims (virtual-library, assets, visual-validate)
All three share ONE gateway base/key, resolved under the single config provider
"byteplus" (BYTEPLUS_BASE_URL / BYTEPLUS_API_KEY). NB: the *gating* vendor is
"bytedance" (derived client-side from python_module=nodes_bytedance), which is a
different name — see config.DEFAULT_ALLOWED_VENDORS.

Dispatch is purely path+method based (the router strips the vendor segment before
calling handle), so byteplus and byteplus-seedance2 polls land on the same code.

Translation summary (spec §"翻译/Shim 设计"):
  • Video create  POST api/v3/contents/generations/tasks -> POST {base}/v1/video/generations
      - model: seedance-*->doubao-seedance-*; dreamina-seedance-2-0-*->doubao-seedance-2-0-*
      - 1.x:  --params already inline in content[].text -> prompt verbatim
      - 2.0:  separate fields (resolution/ratio/duration/seed/watermark) -> appended as
              --params to the prompt text (the gateway only consumes inline --params)
      - images by role -> ONE top-level images[] array (1=first frame, 2=first+last,
        N=reference set); the gateway silently drops flat first_frame_image/etc. NB the
        video endpoint wants images[] (plural) but the image endpoint below wants `image`.
      - bridge asset urls (…/asset/{id}) and asset://{id} (2.0 virtual-library) are
        resolved to base64 data-URIs (the gateway cannot reach 127.0.0.1)
  • Video poll    GET …/tasks/{id}        -> GET {base}/v1/video/generations/{id}
      - returns the inner data.data (already Ark TaskStatusResponse shape); falls back to
        synthesizing one from the outer envelope status when the inner block is absent
  • Image         POST api/v3/images/generations -> POST {base}/v1/images/generations
      - model -> doubao-seedream-*; reference image[] -> base64; response normalized to
        ImageTaskCreationResponse (model/created/data/error)
  • Shims (no upstream, no key): virtual-library/assets + assets (store bridge asset,
    return asset_id), assets/{id} (Active GetAssetResponse), visual-validate/sessions
    (immediate completed group_id so the 2.0 H5 face-auth flow never blocks).
"""
import json
import uuid
from urllib.parse import urlparse

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.adapters import register
from app.adapters.base import (
    AssetNotFound,
    BaseAdapter,
    http_client,
    is_bridge_asset_url,
    resolve_asset_to_base64,
)
from app.config import MissingConfig
from app.errors import missing_config_response, vendor_error_response

# asset_id -> {"url": <bridge asset url or public url>, "asset_type": "Image|Video|Audio"}.
# Populated by the seedance shims, consumed by asset:// resolution in video create.
# Process-local (in-memory): a bridge restart drops it, mirroring assets._REGISTRY.
_SEEDANCE_ASSETS: dict[str, dict] = {}

_OUTER_STATUS_MAP = {
    "QUEUED": "queued",
    "IN_PROGRESS": "running",
    "RUNNING": "running",
    "SUCCESS": "succeeded",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "FAILURE": "failed",  # gateway's actual terminal-failure spelling (new-api)
    "ERROR": "failed",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
}

# Outer-envelope states that are terminal failures. On these the gateway frequently
# leaves the inner data.data block frozen at its last status:"running" snapshot, so
# the inner block must NOT be trusted here (doing so hangs ComfyUI's poll loop).
_OUTER_FAILURE_STATES = {"failed", "cancelled"}


# ── model name mapping ────────────────────────────────────────────────────────
def _map_video_model(model: str) -> str:
    """seedance-1-5-pro-251215 -> doubao-seedance-1-5-pro-251215;
    dreamina-seedance-2-0-260128 -> doubao-seedance-2-0-260128 (strip dreamina-, add doubao-)."""
    if not model or model.startswith("doubao-"):
        return model
    if model.startswith("dreamina-seedance-"):
        return "doubao-" + model[len("dreamina-"):]
    if model.startswith("seedance-"):
        return "doubao-" + model
    return model


def _map_image_model(model: str) -> str:
    """seedream-4-5-251128 -> doubao-seedream-4-5-251128 (add doubao- prefix)."""
    if not model or model.startswith("doubao-"):
        return model
    if model.startswith("seedream-"):
        return "doubao-" + model
    return model


# ── asset reference resolution (gateway can't reach 127.0.0.1) ─────────────────
def _to_data_uri_if_bridge(url: str) -> str:
    if is_bridge_asset_url(url):
        b64, media_type = resolve_asset_to_base64(url)
        return f"data:{media_type};base64,{b64}"
    return url


def _resolve_media_url(url: str) -> str:
    """Resolve a content media url to something the gateway can fetch. Handles the 2.0
    asset://{id} scheme (via the virtual-library/asset shim registry) and bridge asset
    urls -> base64 data-URI. Public urls pass through unchanged."""
    if not isinstance(url, str) or not url:
        return url
    if url.startswith("asset://"):
        asset_id = url[len("asset://"):]
        rec = _SEEDANCE_ASSETS.get(asset_id)
        if rec is None:
            raise AssetNotFound(f"comfy-bridge: seedance asset not found for {url}")
        return _to_data_uri_if_bridge(rec.get("url", ""))
    return _to_data_uri_if_bridge(url)


def _asset_id_from_url(url: str) -> str:
    """Reuse the bridge asset id as the seedance asset_id when possible (keeps
    asset://{id} resolvable without a second indirection); else mint a uuid."""
    if is_bridge_asset_url(url):
        path = urlparse(url).path.rstrip("/")
        aid = path.rsplit("/", 1)[-1]
        if aid:
            return aid
    return uuid.uuid4().hex


def _store_seedance_asset(url: str, asset_type: str) -> str:
    asset_id = _asset_id_from_url(url)
    _SEEDANCE_ASSETS[asset_id] = {"url": url, "asset_type": asset_type or "Image"}
    return asset_id


# ── request reshaping ──────────────────────────────────────────────────────────
def _seedance2_params_suffix(body: dict, prompt: str) -> str:
    """Build the --params suffix for Seedance 2.0 from its separate fields. Skips any
    flag already present inline in the prompt (defensive against a user-typed --flag)."""
    flags: list[str] = []
    for flag in ("resolution", "ratio", "duration", "seed"):
        val = body.get(flag)
        if val is None or f"--{flag} " in prompt:
            continue
        flags.append(f"--{flag} {val}")
    watermark = body.get("watermark")
    if watermark is not None and "--watermark " not in prompt:
        flags.append(f"--watermark {str(bool(watermark)).lower()}")
    return " ".join(flags)


def _reshape_video_create(body: dict) -> dict:
    """Ark contents/generations/tasks body -> gateway /v1/video/generations body.
    May raise AssetNotFound (propagated to the global handler -> 400) when a referenced
    bridge/asset:// url is stale."""
    model = body.get("model", "")
    content = body.get("content") or []
    texts: list[str] = []
    image_url = None
    first_frame = None
    last_frame = None
    reference_images: list[str] = []
    reference_videos: list[str] = []
    reference_audios: list[str] = []

    for item in content:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "text":
            text = item.get("text")
            if text:
                texts.append(text)
        elif itype == "image_url":
            url = _resolve_media_url((item.get("image_url") or {}).get("url", ""))
            role = item.get("role")
            if role == "first_frame":
                first_frame = url
            elif role == "last_frame":
                last_frame = url
            elif role == "reference_image":
                reference_images.append(url)
            else:
                image_url = url
        elif itype == "video_url":
            reference_videos.append(_resolve_media_url((item.get("video_url") or {}).get("url", "")))
        elif itype == "audio_url":
            reference_audios.append(_resolve_media_url((item.get("audio_url") or {}).get("url", "")))

    prompt = " ".join(texts).strip()
    is_v2 = model.startswith("dreamina-") or any(
        body.get(k) is not None for k in ("resolution", "ratio", "duration")
    )
    if is_v2:
        suffix = _seedance2_params_suffix(body, prompt)
        if suffix:
            prompt = f"{prompt} {suffix}".strip()

    out: dict = {"model": _map_video_model(model), "prompt": prompt}
    # Gateway unified /v1/video/generations contract: ALL input images go in ONE top-level
    # `images` array — 1 = first frame, 2 = first+last frame, N = reference set. The flat
    # first_frame_image / last_frame_image / reference_images fields the nodes imply are
    # silently DROPPED by the gateway (-> it falls back to text2video with the wrong
    # character — verified live 2026-05-30). The four image scenarios are mutually
    # exclusive (Volcano: 含图像的 3 种场景互斥), so pick the one that's populated; order
    # matters for first+last (first, then last).
    images: list[str] = []
    if first_frame:
        images = [first_frame] + ([last_frame] if last_frame else [])
    elif last_frame:
        images = [last_frame]
    elif reference_images:
        images = list(reference_images)  # role:reference_image not expressible top-level; best-effort
    elif image_url:
        images = [image_url]
    if images:
        out["images"] = images
    if reference_videos:  # best-effort (gateway video-ref contract undocumented)
        out["reference_videos"] = reference_videos
    if reference_audios:
        out["reference_audios"] = reference_audios
    return out


def _reshape_image_create(body: dict) -> dict:
    """Seedream Ark images/generations body -> gateway body: doubao- model + ref images
    resolved to base64. Other fields (size/seed/watermark/…) pass through; the gateway
    ignores unknown ones.

    The reference-image field here is `image` (singular) — verified 2026-05-30: `image`
    is honored, `images` is silently dropped (-> text2img, reference ignored). This is the
    OPPOSITE of the video endpoint, which wants images[] (plural). Don't "unify" them."""
    out = dict(body)
    out["model"] = _map_image_model(body.get("model", ""))
    img = body.get("image")
    if isinstance(img, list):
        out["image"] = [_resolve_media_url(u) for u in img]
    return out


# ── response reshaping ───────────────────────────────────────────────────────
def _normalize_create_response(payload):
    """Ensure a TaskCreationResponse-compatible top-level `id` (the node reads .id)."""
    if not isinstance(payload, dict):
        return payload
    if payload.get("id"):
        return payload
    if payload.get("task_id"):
        payload["id"] = payload["task_id"]
        return payload
    data = payload.get("data")
    if isinstance(data, dict):
        if data.get("id"):
            payload["id"] = data["id"]
        elif data.get("task_id"):
            payload["id"] = data["task_id"]
    return payload


def _ensure_status_fields(inner: dict, task_id: str) -> dict:
    """TaskStatusResponse requires id+model+status; backfill from the request id when
    the gateway omits them on an early poll."""
    out = dict(inner)
    if not out.get("id"):
        out["id"] = task_id
    if out.get("model") is None:
        out["model"] = ""
    return out


def _outer_error(data: dict) -> dict:
    """Build an Ark-shaped error from a failed outer envelope. Prefer a structured
    data.error, else synthesize from fail_reason/message so the node surfaces *why*."""
    err = data.get("error")
    if isinstance(err, dict) and err:
        return err
    reason = data.get("fail_reason") or data.get("message")
    return {
        "code": str(data.get("status", "failed")).lower() or "failed",
        "message": str(reason) if reason else "task failed",
    }


def _reshape_poll(payload, task_id: str) -> dict:
    """Gateway poll envelope -> Ark TaskStatusResponse. A terminal-failure outer status
    wins over a stale inner block; otherwise prefer the inner data.data block (already
    Ark-shaped); else synthesize from the outer envelope status."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        # The gateway may already hand back an Ark-shaped body directly.
        if isinstance(payload, dict) and payload.get("status"):
            return _ensure_status_fields(payload, task_id)
        return {"id": task_id, "model": "", "status": "running", "content": None}

    # Terminal failure on the outer envelope wins over a stale inner block. On a failed
    # task the gateway often freezes data.data at status:"running" (and even sets
    # result_url to the error string), so honoring the inner block here would report
    # "running" forever and hang the poll loop. Use the outer fail_reason as the error.
    outer_status = str(data.get("status", "")).upper()
    if _OUTER_STATUS_MAP.get(outer_status) in _OUTER_FAILURE_STATES:
        return {
            "id": data.get("task_id") or task_id,
            "model": (data.get("properties") or {}).get("origin_model_name") or "",
            "status": _OUTER_STATUS_MAP[outer_status],
            "content": None,
            "error": _outer_error(data),
        }

    inner = data.get("data")
    if isinstance(inner, dict) and inner.get("status"):
        return _ensure_status_fields(inner, task_id)

    # Fallback: synthesize from the outer envelope when there's no Ark-shaped inner block.
    mapped = _OUTER_STATUS_MAP.get(outer_status, "running")
    # Recover a downloadable url from the envelope (result_url, or a stray inner content)
    # so a 'succeeded' never carries null content — the node reads content.video_url and
    # would crash on None. A success with no url anywhere is unusable -> report failed
    # (clearer than a crash or an endless poll).
    video_url = data.get("result_url")
    if not video_url and isinstance(data.get("data"), dict):
        inner_content = data["data"].get("content")
        if isinstance(inner_content, dict):
            video_url = inner_content.get("video_url")
    content = {"video_url": video_url} if video_url else None
    err = data.get("error")
    if mapped == "succeeded" and content is None:
        mapped = "failed"
        if not err:
            err = {
                "code": "comfy_bridge_no_video_url",
                "message": "gateway reported success but returned no video_url/result_url",
            }
    result: dict = {
        "id": data.get("task_id") or task_id,
        "model": "",
        "status": mapped,
        "content": content,
    }
    if err:
        result["error"] = err if isinstance(err, dict) else {"code": "error", "message": str(err)}
    return result


def _normalize_image_response(payload, req_model: str) -> dict:
    """Gateway image response -> ImageTaskCreationResponse (model/created/data/error).
    `data` items pass through (the node reads data[i]['url'])."""
    if not isinstance(payload, dict):
        return {"model": req_model, "created": 0, "data": [], "error": {}}
    data = payload.get("data")
    if not isinstance(data, list):
        data = []
    error = payload.get("error")
    return {
        "model": payload.get("model") or req_model,
        "created": payload.get("created") or 0,
        "data": data,
        "error": error if isinstance(error, dict) else {},
    }


# ── small response helpers ──────────────────────────────────────────────────────
def _json_response(obj, status_code: int = 200) -> Response:
    return Response(content=json.dumps(obj), status_code=status_code, media_type="application/json")


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return resp.text


class ByteplusAdapter(BaseAdapter):
    # Shared by all three route segments; base()/key() resolve the "byteplus" config.
    provider = "byteplus"

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        p = path.strip("/")
        method = request.method

        # ── local shims: no upstream, no key required ──
        if p == "virtual-library/assets" and method == "POST":
            return self._asset_create(raw)
        if p == "assets" and method == "POST":
            return self._asset_create(raw)
        if p.startswith("assets/") and method == "GET":
            return self._asset_get(p.rsplit("/", 1)[-1])
        if p == "visual-validate/sessions" and method == "POST":
            return _json_response({"session_id": uuid.uuid4().hex, "h5_link": ""})
        if p.startswith("visual-validate/sessions/") and method == "GET":
            return _json_response(
                {"session_id": p.rsplit("/", 1)[-1], "status": "completed", "group_id": "comfy-bridge"}
            )

        # ── upstream calls: key required ──
        try:
            key = self.key()
        except MissingConfig as e:
            return missing_config_response(str(e))
        base = self.base()
        headers = {"Authorization": f"Bearer {key}"}
        client = http_client()

        if p == "api/v3/images/generations" and method == "POST":
            return await self._image_create(client, base, headers, raw)
        if p == "api/v3/contents/generations/tasks" and method == "POST":
            return await self._video_create(client, base, headers, raw)
        if p.startswith("api/v3/contents/generations/tasks/") and method == "GET":
            return await self._video_poll(client, base, headers, p.rsplit("/", 1)[-1])

        return JSONResponse(
            status_code=424,
            content={
                "error": {
                    "message": f"comfy-bridge: byteplus adapter has no handler for {method} {p}",
                    "type": "comfy_bridge_unhandled_path",
                }
            },
        )

    # ── shim handlers ──
    def _asset_create(self, raw: bytes) -> Response:
        body = json.loads(raw) if raw else {}
        asset_id = _store_seedance_asset(body.get("url", ""), body.get("asset_type") or "Image")
        return _json_response({"asset_id": asset_id})

    def _asset_get(self, asset_id: str) -> Response:
        rec = _SEEDANCE_ASSETS.get(asset_id)
        if rec is None:
            # Unknown id (stale asset_id, or _SEEDANCE_ASSETS wiped by a bridge restart):
            # do NOT fake Active. Report Failed so the node's _wait_for_asset_active /
            # _resolve_reference_assets surface a clear error up front, instead of accepting
            # a phantom Active asset that only fails later at asset:// resolution.
            return _json_response(
                {
                    "id": asset_id,
                    "name": None,
                    "url": None,
                    "asset_type": "Image",
                    "group_id": "comfy-bridge",
                    "status": "Failed",
                    "error": {
                        "code": "comfy_bridge_asset_unknown",
                        "message": f"unknown seedance asset {asset_id} (not in this bridge's registry)",
                    },
                }
            )
        return _json_response(
            {
                "id": asset_id,
                "name": None,
                "url": rec.get("url"),
                "asset_type": rec.get("asset_type", "Image"),
                "group_id": "comfy-bridge",
                "status": "Active",
                "error": None,
            }
        )

    # ── upstream handlers ──
    async def _video_create(self, client, base, headers, raw: bytes) -> Response:
        body = json.loads(raw) if raw else {}
        out = _reshape_video_create(body)  # may raise AssetNotFound -> global 400
        resp = await client.post(
            f"{base}/v1/video/generations",
            json=out,
            headers={**headers, "content-type": "application/json"},
        )
        if resp.status_code >= 400:
            return vendor_error_response(resp.status_code, _safe_json(resp))
        payload = _safe_json(resp)
        if not isinstance(payload, dict):
            return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
        return _json_response(_normalize_create_response(payload), status_code=resp.status_code)

    async def _video_poll(self, client, base, headers, task_id: str) -> Response:
        resp = await client.get(f"{base}/v1/video/generations/{task_id}", headers=headers)
        if resp.status_code >= 400:
            return vendor_error_response(resp.status_code, _safe_json(resp))
        payload = _safe_json(resp)
        if not isinstance(payload, dict):
            return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
        return _json_response(_reshape_poll(payload, task_id), status_code=200)

    async def _image_create(self, client, base, headers, raw: bytes) -> Response:
        body = json.loads(raw) if raw else {}
        out = _reshape_image_create(body)  # may raise AssetNotFound -> global 400
        resp = await client.post(
            f"{base}/v1/images/generations",
            json=out,
            headers={**headers, "content-type": "application/json"},
        )
        if resp.status_code >= 400:
            return vendor_error_response(resp.status_code, _safe_json(resp))
        payload = _safe_json(resp)
        return _json_response(
            _normalize_image_response(payload, out.get("model", "")), status_code=resp.status_code
        )


_adapter = ByteplusAdapter()
register("byteplus", _adapter)
register("byteplus-seedance2", _adapter)
register("seedance", _adapter)
