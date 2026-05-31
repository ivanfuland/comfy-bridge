"""fal HTTP client tests (respx-mocked, async, 0 token)."""
import pytest
import respx
import httpx

from app.adapters.fal_ai import _fal_client

pytestmark = pytest.mark.asyncio  # pytest-asyncio asyncio_mode="auto" already configured


@respx.mock
async def test_submit_returns_request_id(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    route = respx.post("https://queue.fal.run/bytedance/seedance-2.0/text-to-video").mock(
        return_value=httpx.Response(200, json={"request_id": "req-123"}))
    req_id = await _fal_client.submit("bytedance/seedance-2.0/text-to-video", {"prompt": "x"})
    assert req_id == "req-123"
    assert route.calls[0].request.headers["authorization"] == "Key test-key"


@respx.mock
async def test_status_and_result(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    respx.get("https://queue.fal.run/bytedance/seedance-2.0/text-to-video/requests/req-1/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get("https://queue.fal.run/bytedance/seedance-2.0/text-to-video/requests/req-1").mock(
        return_value=httpx.Response(200, json={"video": {"url": "https://cdn/x.mp4"}, "seed": 1}))
    st = await _fal_client.status("bytedance/seedance-2.0/text-to-video", "req-1")
    assert st["status"] == "COMPLETED"
    res = await _fal_client.result("bytedance/seedance-2.0/text-to-video", "req-1")
    assert res["video"]["url"] == "https://cdn/x.mp4"


async def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(_fal_client.FalConfigError):
        await _fal_client.submit("ep", {"prompt": "x"})


@respx.mock
async def test_fal_http_error_wrapped(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    respx.post("https://queue.fal.run/ep").mock(return_value=httpx.Response(429, json={"error": "rate"}))
    with pytest.raises(_fal_client.FalUpstreamError) as ei:
        await _fal_client.submit("ep", {"prompt": "x"})
    assert ei.value.status_code == 429


@respx.mock
async def test_run_sync_timeout_raises(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    respx.post(url__regex=r".*/text-to-image").mock(return_value=httpx.Response(200, json={"request_id": "req-t"}))
    respx.get(url__regex=r".*/requests/req-t/status").mock(return_value=httpx.Response(200, json={"status": "IN_PROGRESS"}))
    with pytest.raises(_fal_client.FalUpstreamError) as ei:
        await _fal_client.run_sync("fal-ai/bytedance/seedream/v4/text-to-image",
                                   {"prompt": "x"}, poll_interval=0.01, max_wait=0.03)
    assert ei.value.status_code == 504


@respx.mock
async def test_run_sync_completed_returns_result(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    ep = "bytedance/seedance-2.0/text-to-video"
    respx.post(f"https://queue.fal.run/{ep}").mock(
        return_value=httpx.Response(200, json={"request_id": "req-ok"}))
    respx.get(f"https://queue.fal.run/{ep}/requests/req-ok/status").mock(
        return_value=httpx.Response(200, json={"status": "COMPLETED"}))
    respx.get(f"https://queue.fal.run/{ep}/requests/req-ok").mock(
        return_value=httpx.Response(200, json={"video": {"url": "https://cdn/y.mp4"}}))
    res = await _fal_client.run_sync(ep, {"prompt": "x"}, poll_interval=0.01, max_wait=1.0)
    assert res["video"]["url"] == "https://cdn/y.mp4"


@respx.mock
async def test_run_sync_failed_raises_502(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    ep = "bytedance/seedance-2.0/text-to-video"
    respx.post(f"https://queue.fal.run/{ep}").mock(
        return_value=httpx.Response(200, json={"request_id": "req-f"}))
    respx.get(f"https://queue.fal.run/{ep}/requests/req-f/status").mock(
        return_value=httpx.Response(200, json={"status": "FAILED", "error": "boom"}))
    with pytest.raises(_fal_client.FalUpstreamError) as ei:
        await _fal_client.run_sync(ep, {"prompt": "x"}, poll_interval=0.01, max_wait=1.0)
    assert ei.value.status_code == 502


@respx.mock
async def test_upload_bytes_returns_fal_url(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    initiate = respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/put/abc",
            "file_url": "https://v3.fal.media/files/abc/x.png",
        }))
    put = respx.put("https://upload.fal.run/put/abc").mock(return_value=httpx.Response(200))
    url = await _fal_client.upload_bytes(b"\x89PNG", "image/png")
    assert url == "https://v3.fal.media/files/abc/x.png"
    assert initiate.called
    assert put.called
    # auth header present on initiate
    assert initiate.calls[0].request.headers["authorization"] == "Key test-key"


@respx.mock
async def test_upload_bytes_clamps_expiry(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    captured = {}

    def _initiate(request):
        captured["lifecycle"] = request.headers.get("x-fal-object-lifecycle-preference")
        return httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/put/abc",
            "file_url": "https://v3.fal.media/files/abc/x.png",
        })

    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(side_effect=_initiate)
    respx.put("https://upload.fal.run/put/abc").mock(return_value=httpx.Response(200))
    # ask for a too-short expiry; client must clamp up to MIN_EXPIRY_SECONDS
    await _fal_client.upload_bytes(b"x", "image/png", expiry_seconds=10)
    assert captured["lifecycle"] is not None
    assert str(_fal_client.MIN_EXPIRY_SECONDS) in captured["lifecycle"]


@respx.mock
async def test_run_sync_polls_until_completed(monkeypatch):
    """Status returns IN_PROGRESS twice then COMPLETED; run_sync returns result payload."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    ep = "bytedance/seedance-2.0/text-to-video"
    respx.post(f"https://queue.fal.run/{ep}").mock(
        return_value=httpx.Response(200, json={"request_id": "req-multi"}))
    respx.get(f"https://queue.fal.run/{ep}/requests/req-multi/status").mock(
        side_effect=[
            httpx.Response(200, json={"status": "IN_PROGRESS"}),
            httpx.Response(200, json={"status": "IN_PROGRESS"}),
            httpx.Response(200, json={"status": "COMPLETED"}),
        ])
    respx.get(f"https://queue.fal.run/{ep}/requests/req-multi").mock(
        return_value=httpx.Response(200, json={"video": {"url": "https://cdn/z.mp4"}}))
    res = await _fal_client.run_sync(ep, {"prompt": "x"}, poll_interval=0.001, max_wait=1.0)
    assert res["video"]["url"] == "https://cdn/z.mp4"


@respx.mock
async def test_upload_bytes_put_failure_raises(monkeypatch):
    """PUT to pre-signed URL returns 500; FalUpstreamError should be raised."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    respx.post("https://rest.alpha.fal.ai/storage/upload/initiate").mock(
        return_value=httpx.Response(200, json={
            "upload_url": "https://upload.fal.run/put/fail",
            "file_url": "https://v3.fal.media/files/fail/x.png",
        }))
    respx.put("https://upload.fal.run/put/fail").mock(
        return_value=httpx.Response(500, json={"error": "internal server error"}))
    with pytest.raises(_fal_client.FalUpstreamError) as ei:
        await _fal_client.upload_bytes(b"\x89PNG", "image/png")
    assert ei.value.status_code == 500
