"""fal-ai bytedance adapter tests (respx mock fal). 0 token."""
import json
import sys
import pytest
import respx
import httpx
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    monkeypatch.setenv("FAL_KEY", "test-key")
    import app.adapters as A
    A._REGISTRY.clear(); A._LOADED_BACKEND_CHOICES.clear(); A._LOADED = False
    for name in list(sys.modules):
        if name.startswith("app.adapters.") and name != "app.adapters.base":
            del sys.modules[name]
    A.load_adapters()
    from app.main import app
    return TestClient(app)


@respx.mock
def test_t2v_create_translates_to_fal_submit(client):
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/text-to-video").mock(
        return_value=httpx.Response(200, json={"request_id": "req-9"}))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "a cat --resolution 720p --ratio adaptive --duration 5"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    assert sub.called
    sent = json.loads(sub.calls[0].request.content)
    assert sent["prompt"].startswith("a cat")
    assert sent["aspect_ratio"] == "auto"          # adaptive normalized
    assert sent["resolution"] == "720p"
    assert sent["duration"] == "5"
    from app.adapters.fal_ai._models import decode_task_id
    ep, rid = decode_task_id(r.json()["id"])
    assert ep == "bytedance/seedance-2.0/text-to-video" and rid == "req-9"


def test_unsupported_model_returns_424(client):
    body = {"model": "seedance-1-0-pro-250528",  # 1.x not on fal-ai
            "content": [{"text": "x"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 424


@respx.mock
def test_fal_upstream_error_passthrough(client):
    respx.post("https://queue.fal.run/bytedance/seedance-2.0/text-to-video").mock(
        return_value=httpx.Response(429, json={"error": "rate limited"}))
    body = {"model": "dreamina-seedance-2-0-260128", "content": [{"text": "a cat"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 429   # fal status passed through


def test_media_content_deferred_424(client):
    # content with a media item (image) currently returns 424 (i2v/ref is a later task)
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "x"}, {"type": "image_url", "image_url": {"url": "asset://a"}}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 424


# ── seedance asset shim (Task 6): upload -> bridge cache, GET Active/Failed, resolve ──
import uuid


def _seed_bridge_asset(data=b"FALFRAME", mime="image/png"):
    """Put bytes into the bridge asset cache (the same store the node fills via
    /customers/storage + PUT) and return its bridge download_url."""
    from app import assets as assets_mod
    aid = uuid.uuid4().hex
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), f"cc-fal-asset-{aid}")
    with open(path, "wb") as f:
        f.write(data)
    assets_mod._REGISTRY[aid] = assets_mod.AssetRecord(
        asset_id=aid, file_name="x.png", media_type=mime, path=path
    )
    return f"http://127.0.0.1:8190/asset/{aid}"


def test_virtual_library_upload_returns_asset_id(client):
    bridge_url = _seed_bridge_asset()
    r = client.post("/proxy/seedance/virtual-library/assets",
                    json={"url": bridge_url, "hash": "deadbeef"})
    assert r.status_code == 200
    asset_id = r.json()["asset_id"]
    assert isinstance(asset_id, str) and asset_id


def test_get_unknown_asset_returns_failed(client):
    r = client.get("/proxy/seedance/assets/nonexistent-id")
    assert r.status_code == 200
    assert r.json()["status"] == "Failed"  # NOT Active for an unknown id


def test_get_known_asset_returns_active(client):
    bridge_url = _seed_bridge_asset()
    asset_id = client.post(
        "/proxy/seedance/virtual-library/assets",
        json={"url": bridge_url, "asset_type": "Image"},
    ).json()["asset_id"]
    r = client.get(f"/proxy/seedance/assets/{asset_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "Active"
    assert r.json()["asset_type"] == "Image"


def test_assets_helper_post_also_stores(client):
    # The optional /proxy/seedance/assets POST (asset-helper nodes) stores + returns id.
    bridge_url = _seed_bridge_asset()
    asset_id = client.post(
        "/proxy/seedance/assets",
        json={"group_id": "g1", "url": bridge_url, "asset_type": "Image", "name": "n"},
    ).json()["asset_id"]
    assert client.get(f"/proxy/seedance/assets/{asset_id}").json()["status"] == "Active"


@respx.mock
def test_resolve_to_fal_url_uploads_cached_bytes(client):
    import asyncio
    from app.adapters import get_adapter
    adapter = get_adapter("seedance")

    bridge_url = _seed_bridge_asset(data=b"RESOLVEME", mime="image/png")
    asset_id = client.post(
        "/proxy/seedance/virtual-library/assets", json={"url": bridge_url}
    ).json()["asset_id"]

    # respx the 3-step fal storage upload
    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/signed-put",
            "file_url": "https://cdn.fal.run/files/resolved.png",
        })
    )
    put = respx.put("https://upload.fal.run/signed-put").mock(
        return_value=httpx.Response(200))

    url = asyncio.run(adapter._resolve_to_fal_url(f"asset://{asset_id}"))
    assert url == "https://cdn.fal.run/files/resolved.png"
    # the cached bytes were the ones uploaded to fal
    assert put.calls[0].request.content == b"RESOLVEME"


@respx.mock
def test_resolve_to_fal_url_accepts_bridge_url_directly(client):
    import asyncio
    from app.adapters import get_adapter
    adapter = get_adapter("seedance")

    bridge_url = _seed_bridge_asset(data=b"DIRECT", mime="image/jpeg")
    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/signed-put2",
            "file_url": "https://cdn.fal.run/files/direct.jpg",
        })
    )
    put = respx.put("https://upload.fal.run/signed-put2").mock(
        return_value=httpx.Response(200))

    url = asyncio.run(adapter._resolve_to_fal_url(bridge_url))
    assert url == "https://cdn.fal.run/files/direct.jpg"
    assert put.calls[0].request.content == b"DIRECT"


def test_resolve_to_fal_url_raises_for_unknown_asset(client):
    import asyncio
    from app.adapters import get_adapter
    from app.adapters.base import AssetNotFound
    adapter = get_adapter("seedance")
    with pytest.raises(AssetNotFound):
        asyncio.run(adapter._resolve_to_fal_url("asset://does-not-exist"))


def test_resolve_to_fal_url_passes_through_public_url(client):
    import asyncio
    from app.adapters import get_adapter
    adapter = get_adapter("seedance")
    out = asyncio.run(adapter._resolve_to_fal_url("https://cdn.example.com/v.mp4"))
    assert out == "https://cdn.example.com/v.mp4"   # public URL returned as-is, no upload
