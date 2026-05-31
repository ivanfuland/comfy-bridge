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


@pytest.fixture
def client_no_key(monkeypatch):
    # Same as `client` but does NOT set FAL_KEY (conftest's autouse fixture already
    # delenvs it). Exercises the missing-key -> FalConfigError -> 424 path.
    monkeypatch.setenv("BYTEPLUS_BACKEND", "fal-ai")
    import app.adapters as A
    A._REGISTRY.clear(); A._LOADED_BACKEND_CHOICES.clear(); A._LOADED = False
    for name in list(sys.modules):
        if name.startswith("app.adapters.") and name != "app.adapters.base":
            del sys.modules[name]
    A.load_adapters()
    from app.main import app
    return TestClient(app)


# fal poll URLs use the app-id (model path minus the operation segment), NOT the
# full endpoint. Submit response carries those verbatim; the adapter encodes
# response_url into the task_id.
_APP = "https://queue.fal.run/bytedance/seedance-2.0"


def _submit_json(req_id: str, app: str = _APP) -> dict:
    return {
        "request_id": req_id,
        "status_url": f"{app}/requests/{req_id}/status",
        "response_url": f"{app}/requests/{req_id}",
    }


@respx.mock
def test_t2v_create_translates_to_fal_submit(client):
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/text-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-9")))
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
    # the returned id decodes to fal's response_url (single source of truth for poll)
    from app.adapters.fal_ai._models import decode_task_id
    assert decode_task_id(r.json()["id"]) == f"{_APP}/requests/req-9"


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


def test_media_unknown_asset_returns_424(client):
    # content with a media item pointing at an unknown asset id -> AssetNotFound -> 424.
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "x"},
                        {"type": "image_url", "image_url": {"url": "asset://does-not-exist"},
                         "role": "first_frame"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 424


# ── seedance asset shim (Task 6): upload -> bridge cache, GET Active/Failed, resolve ──
import uuid


def _seed_bridge_asset(data=b"FALFRAME", mime="image/png"):
    """Put bytes into the bridge asset cache (the same store the node fills via
    /customers/storage + PUT) and return its bridge download_url."""
    from app import assets as assets_mod
    aid = uuid.uuid4().hex
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), f"cc-fal-asset-{aid}")
    with open(path, "wb") as f:
        f.write(data)
    assets_mod._REGISTRY[aid] = assets_mod.AssetRecord(
        asset_id=aid, file_name="x.png", media_type=mime, path=path
    )
    return f"http://127.0.0.1:8190/asset/{aid}"


def test_virtual_library_upload_returns_asset_id(client):
    bridge_url = _seed_bridge_asset()
    r = client.post("/proxy/seedance/virtual-library/assets",
                    json={"url": bridge_url, "hash": "deadbeef"})
    assert r.status_code == 200
    asset_id = r.json()["asset_id"]
    assert isinstance(asset_id, str) and asset_id


def test_get_unknown_asset_returns_failed(client):
    r = client.get("/proxy/seedance/assets/nonexistent-id")
    assert r.status_code == 200
    assert r.json()["status"] == "Failed"  # NOT Active for an unknown id


