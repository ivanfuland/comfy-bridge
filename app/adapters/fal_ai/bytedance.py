"""fal-ai backend for the ByteDance/Seedance vendor (spec M2).

Speaks the inbound byteplus protocol (same paths/bodies the ComfyUI bytedance
nodes POST); proxies outbound to fal.ai queue/storage. Registered under the
same 3 route segments as the native byteplus adapter:
  byteplus            : video create + seedream image
  byteplus-seedance2  : video poll
  seedance            : asset shim
Native app/adapters/byteplus.py is NOT modified (spec §2)."""
import asyncio
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


def _parse_media(content):
    """Group inbound media content items by role/type (matches the byteplus node
    shapes, confirmed against byteplus._reshape_video_create — NOT imported, §2):
      {"type":"image_url","image_url":{"url":...},"role":"first_frame"|"last_frame"
                                                          |"reference_image"|None}
      {"type":"video_url","video_url":{"url":...}}
      {"type":"audio_url","audio_url":{"url":...}}
    Returns (first_frame, last_frame, ref_images[], videos[], audios[]) — each url an
    inbound ref (asset://id | bridge url | public url), unresolved. A roleless image is
    treated as a reference image (best-effort; there is no fal endpoint for a bare i2v
    image without a first_frame role, and reference-to-video subsumes the single-image
    case)."""
    first_frame = None
    last_frame = None
    ref_images: list[str] = []
    videos: list[str] = []
    audios: list[str] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "image_url":
            url = (item.get("image_url") or {}).get("url", "")
            if not url:
                continue
            role = item.get("role")
            if role == "first_frame":
                first_frame = url
            elif role == "last_frame":
                last_frame = url
            else:  # reference_image or roleless -> reference set
                ref_images.append(url)
        elif itype == "video_url":
            url = (item.get("video_url") or {}).get("url", "")
            if url:
                videos.append(url)
        elif itype == "audio_url":
            url = (item.get("audio_url") or {}).get("url", "")
            if url:
                audios.append(url)
    return first_frame, last_frame, ref_images, videos, audios


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

        # Image CREATE (synchronous): POST .../images/generations. The Seedream nodes
        # have NO poll for images — sync_op expects the finished result inline. fal is
        # queue-based, so we submit + block-poll internally (run_sync) then return the
        # Ark ImageTaskCreationResponse the node consumes. (NOTE: images/generations,
        # distinct from video's contents/generations/tasks.)
        if method == "POST" and p.endswith("images/generations"):
            return await self._image_create(raw)
        # Video CREATE: POST .../contents/generations/tasks
        if method == "POST" and p.endswith("contents/generations/tasks"):
            return await self._video_create(raw)
        # Video POLL: GET .../contents/generations/tasks/{task_id}
        if method == "GET" and "contents/generations/tasks/" in p:
            return await self._video_poll(p.rsplit("/", 1)[-1])
        # Skeleton: image / further branches land in later tasks.
        return _json_response(
            {"error": {"code": "not_implemented",
                       "message": f"fal-ai byteplus: no handler yet for {request.method} {path}"}},
            status_code=424,
        )

    async def _video_create(self, raw: bytes) -> Response:
        try:
            body = json.loads(raw) if raw else {}
            model = body.get("model", "")
            content = body.get("content") or []
            prompt0, params = _models.parse_prompt_suffix(_content_text(content))
            gen_audio = body.get("generate_audio")

            if _content_has_media(content):
                first, last, ref_imgs, vids, auds = _parse_media(content)
                # i2v (FirstLastFrameNode) iff a frame role is present AND no reference
                # set / video / audio. Otherwise reference-to-video (ReferenceNode). A
                # request mixing a frame role with reference media is ambiguous; prefer
                # reference-to-video (it can carry every modality, where i2v cannot).
                if (first or last) and not (ref_imgs or vids or auds):
                    image_urls = [await self._resolve_to_fal_url(first)] if first else []
                    end_url = await self._resolve_to_fal_url(last) if last else None
                    endpoint = _models.video_endpoint(model, "first_last")
                    payload = _models.build_video_payload(
                        "i2v", prompt0, params,
                        image_urls=image_urls, end_image_url=end_url,
                        generate_audio=gen_audio,
                    )
                else:
                    # frames (if any) fold into the reference image set so they still
                    # condition generation under the multimodal endpoint.
                    all_imgs = ([first] if first else []) + ([last] if last else []) + ref_imgs
                    img_r, vid_r, aud_r = await asyncio.gather(
                        asyncio.gather(*[self._resolve_to_fal_url(u) for u in all_imgs]),
                        asyncio.gather(*[self._resolve_to_fal_url(u) for u in vids]),
                        asyncio.gather(*[self._resolve_to_fal_url(u) for u in auds]),
                    )
                    image_urls, video_urls, audio_urls = list(img_r), list(vid_r), list(aud_r)
                    endpoint = _models.video_endpoint(model, "reference")
                    payload = _models.build_video_payload(
                        "ref", prompt0, params,
                        image_urls=image_urls, video_urls=video_urls,
                        audio_urls=audio_urls, generate_audio=gen_audio,
                    )
            else:
                endpoint = _models.video_endpoint(model, False)
                payload = _models.build_video_payload(
                    "t2v", prompt0, params, generate_audio=gen_audio,
                )

            req_id = await _fal_client.submit(endpoint, payload)
            task_id = _models.encode_task_id(endpoint, req_id)
            return _json_response(
                {"id": task_id, "model": model, "status": "queued"}
            )
        except AssetNotFound as e:
            return _json_response(
                {"error": {"code": "asset_not_found", "message": str(e)}},
                status_code=424,
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

    # ── image create (Task 9): synchronous Seedream gen via fal queue+block-poll ──
    # Inbound body is the Ark Seedream4TaskCreationRequest the ComfyUI Seedream nodes
    # POST (confirmed against ComfyUI apis/bytedance.py): {model, prompt, image: [url]?,
    # size: "WxH", seed, sequential_image_generation_options: {max_images}, watermark}.
    # The reference-image field is `image` (singular list) — same as native
    # byteplus._reshape_image_create; `images` is NOT honored. Output is the Ark
    # ImageTaskCreationResponse {model, created, data: [{url}, ...], error}; the Seedream4
    # node reads EVERY data[i]["url"] for multi-image, so we return all images, never [0].
    # watermark is dropped (no fal seedream param). No poll endpoint exists for images, so
    # any error returns a 4xx/5xx with an error body (not a fake success the node can't read).
    async def _image_create(self, raw: bytes) -> Response:
        body: dict = {}
        try:
            body = json.loads(raw) if raw else {}
            model = body.get("model", "")
            prompt = (body.get("prompt") or "").strip()
            image = body.get("image")
            image_refs = list(image) if isinstance(image, list) else []
            has_image = bool(image_refs)
            endpoint = _models.image_endpoint(model, has_image)  # may raise UnsupportedModel

            payload: dict = {
                "prompt": prompt,
                "image_size": _models.parse_image_size(body.get("size") or ""),  # may raise
            }
            seed = body.get("seed")
            if isinstance(seed, int):
                payload["seed"] = seed
            opts = body.get("sequential_image_generation_options") or {}
            req_max = opts.get("max_images") if isinstance(opts, dict) else None
            if isinstance(req_max, int) and req_max > 0:
                payload["max_images"] = _models.clamp_max_images(model, req_max)
            if has_image:
                payload["image_urls"] = [
                    await self._resolve_to_fal_url(ref) for ref in image_refs
                ]

            result = await _fal_client.run_sync(endpoint, payload)  # blocks to completion
            images = result.get("images") if isinstance(result, dict) else None
            data = [
                {"url": img["url"]}
                for img in (images or [])
                if isinstance(img, dict) and img.get("url")
            ]
            if not data:
                # COMPLETED but no usable url -> surface an error (don't fake success;
                # the node has no poll and would crash reading data[i]["url"]).
                return self._image_error(model, 502, {
                    "code": "comfy_bridge_no_image_url",
                    "message": "fal reported completion but returned no image urls",
                })
            return _json_response(
                {"model": model, "created": 0, "data": data, "error": None}
            )
        except AssetNotFound as e:
            return self._image_error(body.get("model", ""), 424,
                                     {"code": "asset_not_found", "message": str(e)})
        except _models.UnsupportedModel as e:
            return self._image_error(body.get("model", ""), 424,
                                     {"code": "unsupported_model", "message": str(e)})
        except _fal_client.FalConfigError as e:
            return self._image_error(body.get("model", ""), 424,
                                     {"code": "config_error", "message": str(e)})
        except _fal_client.FalUpstreamError as e:
            err = e.body if isinstance(e.body, dict) else {"code": "fal_error", "message": str(e.body)}
            return self._image_error(body.get("model", ""), e.status_code, err)

    def _image_error(self, model: str, status_code: int, error: dict) -> Response:
        """Ark-shaped error ImageTaskCreationResponse. The node reads response.error;
        we also send a non-2xx so a bridge-level handler sees the failure."""
        return _json_response(
            {"model": model, "created": 0, "data": [], "error": error},
            status_code=status_code,
        )

    # ── video poll (Task 8): fal queue status/result -> byteplus TaskStatusResponse ──
    # Output shape mirrors native byteplus._reshape_poll (NOT imported, §2 zero-diff):
    #   {id, model, status in queued|running|cancelled|succeeded|failed,
    #    content?{video_url: STR}, error?{code, message}}
    # The ComfyUI ByteDance2 nodes read response.content.video_url (a plain string;
    # confirmed from ComfyUI apis/bytedance.py TaskStatusResult.video_url: str). Model is
    # not known at poll time (the task_id only encodes endpoint+request_id), so it is "".
    _RUNNING = {"IN_QUEUE", "IN_PROGRESS", "QUEUED", "RUNNING"}
    _FAILED = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}

    async def _video_poll(self, task_id: str) -> Response:
        try:
            endpoint, request_id = _models.decode_task_id(task_id)
        except _models.BadTaskId as e:
            # A garbage id is a caller error, not a fal failure -> clear 4xx (not 500).
            return _json_response(
                {"error": {"code": "bad_task_id", "message": str(e)}},
                status_code=400,
            )

        try:
            st = await _fal_client.status(endpoint, request_id)
            state = str(st.get("status", "")).upper()

            if state == "COMPLETED":
                res = await _fal_client.result(endpoint, request_id)
                # Two-layer error check: an explicit error field OR a missing video url
                # both mean "no usable output" -> failed. Never report succeeded with
                # null/missing content (the node would crash on content.video_url).
                err = res.get("error") if isinstance(res, dict) else None
                video_url = None
                if isinstance(res, dict):
                    video = res.get("video")
                    if isinstance(video, dict):
                        video_url = video.get("url")
                if err or not video_url:
                    return self._poll_failed(task_id, err)
                return _json_response({
                    "id": task_id, "model": "", "status": "succeeded",
                    "content": {"video_url": video_url},
                })

            if state in self._FAILED:
                return self._poll_failed(task_id, st.get("error") or st.get("status"))

            # IN_QUEUE / IN_PROGRESS — and any unrecognized non-terminal status — keep
            # the node polling (running) rather than ending the poll loop prematurely.
            return _json_response(
                {"id": task_id, "model": "", "status": "running", "content": None}
            )
        except _fal_client.FalUpstreamError as e:
            # A status/result HTTP error must not pass through as a raw 5xx: the node
            # polls and expects a status document. Map to a failed poll status doc.
            return self._poll_failed(task_id, e.body)
        except Exception as e:
            # Last-resort guard: any unexpected error (e.g. JSONDecodeError from a
            # non-JSON fal response) must not propagate as a 500. A poll endpoint
            # must always return a status doc the node can read.
            _log.warning("_video_poll unexpected error for %s: %r", task_id, e)
            return self._poll_failed(task_id, str(e))

    def _poll_failed(self, task_id: str, error=None) -> Response:
        """Build a failed TaskStatusResponse with content=None and a surfaced error
        (so the node reports *why* instead of looping or crashing)."""
        if isinstance(error, dict):
            err = error
        elif error:
            err = {"code": "failed", "message": str(error)}
        else:
            err = {"code": "failed", "message": "task failed"}
        return _json_response({
            "id": task_id, "model": "", "status": "failed",
            "content": None, "error": err,
        })

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
