import os
import json
from fastapi.testclient import TestClient


def _client(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import importlib
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_capture_mode_records_request(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_CAPTURE", "1")
    monkeypatch.setenv("BRIDGE_CAPTURE_DIR", str(tmp_path))
    c = _client(monkeypatch)
    r = c.post("/proxy/openai/v1/responses", json={"model": "gpt-5", "input": "hi"})
    # capture mode is OFFLINE inspection only -> 503 explicit stop, never fake-success 200
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["type"] == "comfy_bridge_capture"
    assert body["captured"]["method"] == "POST"
    assert body["captured"]["path"] == "v1/responses"
    assert body["captured"]["provider"] == "openai"
    files = list(tmp_path.glob("*.json"))
    assert files, "capture file written"


def test_cors_preflight_allowed_origin(monkeypatch):
    c = _client(monkeypatch, BRIDGE_CORS_ORIGINS="http://127.0.0.1:8188,http://localhost:8188")
    r = c.options(
        "/comfy-bridge/gating",
        headers={
            "Origin": "http://localhost:8188",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8188"


def test_cors_disallowed_origin_not_reflected(monkeypatch):
    c = _client(monkeypatch, BRIDGE_CORS_ORIGINS="http://127.0.0.1:8188")
    r = c.options(
        "/comfy-bridge/gating",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") != "http://evil.example"


def test_gating_endpoint_on(monkeypatch):
    c = _client(monkeypatch, BRIDGE_GATING="on")
    r = c.get("/comfy-bridge/gating")
    assert r.status_code == 200
    body = r.json()
    assert body["gating_enabled"] is True
    # vendor tier (coarse): non-listed vendors get hidden client-side
    assert isinstance(body["allowed_vendors"], list)
    assert set(body["allowed_vendors"]) == {"openai", "anthropic", "gemini", "tripo"}
    # class tier (fine): vendor allowed but class not -> greyed "未适配"
    assert isinstance(body["allowed_node_classes"], list)
    assert "ClaudeNode" in body["allowed_node_classes"]
    # corrected real class names (had typo OpenAIImage / GeminiImage in earlier rev)
    assert "OpenAIImage" not in body["allowed_node_classes"]
    assert "GeminiImageNode" in body["allowed_node_classes"]
    assert "GeminiImage2Node" in body["allowed_node_classes"]
    # per-class hard-hide denylist (default empty; set via BRIDGE_HIDDEN_NODE_CLASSES)
    assert isinstance(body["hidden_node_classes"], list)


def test_gating_hidden_node_classes_override(monkeypatch):
    c = _client(monkeypatch, BRIDGE_GATING="on", BRIDGE_HIDDEN_NODE_CLASSES="OpenAIDalle2,OpenAIDalle3")
    body = c.get("/comfy-bridge/gating").json()
    assert body["hidden_node_classes"] == ["OpenAIDalle2", "OpenAIDalle3"]


def test_gating_endpoint_off(monkeypatch):
    c = _client(monkeypatch, BRIDGE_GATING="off")
    r = c.get("/comfy-bridge/gating")
    assert r.status_code == 200
    assert r.json()["gating_enabled"] is False
