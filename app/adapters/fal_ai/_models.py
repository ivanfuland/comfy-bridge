"""Pure helpers: model->endpoint mapping, param normalization, task_id codec.
No network, no side effects."""
import base64


class UnsupportedModel(ValueError):
    pass


class BadTaskId(ValueError):
    pass


_FALLBACK_RATIO = "auto"
_VALID_RATIOS = {"auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}

# fal per-version max images (v5/lite cap ~6; confirm exact at impl/spike; spec §4.2)
_IMAGE_MAX = {"v5/lite": 6, "v4.5": 10, "v4": 10}


def _seedance_tier(model: str) -> str:
    return "fast/" if "-fast-" in model else ""


def video_endpoint(model: str, has_media) -> str:
    """has_media: False (t2v) | 'first_last' (i2v) | 'reference' (ref2v)."""
    if not model.startswith("dreamina-seedance-2-0"):
        raise UnsupportedModel(f"fal-ai: video model {model!r} not supported")
    tier = _seedance_tier(model)
    _kinds = {False: "text-to-video", "first_last": "image-to-video", "reference": "reference-to-video"}
    kind = _kinds.get(has_media)
    if kind is None:
        raise UnsupportedModel(f"fal-ai: unknown has_media value {has_media!r}")
    return f"bytedance/seedance-2.0/{tier}{kind}"


def _seedream_version(model: str) -> str:
    if model.startswith("seedream-5-0"):
        return "v5/lite"
    if model.startswith("seedream-4-5"):
        return "v4.5"
    if model.startswith("seedream-4-0"):
        return "v4"
    raise UnsupportedModel(f"fal-ai: image model {model!r} not supported")


def image_endpoint(model: str, has_image: bool) -> str:
    ver = _seedream_version(model)
    op = "edit" if has_image else "text-to-image"
    return f"fal-ai/bytedance/seedream/{ver}/{op}"


def normalize_ratio(ratio: str) -> str:
    if ratio == "adaptive":
        return _FALLBACK_RATIO
    return ratio if ratio in _VALID_RATIOS else _FALLBACK_RATIO


def clamp_max_images(model: str, requested: int) -> int:
    cap = _IMAGE_MAX.get(_seedream_version(model), 6)
    return min(requested, cap)


def encode_task_id(endpoint_id: str, request_id: str) -> str:
    raw = f"{endpoint_id}|{request_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_task_id(task_id: str) -> tuple[str, str]:
    try:
        pad = "=" * (-len(task_id) % 4)
        raw = base64.urlsafe_b64decode(task_id + pad).decode()
        ep, rid = raw.split("|", 1)
        return ep, rid
    except Exception as e:
        raise BadTaskId(f"cannot decode task_id {task_id!r}: {e}") from e
