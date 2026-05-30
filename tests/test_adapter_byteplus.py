"""ByteDance/Seedance adapter tests (spec §"翻译/Shim 设计").

Covers the three route vendor segments the one adapter registers:
  byteplus            — video create (1.x inline params + 2.0 separate->inline) + seedream image
  byteplus-seedance2  — 2.0 poll prefix -> same upstream /v1/video/generations/{id}
  seedance            — virtual-library / asset / visual-validate shims (no upstream)

All upstream calls target the leihuo default base (https://ai.leihuo.netease.com),
mocked via respx exactly like test_adapter_tripo.py.
"""
import base64
import importlib
import json
import os
import uuid

import httpx
import respx
from fastapi.testclient import TestClient

LEIHUO = "https://ai.leihuo.netease.com"


def _client(monkeypatch, tmp_path, **env):
    env.setdefault("BYTEPLUS_API_KEY", "bk-test")
    monkeypatch.setenv("BRIDGE_ASSET_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg_mod
    importlib.reload(cfg_mod)
    from app import assets as assets_mod
    importlib.reload(assets_mod)
    from app.adapters import base as base_mod
    importlib.reload(base_mod)
    from app.adapters import byteplus as bp_mod
    importlib.reload(bp_mod)  # top-level register() runs on import; also resets _SEEDANCE_ASSETS
    from app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app), assets_mod


def _seed(assets_mod, tmp_path, data=b"IMGBYTES", mime="image/png"):
    aid = uuid.uuid4().hex
    path = os.path.join(str(tmp_path), aid)
    with open(path, "wb") as f:
        f.write(data)
    assets_mod._REGISTRY[aid] = assets_mod.AssetRecord(
        asset_id=aid, file_name="x.png", media_type=mime, path=path
    )
    return aid


def _decode_data_uri(uri: str) -> bytes:
    assert uri.startswith("data:"), uri
    return base64.b64decode(uri.split(",", 1)[1])


# ── video create: Seedance 1.x (params already inline in content text) ──
@respx.mock
def test_v1_text_to_video_create(monkeypatch, tmp_path):
    captured = {}

    def _create(request):
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"id": "task_1", "task_id": "task_1", "status": "queued"})

    respx.post(f"{LEIHUO}/v1/video/generations").mock(side_effect=_create)
    c, _ = _client(monkeypatch, tmp_path)
    body = {
        "model": "seedance-1-5-pro-251215",
        "content": [{"type": "text", "text": "a cat --resolution 720p --ratio 16:9 --duration 5"}],
        "generate_audio": False,
    }
    r = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    assert captured["auth"] == "Bearer bk-test"
    assert captured["body"]["model"] == "doubao-seedance-1-5-pro-251215"
    # 1.x prompt passes through verbatim (params already inline; not duplicated)
    assert captured["body"]["prompt"] == "a cat --resolution 720p --ratio 16:9 --duration 5"
    assert "image_url" not in captured["body"]
    assert r.json()["id"] == "task_1"


@respx.mock
def test_v1_image_to_video_base64(monkeypatch, tmp_path):
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "t"})
    )
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path, data=b"FRAME0")
    body = {
        "model": "seedance-1-0-pro-fast-251015",
        "content": [
            {"type": "text", "text": "move --resolution 480p"},
            {"type": "image_url", "image_url": {"url": f"http://127.0.0.1:8190/asset/{aid}"}},
        ],
        "generate_audio": None,
    }
    r = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    # single image, no role -> images[0] (gateway unified contract), resolved to base64
    assert _decode_data_uri(captured["body"]["images"][0]) == b"FRAME0"
    assert len(captured["body"]["images"]) == 1
    assert "image_url" not in captured["body"]  # flat field is silently dropped by the gateway
    assert captured["body"]["model"] == "doubao-seedance-1-0-pro-fast-251015"


