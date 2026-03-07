from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
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


def compute_text_simhash(value: str) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    weights = [0] * 64
    for token, count in Counter(normalized.split(" ")).items():
        token_hash = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")
        for bit_index in range(64):
            bit_is_set = (token_hash >> bit_index) & 1
            weights[bit_index] += count if bit_is_set else -count
    fingerprint = 0
    for bit_index, weight in enumerate(weights):
        if weight >= 0:
            fingerprint |= 1 << bit_index
    return f"{fingerprint:016x}"


def compute_media_hash(raw: bytes) -> str:
    return sha256_hex(raw)


def compute_image_phash(raw: bytes) -> str:
    image = Image.open(io.BytesIO(raw))
    return str(imagehash.phash(image))


def simhash_hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def simhash_band_values(simhash: str, band_count: int) -> list[str]:
    bits = f"{int(simhash, 16):064b}"
    base_chunk_size, remainder = divmod(len(bits), band_count)
    values: list[str] = []
    start = 0
    for index in range(band_count):
        chunk_size = base_chunk_size + (1 if index < remainder else 0)
        end = start + chunk_size
        values.append(bits[start:end])
        start = end
    return values


def best_available_hash(values: Iterable[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None
