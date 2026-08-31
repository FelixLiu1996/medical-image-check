from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

THUMBNAIL_SIZE = 64
LOCAL_FEATURE_MAX_DIMENSION = 1600
LOCAL_FEATURE_LIMIT = 1200
LOCAL_FEATURE_GRID_SIZE = 4
DETAIL_IMAGE_MAX_PIXELS = 25_000
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
    local_keypoints: NDArray[np.float32]
    local_descriptors: NDArray[np.uint8]
    layout_background_fraction: float
    mean_colorfulness: float
    detail_image: NDArray[np.uint8] | None

    @property
    def identity_fingerprint(self) -> TransformFingerprint:
        return self.fingerprints[0]


def extract_image_features(path: str | Path, data: bytes | None = None) -> tuple[ImageFeature, ...]:
    source = Path(path)
    encoded = data if data is not None else source.read_bytes()
    pages = decode_image_pages(source, encoded)
    return extract_image_features_from_pages(source, pages)


def decode_image_pages(path: str | Path, data: bytes | None = None) -> tuple[NDArray, ...]:
    source = Path(path)
    encoded = data if data is not None else source.read_bytes()
    buffer = np.frombuffer(encoded, dtype=np.uint8)
    return _decode_pages(source, buffer)


def extract_image_features_from_pages(
    path: str | Path,
    pages: tuple[NDArray, ...],
) -> tuple[ImageFeature, ...]:
    source = Path(path)
    page_count = len(pages)
    features: list[ImageFeature] = []
    for page_number, image in enumerate(pages, start=1):
        canonical = canonical_pixels(image)
        gray = to_gray8(canonical)
        thumbnail_u8 = cv2.resize(
            gray,
            (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
            interpolation=cv2.INTER_AREA,
        )
        normalized = _normalize_thumbnail(thumbnail_u8)
        local_keypoints, local_descriptors = _extract_local_features(gray)
        layout_background_fraction, mean_colorfulness = _layout_statistics(canonical, gray)
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
                local_keypoints=local_keypoints,
                local_descriptors=local_descriptors,
                layout_background_fraction=layout_background_fraction,
                mean_colorfulness=mean_colorfulness,
                detail_image=(
                    np.ascontiguousarray(gray)
                    if gray.size <= DETAIL_IMAGE_MAX_PIXELS and min(gray.shape) >= 16
                    else None
                ),
            )
        )
    return tuple(features)


def canonical_pixels(image: NDArray) -> NDArray:
    return _canonical_pixels(image)


def to_gray8(image: NDArray) -> NDArray[np.uint8]:
    return _to_gray8(image)


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


def _layout_statistics(
    canonical: NDArray,
    gray: NDArray[np.uint8],
) -> tuple[float, float]:
    bgr = canonical[:, :, :3]
    values = bgr.astype(np.float32)
    chroma = np.max(values, axis=2) - np.min(values, axis=2)
    background = (gray >= 238) & (chroma <= 25)
    return float(np.mean(background)), float(np.mean(chroma))


def _normalize_thumbnail(image: NDArray[np.uint8]) -> NDArray[np.float32]:
    values = image.astype(np.float32)
    mean = float(np.mean(values))
    deviation = float(np.std(values))
    if deviation <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return np.ascontiguousarray((values - mean) / deviation, dtype=np.float32)


def _extract_local_features(
    image: NDArray[np.uint8],
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    height, width = image.shape[:2]
    scale = min(1.0, LOCAL_FEATURE_MAX_DIMENSION / max(height, width, 1))
    if scale < 1.0:
        processing = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        processing = image

    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(processing)
    detector = cv2.ORB_create(
        nfeatures=LOCAL_FEATURE_LIMIT,
        scaleFactor=1.2,
        nlevels=8,
        edgeThreshold=15,
        patchSize=31,
        fastThreshold=10,
    )
    keypoints, descriptors = detector.detectAndCompute(enhanced, None)
    if descriptors is None or not keypoints:
        return (
            np.empty((0, 2), dtype=np.float32),
            np.empty((0, 32), dtype=np.uint8),
        )

    ordered_indices = _spatial_keypoint_order(keypoints, processing.shape[1], processing.shape[0])
    points = np.asarray(
        [
            (keypoints[index].pt[0] / scale, keypoints[index].pt[1] / scale)
            for index in ordered_indices
        ],
        dtype=np.float32,
    )
    return points, np.ascontiguousarray(descriptors[ordered_indices], dtype=np.uint8)


def _spatial_keypoint_order(keypoints: tuple | list, width: int, height: int) -> list[int]:
    cells: list[list[int]] = [[] for _ in range(LOCAL_FEATURE_GRID_SIZE**2)]
    strongest_first = sorted(
        range(len(keypoints)),
        key=lambda index: (-keypoints[index].response, keypoints[index].pt),
    )
    for index in strongest_first:
        x, y = keypoints[index].pt
        column = min(LOCAL_FEATURE_GRID_SIZE - 1, int(x * LOCAL_FEATURE_GRID_SIZE / max(width, 1)))
        row = min(LOCAL_FEATURE_GRID_SIZE - 1, int(y * LOCAL_FEATURE_GRID_SIZE / max(height, 1)))
        cells[row * LOCAL_FEATURE_GRID_SIZE + column].append(index)

    ordered: list[int] = []
    offsets = [0] * len(cells)
    while len(ordered) < len(keypoints):
        added = False
        for cell_index, cell in enumerate(cells):
            offset = offsets[cell_index]
            if offset >= len(cell):
                continue
            ordered.append(cell[offset])
            offsets[cell_index] += 1
            added = True
        if not added:
            break
    return ordered


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