def test_get_known_asset_returns_active(client):
    bridge_url = _seed_bridge_asset()
    asset_id = client.post(
        "/proxy/seedance/virtual-library/assets",
        json={"url": bridge_url, "asset_type": "Image"},
    ).json()["asset_id"]
    r = client.get(f"/proxy/seedance/assets/{asset_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "Active"
    assert r.json()["asset_type"] == "Image"


def test_asset_management_assets_post_returns_424(client):
    # Repurposed from Task 6's test_assets_helper_post_also_stores. Per spec §5.3 fal-ai
    # does NOT support asset management. POST /proxy/seedance/assets is hit ONLY by the
    # asset-management nodes (ByteDanceCreateImageAsset/CreateVideoAsset via
    # _create_seedance_asset) — NOT by the FirstLastFrame/Reference i2v flow, which uploads
    # via /virtual-library/assets. So it must return a clear 424, not store an asset.
    bridge_url = _seed_bridge_asset()
    r = client.post(
        "/proxy/seedance/assets",
        json={"group_id": "g1", "url": bridge_url, "asset_type": "Image", "name": "n"},
    )
    assert r.status_code == 424
    assert "asset management" in json.dumps(r.json()).lower()


def test_visual_validate_returns_424(client):
    # visual-validate (H5 real-person auth) is hit only by the asset-management nodes'
    # _obtain_group_id_via_h5_auth. fal-ai has no equivalent -> 424, not a 500/hang.
    r = client.post("/proxy/seedance/visual-validate/sessions", json={})
    assert r.status_code == 424
    assert "asset management" in json.dumps(r.json()).lower()
    r2 = client.get("/proxy/seedance/visual-validate/sessions/some-session-id")
    assert r2.status_code == 424


def test_missing_fal_key_returns_424(client_no_key):
    # A video create with no FAL_KEY set -> _fal_client._key() raises FalConfigError ->
    # adapter maps to 424 with FAL_KEY in the message (not a 500/hang).
    body = {"model": "dreamina-seedance-2-0-260128", "content": [{"text": "a cat"}]}
    r = client_no_key.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 424
    assert "FAL_KEY" in json.dumps(r.json())


@respx.mock
def test_resolve_to_fal_url_uploads_cached_bytes(client):
    import asyncio
    from app.adapters import get_adapter
    adapter = get_adapter("seedance")

    bridge_url = _seed_bridge_asset(data=b"RESOLVEME", mime="image/png")
    asset_id = client.post(
        "/proxy/seedance/virtual-library/assets", json={"url": bridge_url}
    ).json()["asset_id"]

    # respx the 3-step fal storage upload
    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/signed-put",
            "file_url": "https://cdn.fal.run/files/resolved.png",
        })
    )
    put = respx.put("https://upload.fal.run/signed-put").mock(
        return_value=httpx.Response(200))

    url = asyncio.run(adapter._resolve_to_fal_url(f"asset://{asset_id}"))
    assert url == "https://cdn.fal.run/files/resolved.png"
    # the cached bytes were the ones uploaded to fal
    assert put.calls[0].request.content == b"RESOLVEME"


@respx.mock
def test_resolve_to_fal_url_accepts_bridge_url_directly(client):
    import asyncio
    from app.adapters import get_adapter
    adapter = get_adapter("seedance")

    bridge_url = _seed_bridge_asset(data=b"DIRECT", mime="image/jpeg")
    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/signed-put2",
            "file_url": "https://cdn.fal.run/files/direct.jpg",
        })
    )
    put = respx.put("https://upload.fal.run/signed-put2").mock(
        return_value=httpx.Response(200))

    url = asyncio.run(adapter._resolve_to_fal_url(bridge_url))
    assert url == "https://cdn.fal.run/files/direct.jpg"
    assert put.calls[0].request.content == b"DIRECT"


def test_resolve_to_fal_url_raises_for_unknown_asset(client):
    import asyncio
    from app.adapters import get_adapter
    from app.adapters.base import AssetNotFound
    adapter = get_adapter("seedance")
    with pytest.raises(AssetNotFound):
        asyncio.run(adapter._resolve_to_fal_url("asset://does-not-exist"))


def test_resolve_to_fal_url_passes_through_public_url(client):
    import asyncio
    from app.adapters import get_adapter
    adapter = get_adapter("seedance")
    out = asyncio.run(adapter._resolve_to_fal_url("https://cdn.example.com/v.mp4"))
    assert out == "https://cdn.example.com/v.mp4"   # public URL returned as-is, no upload


