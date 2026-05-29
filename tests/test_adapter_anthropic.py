"""Anthropic adapter tests (spec §4/§5): x-api-key + anthropic-version injection,
messages[].content[].source.url -> base64 rewrite, missing-key 424, non-bridge URL passthrough."""
import base64
import importlib
import os
import uuid

import httpx
import respx
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    env.setdefault("ANTHROPIC_API_KEY", "ak-test")
    env.setdefault("ANTHROPIC_VERSION", "2023-06-01")
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    from app.adapters import anthropic as an_mod
    importlib.reload(an_mod)  # top-level register("anthropic", ...) runs on import
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app), assets_mod


def _seed(assets_mod, tmp_path, data=b"IMG", mime="image/png"):
    aid = uuid.uuid4().hex
    path = os.path.join(str(tmp_path), aid)
    with open(path, "wb") as f:
        f.write(data)
    assets_mod._REGISTRY[aid] = assets_mod.AssetRecord(
        asset_id=aid, file_name="x.png", media_type=mime, path=path
    )
    return aid


@respx.mock
def test_injects_headers_and_rewrites_source_url(monkeypatch, tmp_path):
    captured = {}

    def _resp(request):
        captured["x-api-key"] = request.headers.get("x-api-key", "")
        captured["anthropic-version"] = request.headers.get("anthropic-version", "")
        import json as _j
        captured["body"] = _j.loads(request.content)
        return httpx.Response(200, json={"id": "msg_1", "content": [{"type": "text", "text": "ok"}]})

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_resp)
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path, data=b"HELLO", mime="image/jpeg")
    body = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "url", "url": f"http://127.0.0.1:8189/asset/{aid}"}},
            {"type": "text", "text": "describe"},
        ]}],
    }
    r = c.post("/proxy/anthropic/v1/messages", json=body)
    assert r.status_code == 200
    assert captured["x-api-key"] == "ak-test"
    assert captured["anthropic-version"] == "2023-06-01"
    src = captured["body"]["messages"][0]["content"][0]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "image/jpeg"
    assert base64.b64decode(src["data"]) == b"HELLO"
    # text part untouched
    assert captured["body"]["messages"][0]["content"][1] == {"type": "text", "text": "describe"}


@respx.mock
def test_non_bridge_url_passthrough(monkeypatch, tmp_path):
    """Vendor public CDN URL must be left as-is (not rewritten to base64)."""
    captured = {}

    def _resp(request):
        import json as _j
        captured["body"] = _j.loads(request.content)
        return httpx.Response(200, json={"id": "msg_2", "content": []})

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_resp)
    c, _assets = _client(monkeypatch, tmp_path)
    body = {
        "model": "claude-opus-4-7",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "url", "url": "https://cdn.example.com/cat.png"}},
        ]}],
    }
    r = c.post("/proxy/anthropic/v1/messages", json=body)
    assert r.status_code == 200
    src = captured["body"]["messages"][0]["content"][0]["source"]
    assert src == {"type": "url", "url": "https://cdn.example.com/cat.png"}


def test_missing_key_returns_424(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path, ANTHROPIC_API_KEY="")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = c.post("/proxy/anthropic/v1/messages", json={"model": "x", "max_tokens": 1, "messages": []})
    assert r.status_code == 424
    assert "anthropic" in r.json()["error"]["message"]
