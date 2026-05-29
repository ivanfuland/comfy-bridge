"""OpenAI adapter (spec §4). base = origin root; adapter appends /v1 and strips a trailing
/v1 from the configured base to avoid double /v1. Endpoints:
  POST v1/responses              -> {base}/v1/responses
  GET  v1/responses/{id}         -> {base}/v1/responses/{id}   (poll; nodes_openai.py:1170-1176)
  POST images/generations        -> {base}/v1/images/generations
  POST images/edits (multipart)  -> {base}/v1/images/edits     (raw multipart passthrough,
                                                                only swap host + inject key)
auth: Authorization: Bearer OPENAI_API_KEY. Missing key -> MissingConfig -> 424."""
from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import BaseAdapter, http_client
from app.config import MissingConfig
from app.errors import missing_config_response, vendor_error_response


def _normalize_base(base: str) -> str:
    """Strip trailing slash + a single trailing /v1, so configured base
    'https://llm.example.com/v1' and 'https://llm.example.com' both work."""
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _target_path(incoming_path: str) -> str:
    """Map incoming proxy path to vendor path under /v1.
    Examples: 'v1/responses' -> '/v1/responses', 'images/generations' -> '/v1/images/generations'."""
    p = incoming_path.lstrip("/")
    if p.startswith("v1/"):
        return "/" + p
    return "/v1/" + p


class OpenAIAdapter(BaseAdapter):
    provider = "openai"

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        try:
            key = self.key()
        except MissingConfig as e:
            return missing_config_response(str(e))
        url = _normalize_base(self.base()) + _target_path(path)
        headers = {"Authorization": f"Bearer {key}"}
        client = http_client()

        if request.method == "GET":
            resp = await client.get(url, params=dict(request.query_params), headers=headers)
        elif "multipart/form-data" in request.headers.get("content-type", ""):
            # raw multipart passthrough: forward body + content-type verbatim,
            # only swap host + inject key, do NOT scan/rewrite body
            fwd_headers = dict(headers)
            fwd_headers["content-type"] = request.headers["content-type"]
            resp = await client.post(url, content=raw, headers=fwd_headers)
        else:
            headers["content-type"] = "application/json"
            resp = await client.post(url, content=raw, headers=headers)

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return vendor_error_response(resp.status_code, body)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )


register("openai", OpenAIAdapter())
