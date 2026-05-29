"""OpenAI adapter tests (spec §4): endpoint normalization, key injection, /v1 strip,
GET poll, multipart raw passthrough, missing-key 424."""
import importlib

import httpx
import respx
from fastapi.testclient import TestClient


def _client(monkeypatch, **env):
    env.setdefault("OPENAI_API_KEY", "sk-test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    from app.adapters import openai as oa_mod
    importlib.reload(oa_mod)  # top-level register("openai", ...) runs on import
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_strip_base_url_double_v1():
    from app.adapters.openai import _normalize_base
    assert _normalize_base("https://llm.example.com/v1") == "https://llm.example.com"
    assert _normalize_base("https://llm.example.com/") == "https://llm.example.com"
    assert _normalize_base("https://llm.example.com") == "https://llm.example.com"


@respx.mock
def test_responses_post_injects_key_and_path(monkeypatch):
    route = respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(200, json={"id": "resp_1", "status": "queued"})
    )
    c = _client(monkeypatch)
    r = c.post("/proxy/openai/v1/responses", json={"model": "gpt-5", "input": "hi"})
    assert r.status_code == 200
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"
    assert r.json()["id"] == "resp_1"


@respx.mock
def test_responses_get_poll(monkeypatch):
    respx.get("https://api.openai.com/v1/responses/resp_1").mock(
        return_value=httpx.Response(200, json={"id": "resp_1", "status": "completed"})
    )
    c = _client(monkeypatch)
    r = c.get("/proxy/openai/v1/responses/resp_1")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


@respx.mock
def test_images_generations_with_custom_base(monkeypatch):
    route = respx.post("https://llm.example.com/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})
    )
    # trailing /v1 in configured base must be stripped before adapter re-appends /v1/
    c = _client(monkeypatch, OPENAI_BASE_URL="https://llm.example.com/v1")
    r = c.post("/proxy/openai/images/generations", json={"prompt": "cat", "model": "gpt-image-2"})
    assert r.status_code == 200
    assert route.called


@respx.mock
def test_images_edits_multipart_passthrough(monkeypatch):
    captured = {}

    def _resp(request):
        captured["ct"] = request.headers.get("content-type", "")
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})

    respx.post("https://api.openai.com/v1/images/edits").mock(side_effect=_resp)
    c = _client(monkeypatch)
    r = c.post(
        "/proxy/openai/images/edits",
        files={"image": ("image.png", b"\x89PNG", "image/png")},
        data={"model": "gpt-image-1", "prompt": "edit"},
    )
    assert r.status_code == 200
    assert "multipart/form-data" in captured["ct"]
    assert captured["auth"] == "Bearer sk-test"


def test_missing_key_returns_424(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = _client(monkeypatch, OPENAI_API_KEY="")
    r = c.post("/proxy/openai/v1/responses", json={"model": "gpt-5"})
    assert r.status_code == 424
    assert "openai" in r.json()["error"]["message"]
