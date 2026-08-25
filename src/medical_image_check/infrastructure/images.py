from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

THUMBNAIL_SIZE = 64
TRANSFORMS = (
    "identity",
    "rotate_90",
    "rotate_180",
    "rotate_270",
    "flip_horizontal",
    "flip_horizontal_rotate_90",
    "flip_vertical",
    "flip_horizontal_rotate_270",
)


@dataclass(frozen=True, slots=True)
class TransformFingerprint:
    transform: str
    phash: int
    dhash: int


@dataclass(frozen=True, slots=True)
class ImageFeature:
    source_path: str
    page: int
    page_count: int
    width: int
    height: int
    pixel_sha256: str
    thumbnail: NDArray[np.float32]
    standard_deviation: float
    fingerprints: tuple[TransformFingerprint, ...]

    @property
    def identity_fingerprint(self) -> TransformFingerprint:
        return self.fingerprints[0]


def extract_image_features(path: str | Path, data: bytes | None = None) -> tuple[ImageFeature, ...]:
    source = Path(path)
    encoded = data if data is not None else source.read_bytes()
    buffer = np.frombuffer(encoded, dtype=np.uint8)
    pages = _decode_pages(source, buffer)
    page_count = len(pages)
    features: list[ImageFeature] = []
    for page_number, image in enumerate(pages, start=1):
        canonical = _canonical_pixels(image)
        gray = _to_gray8(canonical)
        thumbnail_u8 = cv2.resize(
            gray,
            (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
            interpolation=cv2.INTER_AREA,
        )
        normalized = _normalize_thumbnail(thumbnail_u8)
        fingerprints = tuple(
            TransformFingerprint(
                transform=transform,
                phash=_phash(apply_transform(thumbnail_u8, transform)),
                dhash=_dhash(apply_transform(thumbnail_u8, transform)),
            )
            for transform in TRANSFORMS
        )
        height, width = canonical.shape[:2]
        features.append(
            ImageFeature(
                source_path=str(source),
                page=page_number,
                page_count=page_count,
                width=int(width),
                height=int(height),
                pixel_sha256=_pixel_digest(canonical),
                thumbnail=normalized,
                standard_deviation=float(np.std(thumbnail_u8)),
                fingerprints=fingerprints,
            )
        )
    return tuple(features)


def apply_transform(image: NDArray, transform: str) -> NDArray:
    if transform == "identity":
        return image
    if transform == "rotate_90":
        return np.rot90(image, 1)
    if transform == "rotate_180":
        return np.rot90(image, 2)
    if transform == "rotate_270":
        return np.rot90(image, 3)
    if transform == "flip_horizontal":
        return np.fliplr(image)
    if transform == "flip_horizontal_rotate_90":
        return np.rot90(np.fliplr(image), 1)
    if transform == "flip_vertical":
        return np.flipud(image)
    if transform == "flip_horizontal_rotate_270":
        return np.rot90(np.fliplr(image), 3)
    raise ValueError(f"未知图片变换：{transform}")


def hamming_distance(first: int, second: int) -> int:
    return (first ^ second).bit_count()


def normalized_similarity(first: NDArray[np.float32], second: NDArray[np.float32]) -> float:
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-12:
        return 1.0 if np.array_equal(first, second) else 0.0
    return max(-1.0, min(1.0, float(np.sum(first * second)) / denominator))


def _decode_pages(source: Path, buffer: NDArray[np.uint8]) -> tuple[NDArray, ...]:
    if source.suffix.lower() in {".tif", ".tiff"}:
        success, decoded = cv2.imdecodemulti(buffer, cv2.IMREAD_UNCHANGED)
        if success and decoded:
            return tuple(decoded)
    image = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("图片无法解码或文件已损坏")
    return (image,)


def _canonical_pixels(image: NDArray) -> NDArray:
    if image.ndim == 2:
        return np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))
    if image.ndim != 3:
        raise ValueError(f"不支持的图片维度：{image.shape}")
    channels = image.shape[2]
    if channels == 1:
        return np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))
    if channels == 3:
        return np.ascontiguousarray(image)
    if channels == 4:
        alpha = image[:, :, 3]
        maximum = np.iinfo(alpha.dtype).max if np.issubdtype(alpha.dtype, np.integer) else 1.0
        if np.all(alpha == maximum):
            return np.ascontiguousarray(image[:, :, :3])
        return np.ascontiguousarray(image)
    raise ValueError(f"不支持的图片通道数：{channels}")


def _to_gray8(image: NDArray) -> NDArray[np.uint8]:
    if image.shape[2] == 4:
        gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if gray.dtype == np.uint8:
        return gray
    normalized = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    return normalized.astype(np.uint8)


def _pixel_digest(image: NDArray) -> str:
    digest = sha256()
    digest.update(str(image.dtype).encode("ascii"))
    digest.update(str(tuple(image.shape)).encode("ascii"))
    digest.update(image.tobytes(order="C"))
    return digest.hexdigest()


def _normalize_thumbnail(image: NDArray[np.uint8]) -> NDArray[np.float32]:
    values = image.astype(np.float32)
    mean = float(np.mean(values))
    deviation = float(np.std(values))
    if deviation <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return np.ascontiguousarray((values - mean) / deviation, dtype=np.float32)


def _phash(image: NDArray[np.uint8]) -> int:
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    coefficients = cv2.dct(resized)[:8, :8]
    threshold = float(np.median(coefficients.reshape(-1)[1:]))
    bits = coefficients > threshold
    return int.from_bytes(np.packbits(bits.reshape(-1)).tobytes(), "big")


def _dhash(image: NDArray[np.uint8]) -> int:
    resized = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
    bits = resized[:, 1:] > resized[:, :-1]
    return int.from_bytes(np.packbits(bits.reshape(-1)).tobytes(), "big")
