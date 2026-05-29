"""Tripo adapter tests (spec §4/§5): POST v2/openapi/task with image-url -> file_token
swap via sub-request to /v2/openapi/upload; multiview empty {} + order preservation;
GET v2/openapi/task/{id} passthrough poll; missing-key 424."""
import importlib
import json
import os
import uuid

import httpx
import respx
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    env.setdefault("TRIPO_API_KEY", "tk-test")
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    from app.adapters import tripo as tp_mod
    importlib.reload(tp_mod)  # top-level register("tripo", ...) runs on import
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app), assets_mod


def _seed(assets_mod, tmp_path, data=b"IMG", mime="image/jpeg"):
    aid = uuid.uuid4().hex
    path = os.path.join(str(tmp_path), aid)
    with open(path, "wb") as f:
        f.write(data)
    assets_mod._REGISTRY[aid] = assets_mod.AssetRecord(
        asset_id=aid, file_name="x.jpg", media_type=mime, path=path
    )
    return aid


@respx.mock
def test_single_image_url_to_token(monkeypatch, tmp_path):
    respx.post("https://api.tripo3d.ai/v2/openapi/upload").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"image_token": "TOK123"}})
    )
    task_captured = {}

    def _task(request):
        task_captured["body"] = json.loads(request.content)
        task_captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "t1"}})

    respx.post("https://api.tripo3d.ai/v2/openapi/task").mock(side_effect=_task)
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path)
    body = {
        "type": "image_to_model",
        "file": {"type": "jpeg", "url": f"http://127.0.0.1:8189/asset/{aid}"},
    }
    r = c.post("/proxy/tripo/v2/openapi/task", json=body)
    assert r.status_code == 200
    assert task_captured["auth"] == "Bearer tk-test"
    f = task_captured["body"]["file"]
    assert f["file_token"] == "TOK123"
    assert "url" not in f


@respx.mock
def test_multiview_preserves_empty_and_order(monkeypatch, tmp_path):
    respx.post("https://api.tripo3d.ai/v2/openapi/upload").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"image_token": "TOK"}})
    )
    task_captured = {}

    def _task(request):
        task_captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "t2"}})

    respx.post("https://api.tripo3d.ai/v2/openapi/task").mock(side_effect=_task)
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path)
    body = {
        "type": "multiview_to_model",
        "files": [
            {"type": "jpeg", "url": f"http://127.0.0.1:8189/asset/{aid}"},
            {},  # empty back view (TripoFileEmptyReference)
            {"type": "jpeg", "url": f"http://127.0.0.1:8189/asset/{aid}"},
        ],
    }
    r = c.post("/proxy/tripo/v2/openapi/task", json=body)
    assert r.status_code == 200
    files = task_captured["body"]["files"]
    assert len(files) == 3
    assert files[1] == {}                       # empty preserved + ordered
    assert files[0]["file_token"] == "TOK"
    assert files[2]["file_token"] == "TOK"


@respx.mock
def test_poll_passthrough(monkeypatch, tmp_path):
    respx.get("https://api.tripo3d.ai/v2/openapi/task/t1").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"status": "success"}})
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/tripo/v2/openapi/task/t1")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "success"


@respx.mock
def test_submit_strips_empty_status(monkeypatch, tmp_path):
    """Some gateways pad the submit response with status:''/type:'' that the real Tripo
    API omits. ComfyUI's TripoTask.status is Optional (absent->None is valid) but the
    empty string fails the enum, so the adapter must drop empty status/type."""
    respx.post("https://api.tripo3d.ai/v2/openapi/task").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"task_id": "t9", "type": "", "status": "", "progress": 0}},
        )
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.post("/proxy/tripo/v2/openapi/task", json={"type": "text_to_model", "prompt": "x"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == "t9"
    assert "status" not in data       # empty enum stripped -> absent -> ComfyUI sees None
    assert "type" not in data
    assert data["progress"] == 0      # other fields untouched


@respx.mock
def test_poll_keeps_valid_status_strips_empty(monkeypatch, tmp_path):
    """A real (non-empty) status passes through; an empty status (early queued state)
    is stripped so ComfyUI's poller treats it as not-yet-terminal and keeps polling."""
    respx.get("https://api.tripo3d.ai/v2/openapi/task/tq").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "tq", "status": "", "progress": 0}})
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/tripo/v2/openapi/task/tq")
    assert r.status_code == 200
    assert "status" not in r.json()["data"]


def test_missing_key_returns_424(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path, TRIPO_API_KEY="")
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    r = c.post(
        "/proxy/tripo/v2/openapi/task",
        json={"type": "text_to_model", "prompt": "x"},
    )
    assert r.status_code == 424
    assert "tripo" in r.json()["error"]["message"]


@respx.mock
def test_upload_error_surfaces_as_vendor_error(monkeypatch, tmp_path):
    """If the upload sub-request fails, surface the upstream status via vendor_error_response
    rather than raising an unhandled 500. This is the first adapter that calls vendor on its
    own initiative, so explicit error handling matters."""
    respx.post("https://api.tripo3d.ai/v2/openapi/upload").mock(
        return_value=httpx.Response(502, json={"code": 1, "message": "upstream down"})
    )
    # task endpoint must NOT be hit when upload fails
    task_route = respx.post("https://api.tripo3d.ai/v2/openapi/task").mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"task_id": "shouldnotrun"}})
    )
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path)
    body = {
        "type": "image_to_model",
        "file": {"type": "jpeg", "url": f"http://127.0.0.1:8189/asset/{aid}"},
    }
    r = c.post("/proxy/tripo/v2/openapi/task", json=body)
    assert r.status_code == 502
    assert not task_route.called
    assert "comfy-bridge upstream" in r.json()["error"]["message"]
