"""End-to-end integration tests (spec §9 / plan Task 11).

These exercise the FULL app via TestClient — no manual adapter `register()` calls. The
helper reloads the adapter package (which resets `_LOADED` and `_REGISTRY`) plus each
adapter module so their top-level `register()` re-runs against the fresh registry, then
reloads `app.main` so `create_app()` rebuilds with the current config/registry.

Coverage (3 flows per plan §11):
  1. OpenAI text — POST /v1/responses + GET poll passthrough.
  2. Anthropic image — storage slot POST -> PUT raw bytes -> /v1/messages with
     bridge download_url; assert vendor receives source.type=base64 with original bytes.
  3. Tripo image_to_model — storage slot POST -> PUT raw bytes -> /v2/openapi/task with
     file.url=bridge download_url; assert vendor receives file.file_token (and no url).
"""
import base64
import importlib
import json
from urllib.parse import urlparse

import httpx
import respx
from fastapi.testclient import TestClient


def _full_client(monkeypatch, tmp_path):
    """Build a TestClient against the full app with all 4 adapters wired via load_adapters()."""
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    monkeypatch.setenv("BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("BRIDGE_PORT", "8189")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
    monkeypatch.setenv("TRIPO_API_KEY", "tk-test")
    monkeypatch.setenv("ANTHROPIC_VERSION", "2023-06-01")
    # Reload adapter package first (resets _LOADED=False and _REGISTRY={}), then each
    # adapter module so its top-level register() re-runs against the fresh registry.
    # Finally reload main so create_app() picks up current config + calls load_adapters().
    for m in (
        "app.config",
        "app.assets",
        "app.adapters",          # resets _LOADED / _REGISTRY
        "app.adapters.base",
        "app.adapters.openai",   # top-level register("openai", ...)
        "app.adapters.anthropic",
        "app.adapters.gemini",
        "app.adapters.tripo",
        "app.main",
    ):
        importlib.reload(importlib.import_module(m))
    from app import main as main_mod
    return TestClient(main_mod.app)


@respx.mock
def test_e2e_openai_text_create_and_poll(monkeypatch, tmp_path):
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(200, json={"id": "resp_X", "status": "queued"})
    )
    respx.get("https://api.openai.com/v1/responses/resp_X").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp_X",
                "status": "completed",
                "output": [{"content": [{"text": "hello"}]}],
            },
        )
    )
    c = _full_client(monkeypatch, tmp_path)
    created = c.post("/proxy/openai/v1/responses", json={"model": "gpt-5", "input": "hi"}).json()
    assert created["id"] == "resp_X"
    assert created["status"] == "queued"
    polled = c.get(f"/proxy/openai/v1/responses/{created['id']}").json()
    assert polled["status"] == "completed"


@respx.mock
def test_e2e_anthropic_image_url_to_base64(monkeypatch, tmp_path):
    captured = {}

    def _resp(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"id": "m1", "content": [{"type": "text", "text": "a cat"}]}
        )

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_resp)
    c = _full_client(monkeypatch, tmp_path)
    # Node flow: create storage slot -> raw PUT bytes -> send messages referencing download_url
    slot = c.post(
        "/customers/storage", json={"file_name": "img.png", "content_type": "image/png"}
    ).json()
    put_path = urlparse(slot["upload_url"]).path
    put_resp = c.put(put_path, content=b"PNGBYTES")
    assert put_resp.status_code == 200
    body = {
        "model": "claude-opus-4-7",
        "max_tokens": 512,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": slot["download_url"]}},
                    {"type": "text", "text": "what?"},
                ],
            }
        ],
    }
    r = c.post("/proxy/anthropic/v1/messages", json=body)
    assert r.status_code == 200
    src = captured["body"]["messages"][0]["content"][0]["source"]
    assert src["type"] == "base64"
    assert base64.b64decode(src["data"]) == b"PNGBYTES"


@respx.mock
def test_e2e_tripo_image_to_model_upload_swap(monkeypatch, tmp_path):
    respx.post("https://api.tripo3d.ai/v2/openapi/upload").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"image_token": "ITOK"}})
    )
    task_cap = {}

    def _task(request):
        task_cap["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "tt"}})

    respx.post("https://api.tripo3d.ai/v2/openapi/task").mock(side_effect=_task)
    c = _full_client(monkeypatch, tmp_path)
    slot = c.post(
        "/customers/storage", json={"file_name": "v.jpg", "content_type": "image/jpeg"}
    ).json()
    put_path = urlparse(slot["upload_url"]).path
    put_resp = c.put(put_path, content=b"JPEGBYTES")
    assert put_resp.status_code == 200
    body = {"type": "image_to_model", "file": {"type": "jpeg", "url": slot["download_url"]}}
    r = c.post("/proxy/tripo/v2/openapi/task", json=body)
    assert r.status_code == 200
    assert task_cap["body"]["file"]["file_token"] == "ITOK"
    assert "url" not in task_cap["body"]["file"]
