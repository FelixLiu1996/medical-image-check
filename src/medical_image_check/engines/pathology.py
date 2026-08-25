from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    deterministic_finding_id,
)
from medical_image_check.infrastructure.images import canonical_pixels, hamming_distance

PATHOLOGY_MAX_DIMENSION = 1200
PATHOLOGY_STRUCTURE_SIZE = (96, 96)
PATHOLOGY_MAX_REGIONS_PER_PAGE = 40
PATHOLOGY_INDEX_BUCKET_LIMIT = 128
PATHOLOGY_CANDIDATE_MIN_VOTES = 2
PATHOLOGY_HASH_MAX_DISTANCE = 20
PATHOLOGY_TRANSFORMS = (
    "identity",
    "flip_horizontal",
    "flip_vertical",
    "rotate_180",
)

_MAGNIFICATION_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*[x×](?![a-z0-9])", re.I)
_PATHOLOGY_NAME_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(h[&+]?e|he|ihc|path|histology|病理|切片)(?:[^a-z0-9]|$)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class PathologyFingerprint:
    transform: str
    value: int


@dataclass(frozen=True, slots=True)
class PathologyRegion:
    source_path: str
    page: int
    page_count: int
    page_width: int
    page_height: int
    region: tuple[int, int, int, int]
    tissue_fraction: float
    magnification: float | None
    structure: NDArray[np.float32]
    tissue_mask: NDArray[np.uint8]
    fingerprints: tuple[PathologyFingerprint, ...]


@dataclass(frozen=True, slots=True)
class _PathologyMatch:
    transform: str
    hash_distance: int
    structure_similarity: float
    tissue_mask_iou: float
    confidence: float


class PathologyDuplicateDetector:
    reuse_rule_id = "image.pathology.local_reuse"
    magnification_rule_id = "image.pathology.same_region_different_magnification"

    def extract_from_pages(
        self,
        path: str | Path,
        pages: tuple[NDArray, ...],
    ) -> tuple[PathologyRegion, ...]:
        source = str(Path(path))
        named = bool(_PATHOLOGY_NAME_PATTERN.search(Path(path).stem))
        magnification = _infer_magnification(Path(path).stem)
        page_count = len(pages)
        extracted: list[PathologyRegion] = []
        for page_number, page in enumerate(pages, start=1):
            extracted.extend(
                _extract_page_regions(
                    source,
                    page_number,
                    page_count,
                    page,
                    named,
                    magnification,
                )
            )
        return tuple(extracted)

    def findings(
        self,
        regions: list[PathologyRegion],
        excluded_page_pairs: set[tuple[str, str]] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> list[Finding]:
        best_by_page_pair: dict[
            tuple[str, str], tuple[PathologyRegion, PathologyRegion, _PathologyMatch]
        ] = {}
        for candidate_index, (first_index, second_index) in enumerate(
            sorted(_candidate_pairs(regions))
        ):
            if checkpoint and candidate_index % 64 == 0:
                checkpoint()
            first = regions[first_index]
            second = regions[second_index]
            page_pair = _page_pair_key(first, second)
            if excluded_page_pairs and page_pair in excluded_page_pairs:
                continue
            match = _best_match(first, second)
            if match is None:
                continue
            existing = best_by_page_pair.get(page_pair)
            if existing is None or match.confidence > existing[2].confidence:
                best_by_page_pair[page_pair] = (first, second, match)

        findings: list[Finding] = []
        for first, second, match in best_by_page_pair.values():
            different_magnification, scale_ratio = _different_magnification(first, second)
            locations = (_location(first), _location(second))
            if different_magnification:
                rule_id = self.magnification_rule_id
                finding_type = FindingType.NORMAL_RELATION
                risk = RiskLevel.LOW
                title = "病理图疑似同一区域的不同倍率"
                description = (
                    "组织结构在染色强度归一化和多尺度匹配后对应；不同倍率默认属于正常图像关系。"
                )
                relationship = "normal_different_magnification"
            else:
                rule_id = self.reuse_rule_id
                finding_type = FindingType.SUSPECTED_REUSE
                risk = RiskLevel.MEDIUM if match.confidence >= 0.92 else RiskLevel.LOW
                title = "病理图组织区域疑似复用"
                description = (
                    "局部组织形态和组织掩膜在染色归一化后高度一致，"
                    "需结合切片、倍率和实验分组人工复核。"
                )
                relationship = "suspected_pathology_reuse"
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(rule_id, locations),
                    rule_id=rule_id,
                    finding_type=finding_type,
                    risk=risk,
                    title=title,
                    description=description,
                    locations=locations,
                    confidence=match.confidence,
                    details=_match_details(
                        first,
                        second,
                        match,
                        relationship,
                        scale_ratio,
                    ),
                )
            )
        return findings


