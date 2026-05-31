"""Pure helpers: model->endpoint mapping, param normalization, task_id codec.
No network, no side effects."""
import base64
import re


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


_SUFFIX_RE = re.compile(r"--(\w+)\s+(\S+)")


def parse_prompt_suffix(prompt: str) -> tuple[str, dict]:
    """'a cat --resolution 720p --ratio adaptive --duration 5'
       -> ('a cat', {'resolution':'720p','ratio':'adaptive','duration':'5'})"""
    m = _SUFFIX_RE.search(prompt)
    if not m:
        return prompt.strip(), {}
    base = prompt[:m.start()].strip()
    params = dict(_SUFFIX_RE.findall(prompt))
    return base, params


def build_video_payload(kind, prompt, params, *, image_urls=None,
                        end_image_url=None, video_urls=None, audio_urls=None,
                        generate_audio=None) -> dict:
    """kind: 't2v'|'i2v'|'ref'. Returns fal request body.
    generate_audio: from inbound body top-level field (adapter passes it)."""
    p = {"prompt": prompt}
    if "resolution" in params:
        p["resolution"] = params["resolution"]
    if "ratio" in params:
        p["aspect_ratio"] = normalize_ratio(params["ratio"])
    if "duration" in params:
        p["duration"] = str(params["duration"])
    if "seed" in params:
        try:
            p["seed"] = int(params["seed"])
        except ValueError:
            pass
    if generate_audio is not None:
        p["generate_audio"] = bool(generate_audio)
    if kind == "i2v":
        if not image_urls:
            raise UnsupportedModel("fal i2v requires at least one image_url")
        p["image_url"] = image_urls[0]
        if end_image_url:
            p["end_image_url"] = end_image_url
    elif kind == "ref":
        imgs = (image_urls or [])[:9]
        vids = (video_urls or [])[:3]
        auds = (audio_urls or [])[:3]
        if len(imgs) + len(vids) + len(auds) > 12:
            raise UnsupportedModel("fal reference: total media exceeds 12")
        if imgs:
            p["image_urls"] = imgs
        if vids:
            p["video_urls"] = vids
        if auds:
            p["audio_urls"] = auds
        p["prompt"] = inject_ref_tokens(prompt, imgs, vids, auds)
    return p


def inject_ref_tokens(prompt, images, videos, audios) -> str:
    """fal reference-to-video refs media as @Image1/@Video1/@Audio1 in prompt.
    (1) Normalize the node's 'Image N'/'Video N'/'Audio N' text refs to '@ImageN' etc.
    (2) Append @TagN for any media slot not already referenced (word-boundary safe;
        no double-injection; case-insensitive)."""
    out = prompt
    for tag, items in (("Image", images), ("Video", videos), ("Audio", audios)):
        # IGNORECASE matches input casing; replacement always emits canonical @Image/@Video/@Audio.
        out = re.sub(rf"(?<!@)\b{tag}\s*(\d+)\b", rf"@{tag}\1", out, flags=re.IGNORECASE)
        for i in range(1, len(items) + 1):
            if not re.search(rf"@{tag}{i}(?!\d)", out, flags=re.IGNORECASE):
                out = f"{out} @{tag}{i}".strip()
    return out
