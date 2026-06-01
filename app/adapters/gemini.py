"""Gemini adapter (spec §4/§5). Incoming Vertex shell 'gemini/{model}' (provider segment
'vertexai' already stripped by router; nodes_gemini.py:48,497,710) -> GL:
{base}/v1beta/models/{model}:generateContent, AI Studio key via header x-goog-api-key.
Body transforms: drop uploadImagesToStorage (apis/gemini.py:142-149);
contents[].parts[].fileData.fileUri (apis/gemini.py:61-62) -> inlineData{mimeType,data}
(apis/gemini.py:52-58). Registry key 'vertexai' (router uses provider segment),
config provider key 'gemini' (env GEMINI_*). Missing key -> 424."""
import json

from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import (
    BaseAdapter,
    http_client,
    is_bridge_asset_url,
    resolve_asset_to_base64,
)
from app.config import MissingConfig
from app.errors import missing_config_response, vendor_error_response


def _model_from_path(path: str) -> str:
    """Extract '{model}' from incoming 'gemini/{model}' (router strips the leading
    '/proxy/vertexai/' provider segment). Fallback to last path segment if the
    'gemini/' prefix is missing for any reason."""
    p = path.lstrip("/")
    if p.startswith("gemini/"):
        return p[len("gemini/"):]
    return p.rsplit("/", 1)[-1]


def _part_has_data(part: dict) -> bool:
    """True if a Gemini part carries a usable data oneof (text/inlineData/fileData)."""
    if not isinstance(part, dict):
        return False
    if part.get("inlineData") or part.get("fileData"):
        return True
    text = part.get("text")
    return isinstance(text, str) and text.strip() != ""


def _rewrite_body(body: dict) -> dict:
    """Drop uploadImagesToStorage; rewrite bridge-asset fileData parts to inlineData; drop
    empty parts. Non-bridge fileUri (e.g. gs:// / public GCS) and non-fileData parts
    (text, existing inlineData) are left untouched."""
    body.pop("uploadImagesToStorage", None)
    # Strip the redundant default imageOutputOptions. ComfyUI's GeminiImageConfig always
    # emits imageOutputOptions={"mimeType":"image/png"} (pydantic default_factory; no node
    # widget sets it), but Google AI Studio's generativelanguage API rejects the field on
    # every version (v1/v1beta/v1alpha) — it is a Vertex-only field. PNG is already Google's
    # default, so dropping the default-valued field is a semantic no-op on upstreams that
    # accept it (Vertex / leihuo gateway) and unblocks those that don't (AI Studio via
    # litellm). A genuine non-default request (jpeg / compressionQuality) is preserved.
    gc = body.get("generationConfig")
    if isinstance(gc, dict):
        ic = gc.get("imageConfig")
        if isinstance(ic, dict):
            opts = ic.get("imageOutputOptions")
            if (
                isinstance(opts, dict)
                and opts.get("mimeType", "image/png") == "image/png"
                and not opts.get("compressionQuality")
            ):
                ic.pop("imageOutputOptions", None)
    for content in body.get("contents", []) or []:
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            fd = part.get("fileData")
            if not isinstance(fd, dict):
                continue
            uri = fd.get("fileUri", "")
            if not is_bridge_asset_url(uri):
                continue
            b64, media_type = resolve_asset_to_base64(uri)
            mime = fd.get("mimeType") or media_type
            del part["fileData"]
            part["inlineData"] = {"mimeType": mime, "data": b64}
        # Drop empty parts. GeminiNode always prepends a text part from the `prompt` widget;
        # when that widget is blank (instructions live in system_prompt instead), it sends
        # {"text": ""}, which Gemini rejects with "parts[].data: required oneof field 'data'
        # must have one initialized field". comfy.org strips these server-side; mirror that.
        kept = [p for p in parts if _part_has_data(p)]
        if kept != parts:
            content["parts"] = kept
    return body


class GeminiAdapter(BaseAdapter):
    provider = "gemini"

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        try:
            key = self.key()
        except MissingConfig as e:
            return missing_config_response(str(e))
        model = _model_from_path(path)
        url = f"{self.base()}/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": key, "content-type": "application/json"}
        body = _rewrite_body(json.loads(raw) if raw else {})

        resp = await http_client().post(url, json=body, headers=headers)

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            return vendor_error_response(resp.status_code, err)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )


register("vertexai", GeminiAdapter())
