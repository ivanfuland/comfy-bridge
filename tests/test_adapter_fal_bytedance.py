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