# ── Task 9: seedream image generation (sync: queue submit + block-poll) ──────────
@respx.mock
def test_seedream_text_to_image_multi(client):
    respx.post("https://queue.fal.run/fal-ai/bytedance/seedream/v4.5/text-to-image").mock(
        return_value=httpx.Response(200, json=_submit_json(
            "req-s", "https://queue.fal.run/fal-ai/bytedance/seedream/v4.5")))
    respx.get(url__regex=r".*/requests/req-s/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(url__regex=r".*/requests/req-s$").mock(
        return_value=httpx.Response(200, json={"images": [{"url": "u1"}, {"url": "u2"}], "seed": 1}))
    body = {"model": "seedream-4-5-251128", "prompt": "a dog", "size": "2048x2048"}
    r = client.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code == 200
    # multi-image: ALL of data[], NOT just [0]
    body_out = r.json()
    assert [d["url"] for d in body_out["data"]] == ["u1", "u2"]
    # node's ImageTaskCreationResponse pydantic model: error must be a dict (default {}),
    # NOT None; created an int. e2e regression guard.
    assert body_out["error"] == {} and isinstance(body_out["error"], dict)
    assert isinstance(body_out["created"], int)


@respx.mock
def test_seedream_t2i_payload_shape(client):
    sub = respx.post("https://queue.fal.run/fal-ai/bytedance/seedream/v4/text-to-image").mock(
        return_value=httpx.Response(200, json=_submit_json(
            "req-p", "https://queue.fal.run/fal-ai/bytedance/seedream/v4")))
    respx.get(url__regex=r".*/requests/req-p/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(url__regex=r".*/requests/req-p$").mock(
        return_value=httpx.Response(200, json={"images": [{"url": "x"}], "seed": 7}))
    body = {
        "model": "seedream-4-0-250828", "prompt": "a cat", "size": "1024x1024", "seed": 42,
        "watermark": True,  # dropped per spec
        "sequential_image_generation_options": {"max_images": 4},
    }
    r = client.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code == 200
    sent = json.loads(sub.calls[0].request.content)
    assert sent["prompt"] == "a cat"
    assert sent["image_size"] == {"width": 1024, "height": 1024}
    assert sent["seed"] == 42
    assert sent["max_images"] == 4
    assert "image_urls" not in sent  # no input image -> t2i, not edit
    assert "watermark" not in sent   # dropped


@respx.mock
def test_seedream_edit_endpoint_when_image_present(client):
    # with input image -> fal edit endpoint. asset resolved via the bridge cache (Task 6
    # mechanics); mock the fal storage upload + the edit queue.
    bridge_url = _seed_bridge_asset(data=b"EDITSRC", mime="image/png")
    asset_id = client.post(
        "/proxy/seedance/virtual-library/assets", json={"url": bridge_url}
    ).json()["asset_id"]
    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/sp-edit",
            "file_url": "https://cdn.fal.run/files/edit-src.png",
        }))
    respx.put("https://upload.fal.run/sp-edit").mock(return_value=httpx.Response(200))
    sub = respx.post("https://queue.fal.run/fal-ai/bytedance/seedream/v5/lite/edit").mock(
        return_value=httpx.Response(200, json=_submit_json(
            "req-e", "https://queue.fal.run/fal-ai/bytedance/seedream/v5/lite")))
    respx.get(url__regex=r".*/requests/req-e/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(url__regex=r".*/requests/req-e$").mock(
        return_value=httpx.Response(200, json={"images": [{"url": "o"}]}))
    body = {"model": "seedream-5-0-260128", "prompt": "x",
            "image": [f"asset://{asset_id}"], "size": "2048x2048"}
    r = client.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code == 200 and "o" in json.dumps(r.json())
    sent = json.loads(sub.calls[0].request.content)
    assert sent["image_urls"] == ["https://cdn.fal.run/files/edit-src.png"]


def test_seedream_size_parsed(client):
    from app.adapters.fal_ai._models import parse_image_size
    assert parse_image_size("1024x1024") == {"width": 1024, "height": 1024}


@respx.mock
def test_seedream_failed_maps_error(client):
    respx.post(url__regex=r".*/text-to-image").mock(
        return_value=httpx.Response(200, json=_submit_json(
            "req-f", "https://queue.fal.run/fal-ai/bytedance/seedream/v4")))
    respx.get(url__regex=r".*/requests/req-f/status").mock(
        return_value=httpx.Response(200, json={"status": "FAILED"}))
    body = {"model": "seedream-4-0-250828", "prompt": "x", "size": "1024x1024"}
    r = client.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code >= 400  # fal FAILED -> error, not a fake success


