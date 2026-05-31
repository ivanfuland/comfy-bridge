"""Pure mapping/encoding functions — no network."""
import pytest
from app.adapters.fal_ai import _models as M


@pytest.mark.parametrize("model,has_media,expected", [
    ("dreamina-seedance-2-0-260128", False, "bytedance/seedance-2.0/text-to-video"),
    ("dreamina-seedance-2-0-fast-260128", False, "bytedance/seedance-2.0/fast/text-to-video"),
    ("dreamina-seedance-2-0-260128", "first_last", "bytedance/seedance-2.0/image-to-video"),
    ("dreamina-seedance-2-0-fast-260128", "first_last", "bytedance/seedance-2.0/fast/image-to-video"),
    ("dreamina-seedance-2-0-260128", "reference", "bytedance/seedance-2.0/reference-to-video"),
])
def test_video_endpoint(model, has_media, expected):
    assert M.video_endpoint(model, has_media) == expected


@pytest.mark.parametrize("model,has_image,expected", [
    ("seedream-5-0-260128", False, "fal-ai/bytedance/seedream/v5/lite/text-to-image"),
    ("seedream-5-0-260128", True, "fal-ai/bytedance/seedream/v5/lite/edit"),
    ("seedream-4-5-251128", False, "fal-ai/bytedance/seedream/v4.5/text-to-image"),
    ("seedream-4-0-250828", True, "fal-ai/bytedance/seedream/v4/edit"),
])
def test_image_endpoint(model, has_image, expected):
    assert M.image_endpoint(model, has_image) == expected


def test_unknown_model_raises():
    with pytest.raises(M.UnsupportedModel):
        M.video_endpoint("seedance-1-0-pro-250528", False)
    with pytest.raises(M.UnsupportedModel):
        M.image_endpoint("seedream-3-0-t2i-250415", False)


def test_video_endpoint_bad_has_media():
    with pytest.raises(M.UnsupportedModel):
        M.video_endpoint("dreamina-seedance-2-0-260128", "bogus")


@pytest.mark.parametrize("inp,out", [
    ("adaptive", "auto"), ("16:9", "16:9"), ("9:16", "9:16"), ("auto", "auto"), ("2:1", "auto")])
def test_normalize_ratio(inp, out):
    assert M.normalize_ratio(inp) == out


def test_task_id_roundtrip():
    tid = M.encode_task_id("bytedance/seedance-2.0/text-to-video", "req-abc_123")
    assert "=" not in tid
    assert "/" not in tid and "+" not in tid
    ep, rid = M.decode_task_id(tid)
    assert ep == "bytedance/seedance-2.0/text-to-video"
    assert rid == "req-abc_123"


def test_task_id_decode_failure():
    with pytest.raises(M.BadTaskId):
        M.decode_task_id("!!!not-valid!!!")


@pytest.mark.parametrize("model,requested,capped", [
    ("seedream-5-0-260128", 14, 6),
    ("seedream-4-5-251128", 10, 10),
    ("seedream-4-0-250828", 15, 10),
])
def test_clamp_max_images(model, requested, capped):
    assert M.clamp_max_images(model, requested) == capped


@pytest.mark.parametrize("size,expected", [
    ("2048x2048", {"width": 2048, "height": 2048}),
    ("1024x1024", {"width": 1024, "height": 1024}),
    ("4096x2160", {"width": 4096, "height": 2160}),
])
def test_parse_image_size(size, expected):
    assert M.parse_image_size(size) == expected


@pytest.mark.parametrize("bad", ["foo", "1024", "", "axb", None])
def test_parse_image_size_bad_raises(bad):
    with pytest.raises(M.UnsupportedModel):
        M.parse_image_size(bad)


def test_build_video_payload_i2v_requires_image():
    from app.adapters.fal_ai import _models as M
    with pytest.raises(M.UnsupportedModel):
        M.build_video_payload("i2v", "x", {}, image_urls=[])
