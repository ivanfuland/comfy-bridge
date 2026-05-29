"""Anthropic adapter (spec §4/§5). POST v1/messages -> {base}/v1/messages.
Inject x-api-key + anthropic-version (node sends neither; apis/anthropic.py has no version field).
Rewrite messages[].content[].source.url (AnthropicImageSourceUrl, nodes_anthropic.py:147)
into base64 source{type, media_type, data} (apis/anthropic.py:17-20). Missing key -> 424."""
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


def _rewrite_body(body: dict) -> dict:
    """In-place rewrite of messages[].content[].source.url -> base64 for bridge assets.
    Non-bridge URLs (vendor public CDN) and already-base64 sources are left untouched."""
    for msg in body.get("messages", []) or []:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image":
                continue
            src = part.get("source")
            if not isinstance(src, dict) or src.get("type") != "url":
                continue
            url = src.get("url", "")
            if is_bridge_asset_url(url):
                b64, media_type = resolve_asset_to_base64(url)
                part["source"] = {"type": "base64", "media_type": media_type, "data": b64}
    return body


class AnthropicAdapter(BaseAdapter):
    provider = "anthropic"

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        try:
            key = self.key()
        except MissingConfig as e:
            return missing_config_response(str(e))
        url = self.base() + "/" + path.lstrip("/")  # path == 'v1/messages'
        headers = {
            "x-api-key": key,
            "anthropic-version": self.cfg.anthropic_version,
            "content-type": "application/json",
        }
        body = json.loads(raw) if raw else {}
        body = _rewrite_body(body)

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


register("anthropic", AnthropicAdapter())
