"""OpenAI adapter (spec §4). base = origin root; adapter appends /v1 and strips a trailing
/v1 from the configured base to avoid double /v1. Endpoints:
  POST v1/responses              -> {base}/v1/responses
  GET  v1/responses/{id}         -> {base}/v1/responses/{id}   (poll; nodes_openai.py:1170-1176)
  POST images/generations        -> {base}/v1/images/generations
  POST images/edits (multipart)  -> {base}/v1/images/edits     (raw multipart passthrough,
                                                                only swap host + inject key)
auth: Authorization: Bearer OPENAI_API_KEY. Missing key -> MissingConfig -> 424.

Responses-poll shim: ComfyUI OpenAIChatNode always does POST /v1/responses (create) then
GET /v1/responses/{id} (poll), but stream=false makes the create already return a terminal
(status=completed/incomplete) body with the full output. Some upstream gateways (e.g. the
New-API-based leihuo gateway) implement the create POST but NOT the retrieve GET, returning
404 'Invalid URL (GET /v1/responses/{id})'. We cache terminal create responses by id and
serve the poll GET from cache, so the node's poll succeeds without hitting the missing
upstream route. Falls through to upstream GET on cache miss (preserves behavior for gateways
that DO implement retrieve)."""
from collections import OrderedDict

from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import BaseAdapter, http_client
from app.config import MissingConfig
from app.errors import missing_config_response, vendor_error_response

# Bounded LRU cache of terminal create-response bodies, keyed by response id.
# Responses are created then immediately polled, so a small cap is plenty.
_RESPONSE_CACHE: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
_RESPONSE_CACHE_MAX = 256
# Statuses the node's poll_op treats as terminal (nodes_openai.py:1175); only these are
# worth caching — caching a non-terminal status would trap the node in a poll loop.
_TERMINAL_STATUSES = {"completed", "incomplete"}


def _cache_put(rid: str, content: bytes, media_type: str) -> None:
    _RESPONSE_CACHE[rid] = (content, media_type)
    _RESPONSE_CACHE.move_to_end(rid)
    while len(_RESPONSE_CACHE) > _RESPONSE_CACHE_MAX:
        _RESPONSE_CACHE.popitem(last=False)


def _response_id_from_get_path(path: str) -> str | None:
    """'v1/responses/resp_xxx' -> 'resp_xxx'; anything else -> None."""
    p = path.lstrip("/")
    prefix = "v1/responses/"
    if p.startswith(prefix):
        rid = p[len(prefix):]
        return rid or None
    return None


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
            # Serve responses poll from cache (upstream may lack the retrieve route).
            rid = _response_id_from_get_path(path)
            if rid is not None and rid in _RESPONSE_CACHE:
                content, media_type = _RESPONSE_CACHE[rid]
                _RESPONSE_CACHE.move_to_end(rid)
                return Response(content=content, status_code=200, media_type=media_type)
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

        media_type = resp.headers.get("content-type", "application/json")
        # Cache a terminal create response so the node's subsequent poll GET can be served
        # from cache even if upstream doesn't implement GET /v1/responses/{id}.
        if request.method == "POST" and path.lstrip("/").rstrip("/") == "v1/responses":
            try:
                body = resp.json()
                rid = body.get("id")
                if rid and body.get("status") in _TERMINAL_STATUSES:
                    _cache_put(rid, resp.content, media_type)
            except Exception:
                pass

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=media_type,
        )


register("openai", OpenAIAdapter())
