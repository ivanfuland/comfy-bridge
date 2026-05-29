"""Central handler: when an adapter resolves a stale/unknown bridge asset URL,
AssetNotFound must surface as 400 (permanent client error), NOT unhandled 500.

500 is in ComfyUI's _RETRY_STATUS={408,500,502,503,504} (util/client.py:86) and would
cause confusing retry loops on a permanent error (process restart, stale URL, etc.).

The adapter must NOT call vendor when this happens — _rewrite_body() raises before the
httpx.post(). Hence no respx mock is set: any vendor call would fail with respx's
"unmatched request" error and make this test red.
"""
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    env.setdefault("ANTHROPIC_API_KEY", "ak-test")
    env.setdefault("ANTHROPIC_VERSION", "2023-06-01")
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGE_PORT", "8189")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    from app.adapters import anthropic as an_mod
    importlib.reload(an_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_stale_bridge_asset_url_returns_400_not_500(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    stale_url = "http://127.0.0.1:8189/asset/does-not-exist"
    body = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "url", "url": stale_url}},
            {"type": "text", "text": "describe"},
        ]}],
    }
    r = c.post("/proxy/anthropic/v1/messages", json=body)
    # 400 (not 500): permanent client error, must not be retried by Comfy client.
    assert r.status_code == 400, f"expected 400, got {r.status_code} body={r.text}"
    err = r.json()["error"]
    assert err["type"] == "comfy_bridge_asset_not_found"
    # message should mention the offending URL (or at least 'asset') so the client can diagnose.
    msg = err["message"]
    assert stale_url in msg or "asset" in msg.lower()
