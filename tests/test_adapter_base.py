import base64
import importlib

import pytest


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGE_PORT", "8189")
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    return assets_mod, base_mod


def _seed_asset(assets_mod, tmp_path, data=b"IMG", mime="image/png"):
    import uuid, os
    aid = uuid.uuid4().hex
    path = os.path.join(str(tmp_path), aid)
    with open(path, "wb") as f:
        f.write(data)
    assets_mod._REGISTRY[aid] = assets_mod.AssetRecord(asset_id=aid, file_name="x.png", media_type=mime, path=path)
    return aid


def test_is_bridge_asset_url(monkeypatch, tmp_path):
    _, base_mod = _mod(monkeypatch, tmp_path)
    assert base_mod.is_bridge_asset_url("http://127.0.0.1:8189/asset/abc") is True
    assert base_mod.is_bridge_asset_url("https://cdn.tripo3d.ai/x.glb") is False


def test_resolve_asset_to_base64(monkeypatch, tmp_path):
    assets_mod, base_mod = _mod(monkeypatch, tmp_path)
    aid = _seed_asset(assets_mod, tmp_path, data=b"HELLO", mime="image/jpeg")
    url = f"http://127.0.0.1:8189/asset/{aid}"
    data_b64, media_type = base_mod.resolve_asset_to_base64(url)
    assert base64.b64decode(data_b64) == b"HELLO"
    assert media_type == "image/jpeg"


def test_resolve_unknown_asset_raises(monkeypatch, tmp_path):
    _, base_mod = _mod(monkeypatch, tmp_path)
    with pytest.raises(base_mod.AssetNotFound):
        base_mod.resolve_asset_to_base64("http://127.0.0.1:8189/asset/nope")


def test_rewrite_list_preserves_order_and_empties(monkeypatch, tmp_path):
    """multiview files[]: rewrite url elements, keep empty {} in place, preserve order."""
    assets_mod, base_mod = _mod(monkeypatch, tmp_path)
    aid = _seed_asset(assets_mod, tmp_path)
    items = [{"url": f"http://127.0.0.1:8189/asset/{aid}", "type": "jpeg"}, {}, {"url": f"http://127.0.0.1:8189/asset/{aid}"}]
    out = base_mod.rewrite_list(items, lambda el, b64, mt: {"file_token": "TOK", "type": el.get("type", "jpeg")})
    assert out[1] == {}
    assert out[0]["file_token"] == "TOK"
    assert out[2]["file_token"] == "TOK"
    assert len(out) == 3
