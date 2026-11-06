"""Local image checks before any API call. Standard library only.

Limits are from the Claude vision documentation (fetched 2026-11-05,
https://platform.claude.com/docs/en/build-with-claude/vision):

* formats: image/jpeg, image/png, image/gif, image/webp (GIF: first frame only)
* per-image size: 10 MB base64-encoded on the Claude API
* max 8000x8000 px; with more than 20 images per request, keep each side <= 3000 px
* accuracy degrades for images under 200 px (warning, not rejection)
* visual tokens = ceil(w/28) * ceil(h/28) after downscaling to the model's tier
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import math
import struct
from pathlib import Path

SUPPORTED_MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
MAX_BASE64_BYTES = 10_000_000          # "10 MB" read conservatively as decimal megabytes
MAX_SIDE_PX = 8000
MANY_IMAGES_THRESHOLD = 20
MANY_IMAGES_MAX_SIDE_PX = 3000
SMALL_IMAGE_PX = 200

# (max long edge, max visual tokens) per resolution tier.
TIERS = {"high": (2576, 4784), "standard": (1568, 1568)}
HIGH_RES_MODELS = ("claude-fable", "claude-mythos", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
                   "claude-sonnet-5", "claude-haiku-5")


@dataclass
class ImageCheck:
    path: str
    ok: bool
    media_type: str | None = None
    width: int | None = None
    height: int | None = None
    raw_bytes: int = 0
    base64_bytes: int = 0
    sha256: str = ""
    rejection: str | None = None
    warnings: list[str] = field(default_factory=list)


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return "image/heic"
    return None


def dimensions(data: bytes, media_type: str) -> tuple[int, int] | None:
    try:
        if media_type == "image/png":
            return struct.unpack(">II", data[16:24])
        if media_type == "image/gif":
            return struct.unpack("<HH", data[6:10])
        if media_type == "image/webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            return None
        if media_type == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
            return None
    except struct.error:
        return None
    return None


def check_image(path: Path, images_in_request: int = 1) -> ImageCheck:
    data = path.read_bytes()
    check = ImageCheck(path=str(path), ok=False, raw_bytes=len(data),
                       base64_bytes=4 * math.ceil(len(data) / 3), sha256=hashlib.sha256(data).hexdigest())
    media = sniff(data)
    check.media_type = media
    if media is None:
        check.rejection = "unrecognized_format"
        return check
    if media not in SUPPORTED_MEDIA_TYPES:
        check.rejection = "unsupported_media_type"  # e.g. HEIC, accepted by the CampusFix frontend
        return check
    dims = dimensions(data, media)
    if dims is None:
        check.rejection = "unreadable_dimensions"
        return check
    check.width, check.height = dims
    if check.base64_bytes > MAX_BASE64_BYTES:
        check.rejection = "exceeds_base64_size_limit"
        return check
    limit = MANY_IMAGES_MAX_SIDE_PX if images_in_request > MANY_IMAGES_THRESHOLD else MAX_SIDE_PX
    if max(dims) > limit:
        check.rejection = "exceeds_dimension_limit"
        return check
    if min(dims) < SMALL_IMAGE_PX:
        check.warnings.append("small_image_accuracy_risk")
    if media == "image/gif":
        check.warnings.append("gif_first_frame_only")
    check.ok = True
    return check


def encode(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


def tier_for(model: str) -> str:
    return "high" if model.startswith(HIGH_RES_MODELS) else "standard"


def count_image_tokens(width: int, height: int) -> int:
    return math.ceil(width / 28) * math.ceil(height / 28)


def resized_size(width: int, height: int, max_edge: int, max_tokens: int) -> tuple[int, int]:
    """Size Claude resizes an image to before padding.

    Reference implementation from the Claude docs ("Coordinates and bounding boxes" ->
    "Resize your image before uploading", fetched 2026-11-05). Python's round() is
    round-half-even, matching the live API at exact .5 ties.
    """
    def fits(w: int, h: int) -> bool:
        return (math.ceil(w / 28) * 28 <= max_edge and math.ceil(h / 28) * 28 <= max_edge
                and count_image_tokens(w, h) <= max_tokens)

    if fits(width, height):
        return (width, height)
    if height > width:
        resized_h, resized_w = resized_size(height, width, max_edge, max_tokens)
        return (resized_w, resized_h)
    aspect_ratio = width / height
    lo, hi = 1, width  # lo always fits; hi never fits
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect_ratio), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect_ratio), 1))


def visual_tokens(width: int, height: int, tier: str = "high") -> tuple[int, int, int]:
    """(tokens, processed_width, processed_height) for the model's resolution tier."""
    w, h = resized_size(width, height, *TIERS[tier])
    return count_image_tokens(w, h), w, h
