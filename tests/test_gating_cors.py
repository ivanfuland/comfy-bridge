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
    # NB: ByteDance's gating vendor is "bytedance" (from python_module nodes_bytedance),
    # distinct from the byteplus/byteplus-seedance2 route segments the adapter registers.
    assert set(body["allowed_vendors"]) == {"openai", "anthropic", "gemini", "tripo", "bytedance"}
    # the per-class allowlist / "未适配" grey tier was removed: the endpoint no longer
    # serves allowed_node_classes — a node is either shown or hidden.
    assert "allowed_node_classes" not in body
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


def test_gating_exposes_capability_managed_and_loaded_segments(monkeypatch):
    c = _client(monkeypatch, BRIDGE_GATING="on")
    body = c.get("/comfy-bridge/gating").json()
    assert "capability_managed_node_classes" in body
    assert "ByteDanceCreateImageAsset" in body["capability_managed_node_classes"]
    assert "OpenAIInputFiles" not in body["capability_managed_node_classes"]
    assert "loaded_segments" in body
    assert isinstance(body["loaded_segments"], list)


def test_loaded_segments_requires_full_route_keys(monkeypatch):
    # byteplus 的 expected_route_keys = [byteplus, byteplus-seedance2, seedance]（Codex plan #3 / spec §6 9b）
    from app import adapters
    c = _client(monkeypatch, BRIDGE_GATING="on")          # 先 reload，再 monkeypatch（gating() 函数内 from-import 取当前值）
    monkeypatch.setattr(adapters, "_LOADED_BACKEND_CHOICES", {"byteplus": "native"}, raising=False)
    monkeypatch.setattr(adapters, "_REGISTRY", {"byteplus": object()}, raising=False)
    assert "bytedance" not in c.get("/comfy-bridge/gating").json()["loaded_segments"]
    monkeypatch.setattr(
        adapters, "_REGISTRY",
        {"byteplus": object(), "byteplus-seedance2": object(), "seedance": object()},
        raising=False,
    )
    assert "bytedance" in c.get("/comfy-bridge/gating").json()["loaded_segments"]