def _extract_page_regions(
    source_path: str,
    page: int,
    page_count: int,
    image: NDArray,
    named: bool,
    magnification: float | None,
) -> list[PathologyRegion]:
    canonical = canonical_pixels(image)
    bgr = canonical[:, :, :3]
    if bgr.dtype != np.uint8:
        bgr = cv2.normalize(bgr, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    original_height, original_width = bgr.shape[:2]
    scale = min(1.0, PATHOLOGY_MAX_DIMENSION / max(original_height, original_width, 1))
    if scale < 1.0:
        bgr = cv2.resize(
            bgr,
            (
                max(1, round(original_width * scale)),
                max(1, round(original_height * scale)),
            ),
            interpolation=cv2.INTER_AREA,
        )

    morphology, tissue_mask = _stain_invariant_morphology(bgr)
    tissue_fraction = float(np.mean(tissue_mask > 0))
    if not named and not _looks_like_pathology(bgr, tissue_mask):
        return []
    if tissue_fraction < 0.035:
        return []

    boxes = _multiscale_boxes(bgr.shape[1], bgr.shape[0], tissue_mask)
    regions: list[PathologyRegion] = []
    for box in boxes[:PATHOLOGY_MAX_REGIONS_PER_PAGE]:
        x, y, width, height = box
        region_mask = tissue_mask[y : y + height, x : x + width]
        region_tissue_fraction = float(np.mean(region_mask > 0))
        if region_tissue_fraction < 0.18:
            continue
        structure = cv2.resize(
            morphology[y : y + height, x : x + width],
            PATHOLOGY_STRUCTURE_SIZE,
            interpolation=cv2.INTER_AREA,
        )
        structure = _standardize(structure)
        resized_mask = cv2.resize(
            region_mask,
            PATHOLOGY_STRUCTURE_SIZE,
            interpolation=cv2.INTER_NEAREST,
        )
        if float(np.std(structure)) < 0.08:
            continue
        fingerprints = tuple(
            PathologyFingerprint(
                transform,
                _perceptual_hash(_apply_transform(structure, transform)),
            )
            for transform in PATHOLOGY_TRANSFORMS
        )
        restored = _restore_box(box, scale)
        regions.append(
            PathologyRegion(
                source_path=source_path,
                page=page,
                page_count=page_count,
                page_width=original_width,
                page_height=original_height,
                region=restored,
                tissue_fraction=region_tissue_fraction,
                magnification=magnification,
                structure=structure,
                tissue_mask=np.asarray(resized_mask > 0, dtype=np.uint8),
                fingerprints=fingerprints,
            )
        )
    return regions


def _stain_invariant_morphology(
    bgr: NDArray[np.uint8],
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    optical_density = -np.log(np.clip((rgb + 1.0) / 256.0, 1.0 / 256.0, 1.0))
    density = np.max(optical_density, axis=2)
    density_sum = np.sum(optical_density, axis=2)
    brightness = np.max(rgb, axis=2)
    tissue_mask = np.asarray((density_sum >= 0.24) & (brightness <= 248), dtype=np.uint8)
    tissue_mask = cv2.morphologyEx(
        tissue_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )
    tissue_mask = cv2.morphologyEx(
        tissue_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )
    positive = density[tissue_mask > 0]
    if positive.size:
        low, high = np.percentile(positive, (3, 99))
    else:
        low, high = 0.0, 1.0
    morphology = np.clip((density - float(low)) / max(float(high - low), 1e-6), 0.0, 1.0)
    morphology = cv2.GaussianBlur(morphology.astype(np.float32), (0, 0), sigmaX=0.65)
    return morphology, tissue_mask


def _looks_like_pathology(bgr: NDArray[np.uint8], tissue_mask: NDArray[np.uint8]) -> bool:
    brightness = np.max(bgr, axis=2)
    background_fraction = float(np.mean(brightness >= 205))
    tissue_fraction = float(np.mean(tissue_mask > 0))
    if background_fraction < 0.08 or not 0.035 <= tissue_fraction <= 0.94:
        return False
    tissue_pixels = bgr[tissue_mask > 0].astype(np.float32)
    if tissue_pixels.size == 0:
        return False
    blue, green, red = tissue_pixels.T
    stain_like = (red >= green * 0.72) & (blue >= green * 0.62)
    return float(np.mean(stain_like)) >= 0.42


def _multiscale_boxes(
    width: int,
    height: int,
    tissue_mask: NDArray[np.uint8],
) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = [(0, 0, width, height)]
    for fraction in (0.66, 0.50, 0.34):
        box_width = max(32, round(width * fraction))
        box_height = max(32, round(height * fraction))
        x_positions = _positions(width, box_width)
        y_positions = _positions(height, box_height)
        candidates: list[tuple[float, tuple[int, int, int, int]]] = []
        for y in y_positions:
            for x in x_positions:
                local_fraction = float(
                    np.mean(tissue_mask[y : y + box_height, x : x + box_width] > 0)
                )
                candidates.append((local_fraction, (x, y, box_width, box_height)))
        candidates.sort(key=lambda item: (-item[0], item[1][1], item[1][0]))
        boxes.extend(box for _, box in candidates[:12])
    return boxes


def _positions(total: int, length: int) -> list[int]:
    if length >= total:
        return [0]
    step = max(1, length // 2)
    positions = list(range(0, total - length + 1, step))
    if positions[-1] != total - length:
        positions.append(total - length)
    return positions


def _candidate_pairs(regions: list[PathologyRegion]) -> set[tuple[int, int]]:
    index: dict[tuple[int, int], list[int]] = defaultdict(list)
    votes: dict[tuple[int, int], int] = defaultdict(int)
    for region_index, region in enumerate(regions):
        seen_keys: set[tuple[int, int]] = set()
        for fingerprint in region.fingerprints:
            for band in range(8):
                key = (band, (fingerprint.value >> (band * 8)) & 0xFF)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                bucket = index[key]
                if len(bucket) <= PATHOLOGY_INDEX_BUCKET_LIMIT:
                    for previous in bucket:
                        if not _same_or_overlapping_region(regions[previous], region):
                            votes[(previous, region_index)] += 1
                    bucket.append(region_index)
    return {pair for pair, count in votes.items() if count >= PATHOLOGY_CANDIDATE_MIN_VOTES}


def _same_or_overlapping_region(first: PathologyRegion, second: PathologyRegion) -> bool:
    if first.source_path != second.source_path or first.page != second.page:
        return False
    return _intersection_over_union(first.region, second.region) > 0.10


def _best_match(first: PathologyRegion, second: PathologyRegion) -> _PathologyMatch | None:
    first_hash = first.fingerprints[0].value
    best: _PathologyMatch | None = None
    for fingerprint in second.fingerprints:
        distance = hamming_distance(first_hash, fingerprint.value)
        if distance > PATHOLOGY_HASH_MAX_DISTANCE:
            continue
        transformed_structure = _apply_transform(second.structure, fingerprint.transform)
        transformed_mask = _apply_transform(second.tissue_mask, fingerprint.transform)
        structure_similarity = _normalized_correlation(first.structure, transformed_structure)
        mask_iou = _mask_iou(first.tissue_mask, transformed_mask)
        if structure_similarity < 0.86 or mask_iou < 0.50:
            continue
        confidence = max(
            0.0,
            min(
                1.0,
                0.66 * structure_similarity + 0.22 * mask_iou + 0.12 * (1.0 - distance / 64.0),
            ),
        )
        candidate = _PathologyMatch(
            fingerprint.transform,
            distance,
            structure_similarity,
            mask_iou,
            confidence,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def _different_magnification(
    first: PathologyRegion,
    second: PathologyRegion,
) -> tuple[bool, float]:
    if first.magnification and second.magnification:
        ratio = max(first.magnification, second.magnification) / min(
            first.magnification,
            second.magnification,
        )
        return ratio >= 1.45, ratio
    first_fraction = math.sqrt(
        first.region[2] * first.region[3] / max(first.page_width * first.page_height, 1)
    )
    second_fraction = math.sqrt(
        second.region[2] * second.region[3] / max(second.page_width * second.page_height, 1)
    )
    ratio = max(first_fraction, second_fraction) / max(min(first_fraction, second_fraction), 1e-6)
    return ratio >= 1.45, ratio


def _infer_magnification(stem: str) -> float | None:
    match = _MAGNIFICATION_PATTERN.search(stem)
    return float(match.group(1)) if match else None


def _standardize(values: NDArray) -> NDArray[np.float32]:
    result = values.astype(np.float32)
    mean = float(np.mean(result))
    deviation = float(np.std(result))
    if deviation <= 1e-6:
        return np.zeros_like(result, dtype=np.float32)
    return np.ascontiguousarray((result - mean) / deviation, dtype=np.float32)


def _normalized_correlation(first: NDArray, second: NDArray) -> float:
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-8:
        return 0.0
    return max(0.0, min(1.0, float(np.sum(first * second)) / denominator))


def _mask_iou(first: NDArray[np.uint8], second: NDArray[np.uint8]) -> float:
    first_mask = first > 0
    second_mask = second > 0
    intersection = int(np.count_nonzero(first_mask & second_mask))
    union = int(np.count_nonzero(first_mask | second_mask))
    return intersection / max(union, 1)


def _perceptual_hash(values: NDArray[np.float32]) -> int:
    resized = cv2.resize(values, (32, 32), interpolation=cv2.INTER_AREA)
    coefficients = cv2.dct(resized.astype(np.float32))[:8, :8]
    threshold = float(np.median(coefficients.reshape(-1)[1:]))
    return int.from_bytes(np.packbits(coefficients > threshold).tobytes(), "big")


def _apply_transform(values: NDArray, transform: str) -> NDArray:
    if transform == "identity":
        return values
    if transform == "flip_horizontal":
        return np.fliplr(values)
    if transform == "flip_vertical":
        return np.flipud(values)
    if transform == "rotate_180":
        return np.rot90(values, 2)
    raise ValueError(f"未知病理图变换：{transform}")


def _intersection_over_union(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    left = max(first_x, second_x)
    top = max(first_y, second_y)
    right = min(first_x + first_width, second_x + second_width)
    bottom = min(first_y + first_height, second_y + second_height)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = first_width * first_height + second_width * second_height - intersection
    return intersection / max(union, 1)


def _match_details(
    first: PathologyRegion,
    second: PathologyRegion,
    match: _PathologyMatch,
    relationship: str,
    scale_ratio: float,
) -> dict[str, str | int | float]:
    first_x, first_y, first_width, first_height = first.region
    second_x, second_y, second_width, second_height = second.region
    return {
        "evidence_kind": "pathology",
        "relationship_class": relationship,
        "transform_second_to_first": match.transform,
        "first_region_x": first_x,
        "first_region_y": first_y,
        "first_region_width": first_width,
        "first_region_height": first_height,
        "second_region_x": second_x,
        "second_region_y": second_y,
        "second_region_width": second_width,
        "second_region_height": second_height,
        "first_tissue_fraction": round(first.tissue_fraction, 6),
        "second_tissue_fraction": round(second.tissue_fraction, 6),
        "first_magnification": first.magnification or "",
        "second_magnification": second.magnification or "",
        "estimated_scale_ratio": round(scale_ratio, 4),
        "structure_similarity": round(match.structure_similarity, 6),
        "tissue_mask_iou": round(match.tissue_mask_iou, 6),
        "phash_distance": match.hash_distance,
    }


def _location(region: PathologyRegion) -> EvidenceLocation:
    x, y, width, height = region.region
    page_text = f"第 {region.page} 页" if region.page_count > 1 else "图片"
    coordinate = f"{page_text}；组织区域 ({x}, {y}, {width}, {height})"
    return EvidenceLocation(region.source_path, coordinate=coordinate)


def _page_pair_key(first: PathologyRegion, second: PathologyRegion) -> tuple[str, str]:
    first_key = f"{first.source_path}#{first.page}"
    second_key = f"{second.source_path}#{second.page}"
    return tuple(sorted((first_key, second_key)))


def _restore_box(
    box: tuple[int, int, int, int],
    scale: float,
) -> tuple[int, int, int, int]:
    if scale >= 1.0:
        return box
    x, y, width, height = box
    return (
        round(x / scale),
        round(y / scale),
        max(1, round(width / scale)),
        max(1, round(height / scale)),
    )