@respx.mock
def test_v1_first_last_frame_roles(monkeypatch, tmp_path):
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "t"})
    )
    c, assets_mod = _client(monkeypatch, tmp_path)
    a_first = _seed(assets_mod, tmp_path, data=b"FIRST")
    a_last = _seed(assets_mod, tmp_path, data=b"LAST")
    body = {
        "model": "seedance-1-5-pro-251215",
        "content": [
            {"type": "text", "text": "p --resolution 720p"},
            {"type": "image_url", "image_url": {"url": f"http://127.0.0.1:8190/asset/{a_first}"}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": f"http://127.0.0.1:8190/asset/{a_last}"}, "role": "last_frame"},
        ],
        "generate_audio": True,
    }
    r = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    # first+last -> images[0]=first, images[1]=last (order matters)
    imgs = captured["body"]["images"]
    assert _decode_data_uri(imgs[0]) == b"FIRST"
    assert _decode_data_uri(imgs[1]) == b"LAST"
    assert "first_frame_image" not in captured["body"]
    assert "last_frame_image" not in captured["body"]
    assert "image_url" not in captured["body"]


@respx.mock
def test_v1_reference_images(monkeypatch, tmp_path):
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "t"})
    )
    c, assets_mod = _client(monkeypatch, tmp_path)
    a1 = _seed(assets_mod, tmp_path, data=b"REF1")
    a2 = _seed(assets_mod, tmp_path, data=b"REF2")
    body = {
        "model": "seedance-1-0-lite-i2v-250428",
        "content": [
            {"type": "text", "text": "p --resolution 480p"},
            {"type": "image_url", "image_url": {"url": f"http://127.0.0.1:8190/asset/{a1}"}, "role": "reference_image"},
            {"type": "image_url", "image_url": {"url": f"http://127.0.0.1:8190/asset/{a2}"}, "role": "reference_image"},
        ],
        "generate_audio": None,
    }
    r = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    # reference set -> images[] (top-level can't carry role:reference_image; best-effort)
    refs = captured["body"]["images"]
    assert len(refs) == 2
    assert _decode_data_uri(refs[0]) == b"REF1"
    assert _decode_data_uri(refs[1]) == b"REF2"
    assert "reference_images" not in captured["body"]


@respx.mock
def test_v1_first_frame_public_url_passthrough(monkeypatch, tmp_path):
    """Regression (2026-05-30): a first_frame image must reach the gateway in the top-level
    `images` array. The old flat `first_frame_image` field was silently dropped by the
    gateway -> text2video with the wrong character. Public urls pass through verbatim."""
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "t"})
    )
    c, _ = _client(monkeypatch, tmp_path)
    url = "https://picgo-fuland.oss-cn-beijing.aliyuncs.com/images/hero.jpg"
    body = {
        "model": "seedance-1-5-pro-251215",
        "content": [
            {"type": "text", "text": "turn head --resolution 480p"},
            {"type": "image_url", "image_url": {"url": url}, "role": "first_frame"},
        ],
    }
    r = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    assert captured["body"]["images"] == [url]  # passthrough, single element = first frame
    assert "first_frame_image" not in captured["body"]


# ── video create: Seedance 2.0 (separate fields -> appended --params) ──
@respx.mock
def test_v2_create_appends_params(monkeypatch, tmp_path):
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "task_v2"})
    )
    c, _ = _client(monkeypatch, tmp_path)
    body = {
        "model": "dreamina-seedance-2-0-260128",
        "content": [{"type": "text", "text": "a dragon"}],
        "generate_audio": True,
        "resolution": "1080p",
        "ratio": "16:9",
        "duration": 7,
        "seed": 42,
        "watermark": False,
    }
    r = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    assert captured["body"]["model"] == "doubao-seedance-2-0-260128"
    prompt = captured["body"]["prompt"]
    assert prompt.startswith("a dragon ")
    assert "--resolution 1080p" in prompt
    assert "--ratio 16:9" in prompt
    assert "--duration 7" in prompt
    assert "--seed 42" in prompt
    assert "--watermark false" in prompt
    assert r.json()["id"] == "task_v2"


@respx.mock
def test_v2_fast_model_mapping(monkeypatch, tmp_path):
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "t"})
    )
    c, _ = _client(monkeypatch, tmp_path)
    body = {
        "model": "dreamina-seedance-2-0-fast-260128",
        "content": [{"type": "text", "text": "x"}],
        "resolution": "720p",
        "duration": 4,
    }
    c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert captured["body"]["model"] == "doubao-seedance-2-0-fast-260128"