def test_seedream_unsupported_model_returns_424(client):
    body = {"model": "seedream-3-0-t2i-250415", "prompt": "x", "size": "1024x1024"}
    r = client.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code == 424


def test_seedream_bad_size_returns_424(client):
    body = {"model": "seedream-4-5-251128", "prompt": "x", "size": "not-a-size"}
    r = client.post("/proxy/byteplus/api/v3/images/generations", json=body)
    assert r.status_code == 424


# ── Task 7: image-to-video (first/last frame) + reference-to-video (multimodal) ──
def _stub_resolve(monkeypatch):
    """Stub _resolve_to_fal_url so the create-branch routing tests focus on which
    fal endpoint/payload is built, not the upload mechanics (covered by Task 6).
    Echoes the inbound ref so assertions can trace each url back to its source."""
    from app.adapters.fal_ai.bytedance import FalBytedanceAdapter

    async def _fake(self, ref):
        return f"https://cdn.fal.run/files/{ref.replace('asset://', '')}"

    monkeypatch.setattr(FalBytedanceAdapter, "_resolve_to_fal_url", _fake)


@respx.mock
def test_first_last_frame_uses_image_to_video(client, monkeypatch):
    _stub_resolve(monkeypatch)
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/image-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-i")))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "pan --duration 5"},
                        {"type": "image_url", "image_url": {"url": "asset://first1"},
                         "role": "first_frame"},
                        {"type": "image_url", "image_url": {"url": "asset://last1"},
                         "role": "last_frame"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200 and sub.called
    sent = json.loads(sub.calls[0].request.content)
    assert sent["image_url"] == "https://cdn.fal.run/files/first1"
    assert sent["end_image_url"] == "https://cdn.fal.run/files/last1"
    assert sent["prompt"].startswith("pan")
    assert sent["duration"] == "5"
    from app.adapters.fal_ai._models import decode_task_id
    assert decode_task_id(r.json()["id"]) == f"{_APP}/requests/req-i"


@respx.mock
def test_first_frame_only_image_to_video(client, monkeypatch):
    _stub_resolve(monkeypatch)
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/image-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-f")))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "zoom"},
                        {"type": "image_url", "image_url": {"url": "asset://only"},
                         "role": "first_frame"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200 and sub.called
    sent = json.loads(sub.calls[0].request.content)
    assert sent["image_url"] == "https://cdn.fal.run/files/only"
    assert "end_image_url" not in sent   # no last frame supplied


@respx.mock
def test_reference_injects_ref_tokens(client, monkeypatch):
    _stub_resolve(monkeypatch)
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/reference-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-r")))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "two cats"},
                        {"type": "image_url", "image_url": {"url": "asset://a1"},
                         "role": "reference_image"},
                        {"type": "image_url", "image_url": {"url": "asset://a2"},
                         "role": "reference_image"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200 and sub.called
    sent = json.loads(sub.calls[0].request.content)
    assert sent["image_urls"] == ["https://cdn.fal.run/files/a1",
                                  "https://cdn.fal.run/files/a2"]
    assert "@Image1" in sent["prompt"] and "@Image2" in sent["prompt"]


@respx.mock
def test_reference_with_video_and_audio(client, monkeypatch):
    _stub_resolve(monkeypatch)
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/reference-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-rva")))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "scene"},
                        {"type": "image_url", "image_url": {"url": "asset://img"},
                         "role": "reference_image"},
                        {"type": "video_url", "video_url": {"url": "asset://vid"}},
                        {"type": "audio_url", "audio_url": {"url": "asset://aud"}}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200 and sub.called
    sent = json.loads(sub.calls[0].request.content)
    assert sent["image_urls"] == ["https://cdn.fal.run/files/img"]
    assert sent["video_urls"] == ["https://cdn.fal.run/files/vid"]
    assert sent["audio_urls"] == ["https://cdn.fal.run/files/aud"]


@respx.mock
def test_fast_tier_image_to_video_endpoint(client, monkeypatch):
    _stub_resolve(monkeypatch)
    sub = respx.post("https://queue.fal.run/bytedance/seedance-2.0/fast/image-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json(
            "req-fast", "https://queue.fal.run/bytedance/seedance-2.0/fast")))
    body = {"model": "dreamina-seedance-2-0-fast-260128",
            "content": [{"text": "go"},
                        {"type": "image_url", "image_url": {"url": "asset://f"},
                         "role": "first_frame"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200 and sub.called


# ── m4a: total > 12 → 424 ──
@respx.mock
def test_total_media_over_12_returns_424(client, monkeypatch):
    """9 images + 3 videos + 1 audio = 13 total; build_video_payload raises
    UnsupportedModel → adapter maps to 424."""
    _stub_resolve(monkeypatch)
    # respx the endpoint so routing doesn't fail before the payload guard fires
    respx.post("https://queue.fal.run/bytedance/seedance-2.0/reference-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-overflow")))
    content = [{"text": "overflow test"}]
    for i in range(9):
        content.append({"type": "image_url", "image_url": {"url": f"asset://img{i}"},
                         "role": "reference_image"})
    for i in range(3):
        content.append({"type": "video_url", "video_url": {"url": f"asset://vid{i}"}})
    content.append({"type": "audio_url", "audio_url": {"url": "asset://aud0"}})
    body = {"model": "dreamina-seedance-2-0-260128", "content": content}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 424


# ── m4b: last_frame only (no first_frame) → 424 ──
@respx.mock
def test_last_frame_only_returns_424(client, monkeypatch):
    """A last_frame without a first_frame falls into the reference branch.
    image_urls will be empty (last_frame was folded into all_imgs but resolved
    to nothing useful alone) — actually last_frame IS in all_imgs so image_urls=[url].
    Wait: code path: (first or last) and not (ref_imgs or vids or auds) → i2v branch
    because last is truthy and no ref/vid/aud. i2v branch: image_urls=[] (no first),
    build_video_payload('i2v', ..., image_urls=[]) → UnsupportedModel → 424."""
    _stub_resolve(monkeypatch)
    respx.post("https://queue.fal.run/bytedance/seedance-2.0/image-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-lf")))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "last only"},
                        {"type": "image_url", "image_url": {"url": "asset://last-only"},
                         "role": "last_frame"}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 424


