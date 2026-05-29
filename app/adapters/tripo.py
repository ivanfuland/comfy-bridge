"""Tripo adapter (spec §4/§5). Endpoints:
  POST v2/openapi/task          -> {base}/v2/openapi/task (body rewrite below)
  GET  v2/openapi/task/{id}     -> {base}/v2/openapi/task/{id} (passthrough poll, nodes_tripo.py:51)
auth: Authorization: Bearer TRIPO_API_KEY on all outgoing calls (task POST, GET poll, upload POST).
Body rewrite for POST task:
  - single image: body['file'] = {url,type} (nodes_tripo.py:285-289, apis/tripo.py:130-146) ->
    if url is a bridge asset, POST {base}/v2/openapi/upload (multipart) to swap for a file_token,
    then body['file'] = {type, file_token} (url field dropped). Non-bridge urls are left intact.
  - multiview: body['files'] = [{url,type} | {}, ...] (nodes_tripo.py:420-440); each non-empty
    dict with a bridge url is uploaded as above; empty {} (TripoFileEmptyReference, apis/tripo.py)
    is kept verbatim AND order is preserved (Tripo treats list index as view position; reorder
    or drop would silently corrupt the 3D reconstruction).
Upload sub-request errors -> vendor_error_response(status, body): this is the first adapter
that calls vendor on its own initiative, so we surface the upstream status rather than let
an httpx exception bubble to 500."""
import json

import httpx
from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import (
    BaseAdapter,
    http_client,
    is_bridge_asset_url,
    resolve_asset_bytes,
)
from app.config import MissingConfig
from app.errors import missing_config_response, vendor_error_response


def _sanitize_task_response(content: bytes) -> bytes:
    """Drop empty-string status/type from a Tripo task response's `data`.

    Some gateways (e.g. new-api / one-api forks) pad the submit (POST task) response with
    `status: ""` / `type: ""` that the real Tripo API omits. ComfyUI's TripoTask
    has `status: Optional[TripoTaskStatus] = None`, so an ABSENT status validates
    fine, but the empty string fails the enum (pydantic). Removing the empty fields
    restores the real-Tripo shape. Poll (GET) responses carry a real status and pass
    through unchanged (only empty strings are stripped)."""
    try:
        payload = json.loads(content)
    except Exception:
        return content
    data = payload.get("data")
    if not isinstance(data, dict):
        return content
    changed = False
    for k in ("status", "type"):
        if data.get(k) == "":
            del data[k]
            changed = True
    return json.dumps(payload).encode() if changed else content


async def _upload_to_token(
    client: httpx.AsyncClient, base: str, key: str, url: str
) -> str:
    """POST {base}/v2/openapi/upload (multipart) -> data.image_token.
    Caller is responsible for catching httpx.HTTPStatusError so the upstream status
    can be surfaced via vendor_error_response."""
    data, media_type = resolve_asset_bytes(url)
    ext = (media_type.split("/")[-1] or "jpeg").lower()
    files = {"file": (f"upload.{ext}", data, media_type)}
    resp = await client.post(
        f"{base}/v2/openapi/upload",
        headers={"Authorization": f"Bearer {key}"},
        files=files,
    )
    resp.raise_for_status()
    payload = resp.json()
    # Tripo /v2/openapi/upload returns {"code":0,"data":{"image_token":"..."}}.
    # Field name 'image_token' to be reconfirmed via Task 2 capture if API drifts.
    return payload["data"]["image_token"]


class TripoAdapter(BaseAdapter):
    provider = "tripo"

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        try:
            key = self.key()
        except MissingConfig as e:
            return missing_config_response(str(e))
        base = self.base()
        url = base + "/" + path.lstrip("/")
        headers = {"Authorization": f"Bearer {key}"}
        client = http_client()

        if request.method == "GET":
            resp = await client.get(
                url, params=dict(request.query_params), headers=headers
            )
        else:
            body = json.loads(raw) if raw else {}
            try:
                body = await self._rewrite_body(client, base, key, body)
            except httpx.HTTPStatusError as e:
                # Upload sub-request failed: surface upstream status instead of 500.
                try:
                    err_body = e.response.json()
                except Exception:
                    err_body = e.response.text
                return vendor_error_response(e.response.status_code, err_body)
            headers["content-type"] = "application/json"
            resp = await client.post(url, json=body, headers=headers)

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            return vendor_error_response(resp.status_code, err)
        return Response(
            content=_sanitize_task_response(resp.content),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )

    async def _rewrite_body(
        self, client: httpx.AsyncClient, base: str, key: str, body: dict
    ) -> dict:
        """Swap bridge-asset urls in body['file'] (single) and body['files'] (multiview)
        for Tripo file_token. base.rewrite_list is NOT used here because it resolves to
        base64; Tripo needs raw bytes for the multipart upload sub-request."""
        # single image: body['file'] = {url, type}
        f = body.get("file")
        if isinstance(f, dict) and is_bridge_asset_url(f.get("url", "")):
            token = await _upload_to_token(client, base, key, f["url"])
            body["file"] = {"type": f.get("type", "jpeg"), "file_token": token}
        # multiview: body['files'] = [{url,type} | {} | non-bridge-dict, ...]
        files_list = body.get("files")
        if isinstance(files_list, list):
            resolved = []
            for el in files_list:
                if (
                    isinstance(el, dict)
                    and el.get("url")
                    and is_bridge_asset_url(el["url"])
                ):
                    token = await _upload_to_token(client, base, key, el["url"])
                    resolved.append(
                        {"type": el.get("type", "jpeg"), "file_token": token}
                    )
                else:
                    # empty {} (TripoFileEmptyReference) and non-bridge urls kept verbatim;
                    # order preserved (Tripo uses list index as view position).
                    resolved.append(el)
            body["files"] = resolved
        return body


register("tripo", TripoAdapter())
