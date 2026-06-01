"""Gemini adapter tests (spec §4/§5): Vertex shell -> GL generateContent mapping,
x-goog-api-key injection, fileData -> inlineData rewrite, drop uploadImagesToStorage,
missing-key 424."""
import base64
import importlib
import json
import os
import uuid

import httpx
import respx
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    env.setdefault("GEMINI_API_KEY", "gk-test")
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    from app.adapters import gemini as gm_mod
    importlib.reload(gm_mod)  # top-level register("vertexai", ...) runs on import
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
def test_vertex_to_gl_mapping_and_rewrite(monkeypatch, tmp_path):
    captured = {}

    def _resp(request):
        captured["x-goog-api-key"] = request.headers.get("x-goog-api-key", "")
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}]},
        )

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro:generateContent"
    ).mock(side_effect=_resp)
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path, data=b"PIC", mime="image/png")
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "fileData": {
                            "fileUri": f"http://127.0.0.1:8189/asset/{aid}",
                            "mimeType": "image/png",
                        }
                    },
                    {"text": "what is this"},
                ],
            }
        ],
        "uploadImagesToStorage": True,
    }
    r = c.post("/proxy/vertexai/gemini/gemini-3-pro", json=body)
    assert r.status_code == 200
    assert captured["x-goog-api-key"] == "gk-test"
    assert (
        captured["url"]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro:generateContent"
    )
    assert "uploadImagesToStorage" not in captured["body"]
    part0 = captured["body"]["contents"][0]["parts"][0]
    assert "fileData" not in part0
    assert part0["inlineData"]["mimeType"] == "image/png"
    assert base64.b64decode(part0["inlineData"]["data"]) == b"PIC"
    # text part untouched
    assert captured["body"]["contents"][0]["parts"][1] == {"text": "what is this"}


@respx.mock
def test_non_bridge_fileuri_passthrough(monkeypatch, tmp_path):
    """A fileData.fileUri pointing at a non-bridge URL (e.g. real Vertex GCS) must
    be left intact — bridge only rewrites its own /asset/{id} references."""
    captured = {}

    def _resp(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": []})

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro:generateContent"
    ).mock(side_effect=_resp)
    c, _assets = _client(monkeypatch, tmp_path)
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "fileData": {
                            "fileUri": "gs://some-bucket/foo.png",
                            "mimeType": "image/png",
                        }
                    }
                ],
            }
        ]
    }
    r = c.post("/proxy/vertexai/gemini/gemini-3-pro", json=body)
    assert r.status_code == 200
    part0 = captured["body"]["contents"][0]["parts"][0]
    assert part0 == {
        "fileData": {"fileUri": "gs://some-bucket/foo.png", "mimeType": "image/png"}
    }
    assert "inlineData" not in part0


@respx.mock
def test_default_image_output_options_stripped(monkeypatch, tmp_path):
    """ComfyUI always emits imageConfig.imageOutputOptions={"mimeType":"image/png"}
    (pydantic default); Google AI Studio rejects the Vertex-only field. The redundant
    default must be stripped (no-op: PNG is Google's default), while imageSize stays."""
    captured = {}

    def _resp(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": []})

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent"
    ).mock(side_effect=_resp)
    c, _ = _client(monkeypatch, tmp_path)
    body = {
        "contents": [{"role": "user", "parts": [{"text": "a little lion"}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"imageSize": "1K", "imageOutputOptions": {"mimeType": "image/png"}},
        },
    }
    r = c.post("/proxy/vertexai/gemini/gemini-3.1-flash-image-preview", json=body)
    assert r.status_code == 200
    ic = captured["body"]["generationConfig"]["imageConfig"]
    assert "imageOutputOptions" not in ic  # default stripped
    assert ic["imageSize"] == "1K"  # real option preserved


@respx.mock
def test_non_default_image_output_options_preserved(monkeypatch, tmp_path):
    """A genuine non-default request (jpeg / compressionQuality) is preserved so
    upstreams that DO support the field (Vertex / leihuo) keep the user's intent."""
    captured = {}

    def _resp(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": []})

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent"
    ).mock(side_effect=_resp)
    c, _ = _client(monkeypatch, tmp_path)
    # Each case is non-default and must be preserved. The PNG+quality case isolates the
    # compressionQuality guard (mimeType still default) so a broken quality guard can't
    # hide behind the mimeType guard; the jpeg-only case isolates the mimeType guard.
    cases = [
        {"mimeType": "image/jpeg", "compressionQuality": 80},
        {"mimeType": "image/png", "compressionQuality": 80},  # PNG but explicit quality
        {"mimeType": "image/jpeg"},  # non-PNG, no quality
    ]
    for opts in cases:
        body = {
            "contents": [{"role": "user", "parts": [{"text": "a little lion"}]}],
            "generationConfig": {"imageConfig": {"imageSize": "1K", "imageOutputOptions": dict(opts)}},
        }
        r = c.post("/proxy/vertexai/gemini/gemini-3.1-flash-image-preview", json=body)
        assert r.status_code == 200
        assert captured["body"]["generationConfig"]["imageConfig"]["imageOutputOptions"] == opts


def test_missing_key_returns_424(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path, GEMINI_API_KEY="")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = c.post("/proxy/vertexai/gemini/gemini-3-pro", json={"contents": []})
    assert r.status_code == 424
    assert "gemini" in r.json()["error"]["message"]