# ── m3: reference with video+audio asserts @Image1/@Video1/@Audio1 in prompt ──
@respx.mock
def test_reference_with_video_and_audio_has_ref_tokens(client, monkeypatch):
    """Strengthens test_reference_with_video_and_audio: verifies that inject_ref_tokens
    appends @Image1, @Video1, @Audio1 to the prompt."""
    _stub_resolve(monkeypatch)
    respx.post("https://queue.fal.run/bytedance/seedance-2.0/reference-to-video").mock(
        return_value=httpx.Response(200, json=_submit_json("req-rva2")))
    body = {"model": "dreamina-seedance-2-0-260128",
            "content": [{"text": "scene"},
                        {"type": "image_url", "image_url": {"url": "asset://img"},
                         "role": "reference_image"},
                        {"type": "video_url", "video_url": {"url": "asset://vid"}},
                        {"type": "audio_url", "audio_url": {"url": "asset://aud"}}]}
    r = client.post("/proxy/byteplus/api/v3/contents/generations/tasks", json=body)
    assert r.status_code == 200
    sent = json.loads(respx.calls.last.request.content)
    assert "@Image1" in sent["prompt"]
    assert "@Video1" in sent["prompt"]
    assert "@Audio1" in sent["prompt"]


# ── Task 8: video poll (status mapping + two-layer error detection) ──
# Inbound: GET /proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{task_id}.
# Output must match native byteplus._reshape_poll's TaskStatusResponse shape:
# {id, model, status in queued|running|cancelled|succeeded|failed, content?{video_url: STR}}
# The node reads response.content.video_url (a plain string — confirmed from
# ComfyUI apis/bytedance.py TaskStatusResult.video_url: str).
@respx.mock
def test_poll_running_then_succeeded(client):
    from app.adapters.fal_ai._models import encode_task_id
    tid = encode_task_id(f"{_APP}/requests/req-9")
    respx.get(url__regex=r".*/requests/req-9/status").mock(
        side_effect=[httpx.Response(200, json={"status": "IN_PROGRESS"}),
                     httpx.Response(200, json={"status": "COMPLETED"})])
    respx.get(url__regex=r".*/requests/req-9$").mock(
        return_value=httpx.Response(200, json={"video": {"url": "https://cdn/x.mp4"}}))
    r1 = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r1.json()["status"] == "running"
    r2 = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r2.json()["status"] == "succeeded"
    # the video url must be where the node reads it: content.video_url (plain string)
    assert r2.json()["content"]["video_url"] == "https://cdn/x.mp4"
    assert "x.mp4" in json.dumps(r2.json())


