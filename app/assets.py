"""Local asset slots (spec §5). Zero public URLs / no OSS.
Flow: POST /customers/storage -> {upload_url(bridge PUT), download_url(bridge /asset/{id})}
      PUT <upload_url> (raw bytes, maybe no content-type) -> persist to BRIDGE_ASSET_DIR + registry
      GET /asset/{id} -> bytes
Adapters call lookup_by_url(download_url) to fetch raw bytes for rewrite."""
import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Response

from app.config import load_config

assets_router = APIRouter()


@dataclass
class AssetRecord:
    asset_id: str
    file_name: str
    media_type: str | None
    path: str

    @property
    def data(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()


_REGISTRY: dict[str, AssetRecord] = {}


def _bridge_origin() -> str:
    # The download_url is a reverse-lookup token (resolved in-process via lookup_by_url, never
    # fetched over HTTP), so it MUST use a hostname that is_bridge_asset_url recognizes
    # ({"127.0.0.1","localhost"}) regardless of BRIDGE_HOST. If we used cfg.host=0.0.0.0
    # (legitimate for LAN bind), adapters would treat the URL as a vendor URL and leak it.
    return f"http://127.0.0.1:{load_config().port}"


def _asset_dir() -> str:
    d = load_config().asset_dir
    os.makedirs(d, exist_ok=True)
    return d


def lookup_by_url(download_url: str) -> AssetRecord | None:
    """Reverse-lookup a bridge download_url (…/asset/{id}) to its registry record.
    Handles query strings/fragments via urlparse."""
    path = urlparse(download_url).path.rstrip("/")
    asset_id = path.rsplit("/", 1)[-1] if path else ""
    return _REGISTRY.get(asset_id)


def lookup_by_id(asset_id: str) -> AssetRecord | None:
    return _REGISTRY.get(asset_id)


@assets_router.post("/customers/storage")
async def create_storage_slot(request: Request) -> dict:
    payload = await request.json()
    file_name = payload.get("file_name", "upload.bin")
    content_type = payload.get("content_type")
    asset_id = uuid.uuid4().hex
    path = os.path.join(_asset_dir(), asset_id)
    _REGISTRY[asset_id] = AssetRecord(asset_id=asset_id, file_name=file_name, media_type=content_type, path=path)
    origin = _bridge_origin()
    return {
        "upload_url": f"{origin}/bridge-upload/{asset_id}",
        "download_url": f"{origin}/asset/{asset_id}",
    }


@assets_router.put("/bridge-upload/{asset_id}")
async def put_asset(asset_id: str, request: Request) -> Response:
    rec = _REGISTRY.get(asset_id)
    if rec is None:
        return Response(status_code=404)
    data = await request.body()
    with open(rec.path, "wb") as f:
        f.write(data)
    ct = request.headers.get("content-type")
    if ct and not rec.media_type:
        rec.media_type = ct
    return Response(status_code=200)


@assets_router.get("/asset/{asset_id}")
async def get_asset(asset_id: str) -> Response:
    rec = _REGISTRY.get(asset_id)
    if rec is None or not os.path.exists(rec.path):
        return Response(status_code=404)
    return Response(content=rec.data, media_type=rec.media_type or "application/octet-stream")