# ── video poll: both prefixes -> /v1/video/generations/{id} ──
@respx.mock
def test_v2_poll_seedance2_prefix(monkeypatch, tmp_path):
    inner = {
        "id": "task_x",
        "model": "doubao-seedance-2-0-260128",
        "status": "succeeded",
        "content": {"video_url": "https://cdn.example/v.mp4"},
        "usage": {"total_tokens": 100, "completion_tokens": 50},
    }
    respx.get(f"{LEIHUO}/v1/video/generations/task_x").mock(
        return_value=httpx.Response(
            200,
            json={"code": "success", "data": {"task_id": "task_x", "status": "SUCCESS", "progress": "100%", "data": inner}},
        )
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/task_x")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "succeeded"
    assert got["content"]["video_url"] == "https://cdn.example/v.mp4"
    assert got["id"] == "task_x"


@respx.mock
def test_v1_poll_byteplus_prefix(monkeypatch, tmp_path):
    inner = {"id": "t9", "model": "doubao-seedance-1-5-pro-251215", "status": "running", "content": None}
    respx.get(f"{LEIHUO}/v1/video/generations/t9").mock(
        return_value=httpx.Response(200, json={"code": "success", "data": {"task_id": "t9", "status": "IN_PROGRESS", "data": inner}})
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus/api/v3/contents/generations/tasks/t9")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


@respx.mock
def test_poll_fallback_outer_status(monkeypatch, tmp_path):
    """No inner data.data block -> synthesize from the outer envelope status."""
    respx.get(f"{LEIHUO}/v1/video/generations/t2").mock(
        return_value=httpx.Response(200, json={"code": "success", "data": {"task_id": "t2", "status": "IN_PROGRESS", "progress": "30%"}})
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus/api/v3/contents/generations/tasks/t2")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "running"
    assert got["id"] == "t2"
    assert got["content"] is None


@respx.mock
def test_poll_fallback_succeeded_recovers_result_url(monkeypatch, tmp_path):
    """Outer SUCCESS without an inner data.data block must NOT return succeeded+null content
    (the node reads content.video_url -> crash). Recover the url from envelope result_url."""
    respx.get(f"{LEIHUO}/v1/video/generations/t3").mock(
        return_value=httpx.Response(
            200,
            json={"code": "success", "data": {"task_id": "t3", "status": "SUCCESS", "result_url": "https://cdn/x.mp4"}},
        )
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus/api/v3/contents/generations/tasks/t3")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "succeeded"
    assert got["content"]["video_url"] == "https://cdn/x.mp4"


@respx.mock
def test_poll_fallback_succeeded_without_url_is_failed(monkeypatch, tmp_path):
    """Success with no downloadable url anywhere is unusable -> report failed, not a
    crash-inducing succeeded+null content."""
    respx.get(f"{LEIHUO}/v1/video/generations/t4").mock(
        return_value=httpx.Response(200, json={"code": "success", "data": {"task_id": "t4", "status": "SUCCESS"}})
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus/api/v3/contents/generations/tasks/t4")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "failed"
    assert got["content"] is None
    assert got["error"]["code"] == "comfy_bridge_no_video_url"


@respx.mock
def test_poll_outer_failure_beats_stale_inner_running(monkeypatch, tmp_path):
    """Regression: on a FAILED task the gateway leaves data.data frozen at
    status:"running" (and sets result_url to the error string). Honoring the inner
    block would report "running" forever and hang ComfyUI's poll loop. The outer
    terminal failure must win, surfacing fail_reason as the error."""
    inner = {  # stale snapshot — never updated to a terminal state
        "id": "cgt-x",
        "model": "doubao-seedance-2-0-fast-260128",
        "status": "running",
        "content": None,
    }
    respx.get(f"{LEIHUO}/v1/video/generations/task_z").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "success",
                "data": {
                    "task_id": "task_z",
                    "status": "FAILURE",
                    "fail_reason": "Failed to get channel info, channel ID: 12",
                    "result_url": "Failed to get channel info, channel ID: 12",
                    "progress": "100%",
                    "properties": {"origin_model_name": "doubao-seedance-2-0-fast-260128"},
                    "data": inner,
                },
            },
        )
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/task_z")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "failed"  # NOT "running"
    assert got["id"] == "task_z"
    assert got["content"] is None  # result_url was the error string -> not a video
    assert "channel ID: 12" in got["error"]["message"]


@respx.mock
def test_poll_outer_failure_no_inner_block(monkeypatch, tmp_path):
    """Outer FAILURE with no inner data.data block -> failed with synthesized error."""
    respx.get(f"{LEIHUO}/v1/video/generations/t5").mock(
        return_value=httpx.Response(
            200,
            json={"code": "success", "data": {"task_id": "t5", "status": "FAILURE", "fail_reason": "boom"}},
        )
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus/api/v3/contents/generations/tasks/t5")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "failed"
    assert got["error"]["message"] == "boom"


@respx.mock
def test_poll_outer_cancelled(monkeypatch, tmp_path):
    """CANCELLED outer status maps to cancelled even with a stale running inner."""
    inner = {"id": "t6", "status": "running", "content": None}
    respx.get(f"{LEIHUO}/v1/video/generations/t6").mock(
        return_value=httpx.Response(
            200,
            json={"code": "success", "data": {"task_id": "t6", "status": "CANCELLED", "data": inner}},
        )
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/byteplus/api/v3/contents/generations/tasks/t6")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


# ── seedream image ──
@respx.mock
def test_seedream_image_base64_and_normalize(monkeypatch, tmp_path):
    captured = {}

    def _img(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "doubao-seedream-4-5-251128", "created": 123, "data": [{"url": "https://cdn/img.png"}]})

    respx.post(f"{LEIHUO}/v1/images/generations").mock(side_effect=_img)
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path, data=b"REFIMG")
    body = {
        "model": "seedream-4-5-251128",
        "prompt": "edit this",
        "image": [f"http://127.0.0.1:8190/asset/{aid}"],
        "size": "2048x2048",
        "seed": 0,
        "watermark": False,
        "response_format": "url",
    }
    r = c.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code == 200
    assert captured["body"]["model"] == "doubao-seedream-4-5-251128"
    assert _decode_data_uri(captured["body"]["image"][0]) == b"REFIMG"
    got = r.json()
    assert got["data"][0]["url"] == "https://cdn/img.png"
    assert got["error"] == {}
    assert got["model"] == "doubao-seedream-4-5-251128"
    assert got["created"] == 123


# ── 2.0 virtual-library shim + asset:// resolution ──
@respx.mock
def test_virtual_library_shim_and_asset_resolution(monkeypatch, tmp_path):
    captured = {}
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        side_effect=lambda req: captured.update(body=json.loads(req.content))
        or httpx.Response(200, json={"id": "t"})
    )
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path, data=b"VLFRAME")
    bridge_url = f"http://127.0.0.1:8190/asset/{aid}"

    # 1) node uploads image into virtual library -> shim returns an asset_id
    r1 = c.post("/proxy/seedance/virtual-library/assets", json={"url": bridge_url, "hash": "deadbeef"})
    assert r1.status_code == 200
    asset_id = r1.json()["asset_id"]

    # 2) node polls the asset until Active
    r2 = c.get(f"/proxy/seedance/assets/{asset_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "Active"
    assert r2.json()["asset_type"] == "Image"

    # 3) 2.0 FLF create with content carrying asset://{id} -> resolved to base64
    body = {
        "model": "dreamina-seedance-2-0-260128",
        "content": [
            {"type": "text", "text": "animate"},
            {"type": "image_url", "image_url": {"url": f"asset://{asset_id}"}, "role": "first_frame"},
        ],
        "resolution": "720p",
        "ratio": "adaptive",
        "duration": 5,
    }
    r3 = c.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r3.status_code == 200
    # asset://{id} -> resolved to base64, carried in the top-level images array
    assert _decode_data_uri(captured["body"]["images"][0]) == b"VLFRAME"


@respx.mock
def test_seedance_asset_helper_create(monkeypatch, tmp_path):
    """The optional /proxy/seedance/assets POST (asset-helper nodes) stores + returns id."""
    c, assets_mod = _client(monkeypatch, tmp_path)
    aid = _seed(assets_mod, tmp_path, data=b"HELPER")
    bridge_url = f"http://127.0.0.1:8190/asset/{aid}"
    r = c.post("/proxy/seedance/assets", json={"group_id": "g1", "url": bridge_url, "asset_type": "Image", "name": "n"})
    assert r.status_code == 200
    asset_id = r.json()["asset_id"]
    r2 = c.get(f"/proxy/seedance/assets/{asset_id}")
    assert r2.json()["status"] == "Active"


def test_asset_get_unknown_is_failed(monkeypatch, tmp_path):
    """Unknown asset_id (stale / bridge restart) must report Failed, not a phantom Active —
    so the node surfaces the error up front instead of failing later at asset:// resolution."""
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/proxy/seedance/assets/does-not-exist")
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "Failed"
    assert got["error"]["code"] == "comfy_bridge_asset_unknown"


def test_visual_validate_shim(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path)
    r = c.post("/proxy/seedance/visual-validate/sessions", json={})
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    assert r.json()["h5_link"] == ""
    r2 = c.get(f"/proxy/seedance/visual-validate/sessions/{session_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "completed"
    assert r2.json()["group_id"]


def test_missing_key_returns_424(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path, BYTEPLUS_API_KEY="")
    monkeypatch.delenv("BYTEPLUS_API_KEY", raising=False)
    r = c.post(
        "/proxy/byteplus/api/v3/contents/generations/tasks",
        json={"model": "seedance-1-5-pro-251215", "content": [{"type": "text", "text": "x"}], "generate_audio": False},
    )
    assert r.status_code == 424
    assert "byteplus" in r.json()["error"]["message"]


@respx.mock
def test_vendor_error_surfaced(monkeypatch, tmp_path):
    respx.post(f"{LEIHUO}/v1/video/generations").mock(
        return_value=httpx.Response(400, json={"error": {"message": "model_not_found"}})
    )
    c, _ = _client(monkeypatch, tmp_path)
    r = c.post(
        "/proxy/byteplus/api/v3/contents/generations/tasks",
        json={"model": "seedance-1-5-pro-251215", "content": [{"type": "text", "text": "x"}], "generate_audio": False},
    )
    assert r.status_code == 400
    assert "comfy-bridge upstream" in r.json()["error"]["message"]