@respx.mock
def test_poll_completed_with_error_maps_failed(client):
    from app.adapters.fal_ai._models import encode_task_id
    tid = encode_task_id(f"{_APP}/requests/req-e")
    respx.get(url__regex=r".*/requests/req-e/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(url__regex=r".*/requests/req-e$").mock(
        return_value=httpx.Response(200, json={"error": "nsfw", "video": None}))
    r = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r.json()["status"] == "failed"   # COMPLETED-with-error / no video.url => failed
    # never succeeded with null content
    assert r.json().get("content") is None


@respx.mock
def test_poll_completed_no_video_url_maps_failed(client):
    """COMPLETED but result has neither error nor a usable video.url -> failed,
    NOT a succeeded with null content (which would crash the node on content.video_url)."""
    from app.adapters.fal_ai._models import encode_task_id
    tid = encode_task_id(f"{_APP}/requests/req-nv")
    respx.get(url__regex=r".*/requests/req-nv/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(url__regex=r".*/requests/req-nv$").mock(
        return_value=httpx.Response(200, json={"video": {}}))
    r = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r.json()["status"] == "failed"
    assert r.json().get("content") is None


def test_poll_bad_task_id_returns_error(client):
    r = client.get("/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/!!!notbase64!!!")
    assert r.status_code >= 400   # BadTaskId => clear error, not 500
    assert r.status_code < 500


@respx.mock
def test_poll_fal_http_error_maps_failed(client):
    from app.adapters.fal_ai._models import encode_task_id
    tid = encode_task_id(f"{_APP}/requests/req-h")
    respx.get(url__regex=r".*/requests/req-h/status").mock(
        return_value=httpx.Response(500, json={"error": "boom"}))
    r = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r.status_code == 200             # bridge returns a 200 status-doc, NOT the upstream 5xx
    assert r.json()["status"] == "failed"   # fal 5xx => failed (a poll status doc, not raw 500)


@respx.mock
def test_poll_terminal_failure_status_maps_failed(client):
    """A terminal fal status (FAILED) without ever reaching COMPLETED -> failed."""
    from app.adapters.fal_ai._models import encode_task_id
    tid = encode_task_id(f"{_APP}/requests/req-tf")
    respx.get(url__regex=r".*/requests/req-tf/status").mock(
        return_value=httpx.Response(200, json={"status": "FAILED"}))
    r = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r.json()["status"] == "failed"


@respx.mock
def test_poll_non_json_result_maps_failed(client):
    from app.adapters.fal_ai._models import encode_task_id
    tid = encode_task_id(f"{_APP}/requests/req-nj")
    respx.get(url__regex=r".*/requests/req-nj/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(url__regex=r".*/requests/req-nj$").mock(
        return_value=httpx.Response(200, text="not json at all"))
    r = client.get(f"/proxy/byteplus-seedance2/api/v3/contents/generations/tasks/{tid}")
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
