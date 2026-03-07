from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Iterable

import imagehash
from PIL import Image

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip()).lower()


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def compute_text_hash(value: str) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    return sha256_hex(normalized.encode("utf-8"))


def compute_media_hash(raw: bytes) -> str:
    return sha256_hex(raw)


def compute_image_phash(raw: bytes) -> str:
    image = Image.open(io.BytesIO(raw))
    return str(imagehash.phash(image))


def best_available_hash(values: Iterable[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None

