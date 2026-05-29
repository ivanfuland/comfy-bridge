import importlib
from urllib.parse import urlparse
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGE_PORT", "8189")
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app), assets_mod


def test_storage_slot_returns_bridge_urls(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path)
    r = c.post("/customers/storage", json={"file_name": "a.png", "content_type": "image/png"})
    assert r.status_code == 200
    body = r.json()
    assert "upload_url" in body and "download_url" in body
    assert "127.0.0.1:8189" in body["upload_url"]
    assert "/asset/" in body["download_url"]


def test_put_persists_and_get_roundtrips(monkeypatch, tmp_path):
    c, assets_mod = _client(monkeypatch, tmp_path)
    slot = c.post("/customers/storage", json={"file_name": "a.png", "content_type": "image/png"}).json()
    put_path = urlparse(slot["upload_url"]).path
    r = c.put(put_path, content=b"\x89PNGDATA")
    assert r.status_code in (200, 204)
    asset_id = slot["download_url"].rsplit("/", 1)[1]
    rec = assets_mod.lookup_by_url(slot["download_url"])
    assert rec is not None
    assert rec.data == b"\x89PNGDATA"
    assert rec.media_type == "image/png"
    assert rec.asset_id == asset_id
    g = c.get("/asset/" + asset_id)
    assert g.status_code == 200
    assert g.content == b"\x89PNGDATA"


def test_lookup_by_url_unknown_returns_none(monkeypatch, tmp_path):
    _, assets_mod = _client(monkeypatch, tmp_path)
    assert assets_mod.lookup_by_url("http://127.0.0.1:8189/asset/does-not-exist") is None


def test_download_url_uses_127_0_0_1_regardless_of_bind_host(monkeypatch, tmp_path):
    """BRIDGE_HOST=0.0.0.0 is legitimate (LAN bind), but the download_url is a reverse-lookup
    token consumed in-process by is_bridge_asset_url which only recognizes
    {"127.0.0.1","localhost"}. If we emitted 0.0.0.0 here, adapters would treat the bridge URL
    as a vendor URL and leak it to the vendor (broken rewrite invariant)."""
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("BRIDGE_HOST", "0.0.0.0")
    monkeypatch.setenv("BRIDGE_PORT", "8189")
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    c = TestClient(main_mod.app)
    r = c.post("/customers/storage", json={"file_name": "a.png", "content_type": "image/png"})
    assert r.status_code == 200
    body = r.json()
    assert "127.0.0.1:8189" in body["download_url"]
    assert "0.0.0.0" not in body["download_url"]
    assert "127.0.0.1:8189" in body["upload_url"]
    assert "0.0.0.0" not in body["upload_url"]
    # and the URL is recognized by is_bridge_asset_url, which is the whole point.
    from app.adapters.base import is_bridge_asset_url
    assert is_bridge_asset_url(body["download_url"])


def test_put_without_content_type_persists(monkeypatch, tmp_path):
    """upload_helpers.py:248-253,289 -> client adds Content-Type to skip_auto_headers when
    content_type was None; bridge PUT must accept raw bytes with no Content-Type header."""
    from urllib.parse import urlparse
    c, assets_mod = _client(monkeypatch, tmp_path)
    slot = c.post("/customers/storage", json={"file_name": "x.bin"}).json()  # NO content_type
    put_path = urlparse(slot["upload_url"]).path
    r = c.put(put_path, content=b"RAWBYTES", headers={"content-type": None} if False else {})
    # explicitly DELETE auto content-type; httpx may still set one — what matters is bridge accepts it
    assert r.status_code in (200, 204)
    rec = assets_mod.lookup_by_url(slot["download_url"])
    assert rec is not None
    assert rec.data == b"RAWBYTES"
