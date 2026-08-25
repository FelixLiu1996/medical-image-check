from __future__ import annotations

import itertools
import math
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
from medical_image_check.infrastructure.images import canonical_pixels, to_gray8

DOT_BLOT_RULE_ID = "image.dot_blot.spot_array_reuse"
DOT_BLOT_QUANTILES = (2, 5, 8, 10, 12, 15, 20, 25, 30)
DOT_BLOT_MAX_COMPONENTS = 24
DOT_BLOT_ALL_PAIRS_LIMIT = 64
DOT_BLOT_INDEX_BUCKET_LIMIT = 32
DOT_BLOT_MIN_PROFILE_SIMILARITY = 0.65


@dataclass(frozen=True, slots=True)
class DotBlotSpot:
    x: int
    y: int
    width: int
    height: int
    area: int
    darkness: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class DotBlotRegion:
    source_path: str
    page: int
    page_count: int
    page_width: int
    page_height: int
    region: tuple[int, int, int, int]
    spots: tuple[DotBlotSpot, ...]
    layout_x: tuple[float, ...]
    extraction_score: float


@dataclass(frozen=True, slots=True)
class _DotBlotMatch:
    matched_spot_count: int
    layout_error: float
    layout_similarity: float
    profile_similarity: float
    confidence: float


class DotBlotDuplicateDetector:
    rule_id = DOT_BLOT_RULE_ID

    def extract_from_pages(
        self,
        path: str | Path,
        pages: tuple[NDArray, ...],
    ) -> tuple[DotBlotRegion, ...]:
        source = str(Path(path))
        page_count = len(pages)
        regions: list[DotBlotRegion] = []
        for page_number, page in enumerate(pages, start=1):
            candidate = _extract_page(source, page_number, page_count, page)
            if candidate is not None:
                regions.append(candidate)
        return tuple(regions)

    def findings(
        self,
        regions: list[DotBlotRegion],
        excluded_page_pairs: set[tuple[str, str]] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for pair_index, (first_index, second_index) in enumerate(sorted(_candidate_pairs(regions))):
            if checkpoint and pair_index % 64 == 0:
                checkpoint()
            first = regions[first_index]
            second = regions[second_index]
            page_pair = _page_pair_key(first, second)
            if first.source_path == second.source_path and first.page == second.page:
                continue
            if excluded_page_pairs and page_pair in excluded_page_pairs:
                continue
            match = _best_match(first, second)
            if match is None:
                continue
            locations = (_location(first), _location(second))
            risk = (
                RiskLevel.MEDIUM
                if match.matched_spot_count >= 4 and match.layout_error <= 0.04
                else RiskLevel.LOW
            )
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.rule_id, locations),
                    rule_id=self.rule_id,
                    finding_type=FindingType.SUSPECTED_REUSE,
                    risk=risk,
                    title="Dot blot 斑点阵列疑似复用",
                    description=(
                        "多个近圆形斑点的水平排列在裁剪、缩放和对比度归一化后高度一致，"
                        "需结合原始实验分组复核。"
                    ),
                    locations=locations,
                    confidence=match.confidence,
                    details=_match_details(first, second, match),
                )
            )
        return findings


def _extract_page(
    source_path: str,
    page: int,
    page_count: int,
    image: NDArray,
) -> DotBlotRegion | None:
    gray = to_gray8(canonical_pixels(image))
    height, width = gray.shape[:2]
    if height < 24 or width < 48 or float(np.std(gray)) < 4.0:
        return None

    best: tuple[float, tuple[DotBlotSpot, ...]] | None = None
    for quantile in DOT_BLOT_QUANTILES:
        threshold = float(np.percentile(gray, quantile))
        binary = np.asarray(gray <= threshold, dtype=np.uint8)
        margin = max(3, round(min(height, width) * 0.02))
        binary[:margin, :] = 0
        binary[-margin:, :] = 0
        binary[:, :margin] = 0
        binary[:, -margin:] = 0
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        )
        spots = _components(gray, binary)
        if len(spots) < 3:
            continue
        candidate = _best_aligned_group(spots, width, height)
        if candidate is None:
            continue
        score, group = candidate
        if best is None or score > best[0]:
            best = (score, group)

    if best is None:
        return None
    score, spots = best
    left = min(spot.x for spot in spots)
    top = min(spot.y for spot in spots)
    right = max(spot.x + spot.width for spot in spots)
    bottom = max(spot.y + spot.height for spot in spots)
    pad_x = max(2, round((right - left) * 0.03))
    pad_y = max(2, round((bottom - top) * 0.15))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    centers = [spot.center_x for spot in spots]
    span = max(centers[-1] - centers[0], 1.0)
    layout = tuple((center - centers[0]) / span for center in centers)
    return DotBlotRegion(
        source_path=source_path,
        page=page,
        page_count=page_count,
        page_width=width,
        page_height=height,
        region=(left, top, right - left, bottom - top),
        spots=spots,
        layout_x=layout,
        extraction_score=score,
    )


def _components(gray: NDArray[np.uint8], binary: NDArray[np.uint8]) -> list[DotBlotSpot]:
    height, width = gray.shape[:2]
    count, labels, stats, centers = cv2.connectedComponentsWithStats(binary, connectivity=8)
    minimum_area = max(12, round(height * width * 0.0006))
    spots: list[DotBlotSpot] = []
    for label in range(1, count):
        x, y, component_width, component_height, area = (int(value) for value in stats[label])
        aspect = component_width / max(component_height, 1)
        if (
            area < minimum_area
            or component_width < 4
            or component_height < 4
            or component_width > width * 0.45
            or component_height > height * 0.75
            or not 0.2 <= aspect <= 4.0
        ):
            continue
        fill_ratio = area / max(component_width * component_height, 1)
        if fill_ratio < 0.14:
            continue
        component = labels[y : y + component_height, x : x + component_width] == label
        component_values = gray[y : y + component_height, x : x + component_width][component]
        darkness = float(255.0 - np.mean(component_values))
        center_x, center_y = centers[label]
        restored_x = round(float(center_x) - component_width / 2)
        restored_y = round(float(center_y) - component_height / 2)
        spots.append(
            DotBlotSpot(
                max(0, restored_x),
                max(0, restored_y),
                component_width,
                component_height,
                area,
                darkness,
            )
        )
    return sorted(spots, key=lambda item: (item.center_x, item.center_y))[:DOT_BLOT_MAX_COMPONENTS]


def _best_aligned_group(
    spots: list[DotBlotSpot],
    page_width: int,
    page_height: int,
) -> tuple[float, tuple[DotBlotSpot, ...]] | None:
    best: tuple[float, tuple[DotBlotSpot, ...]] | None = None
    for anchor in spots:
        median_height = float(np.median([spot.height for spot in spots]))
        tolerance = max(8.0, page_height * 0.13, median_height * 0.8)
        aligned = sorted(
            (spot for spot in spots if abs(spot.center_y - anchor.center_y) <= tolerance),
            key=lambda item: item.center_x,
        )
        for length in range(3, min(8, len(aligned)) + 1):
            for start in range(len(aligned) - length + 1):
                group = tuple(aligned[start : start + length])
                centers = np.asarray([spot.center_x for spot in group], dtype=np.float32)
                span = float(centers[-1] - centers[0])
                if span < page_width * 0.14:
                    continue
                gaps = np.diff(centers)
                gap_cv = float(np.std(gaps) / max(np.mean(gaps), 1e-6))
                if gap_cv > 0.48:
                    continue
                y_values = np.asarray([spot.center_y for spot in group], dtype=np.float32)
                y_spread = float(np.std(y_values) / max(median_height, 1.0))
                if y_spread > 0.9:
                    continue
                aspect_quality = float(
                    np.mean(
                        [
                            min(spot.width, spot.height) / max(spot.width, spot.height)
                            for spot in group
                        ]
                    )
                )
                score = (
                    min(length, 4) * 1.2
                    + max(0.0, 1.0 - gap_cv) * 4.0
                    + max(0.0, 1.0 - y_spread) * 1.5
                    + aspect_quality
                    + min(1.0, span / max(page_width * 0.45, 1.0))
                    - max(0, length - 4) * 0.35
                )
                if best is None or score > best[0]:
                    best = (score, group)
    return best


def _best_match(first: DotBlotRegion, second: DotBlotRegion) -> _DotBlotMatch | None:
    best_error = float("inf")
    best_count = 0
    best_profile_similarity = 0.0
    for count in range(min(6, len(first.spots), len(second.spots)), 2, -1):
        for first_indexes in itertools.combinations(range(len(first.spots)), count):
            first_layout = _subset_layout(first, first_indexes)
            for second_indexes in itertools.combinations(range(len(second.spots)), count):
                second_layout = _subset_layout(second, second_indexes)
                error = float(
                    np.sqrt(np.mean((np.asarray(first_layout) - np.asarray(second_layout)) ** 2))
                )
                profile_similarity = _profile_similarity(
                    tuple(first.spots[index] for index in first_indexes),
                    tuple(second.spots[index] for index in second_indexes),
                )
                if profile_similarity < DOT_BLOT_MIN_PROFILE_SIMILARITY:
                    continue
                if error < best_error or (
                    math.isclose(error, best_error) and profile_similarity > best_profile_similarity
                ):
                    best_error = error
                    best_count = count
                    best_profile_similarity = profile_similarity
        if best_count == count and best_error <= 0.055:
            break
    maximum_error = 0.07 if best_count >= 4 else 0.045
    if best_count < 3 or best_error > maximum_error:
        return None
    similarity = max(0.0, 1.0 - best_error / maximum_error)
    confidence = min(
        0.96,
        0.58 + similarity * 0.20 + best_profile_similarity * 0.12 + min(best_count, 5) * 0.012,
    )
    return _DotBlotMatch(
        best_count,
        best_error,
        similarity,
        best_profile_similarity,
        confidence,
    )


def _subset_layout(region: DotBlotRegion, indexes: tuple[int, ...]) -> tuple[float, ...]:
    centers = [region.spots[index].center_x for index in indexes]
    span = max(centers[-1] - centers[0], 1.0)
    return tuple((center - centers[0]) / span for center in centers)


def _profile_similarity(
    first: tuple[DotBlotSpot, ...],
    second: tuple[DotBlotSpot, ...],
) -> float:
    first_darkness = np.asarray([spot.darkness for spot in first], dtype=np.float32)
    second_darkness = np.asarray([spot.darkness for spot in second], dtype=np.float32)
    first_area = np.asarray([spot.area for spot in first], dtype=np.float32)
    second_area = np.asarray([spot.area for spot in second], dtype=np.float32)
    darkness_similarity = abs(_profile_correlation(first_darkness, second_darkness))
    area_similarity = max(0.0, _profile_correlation(first_area, second_area))
    return float(0.65 * darkness_similarity + 0.35 * area_similarity)


def _profile_correlation(first: NDArray, second: NDArray) -> float:
    first_ranks = np.argsort(np.argsort(first)).astype(np.float32)
    second_ranks = np.argsort(np.argsort(second)).astype(np.float32)
    first_ranks -= float(np.mean(first_ranks))
    second_ranks -= float(np.mean(second_ranks))
    denominator = float(np.linalg.norm(first_ranks) * np.linalg.norm(second_ranks))
    if denominator <= 1e-6:
        return 0.0
    return float(np.clip(np.sum(first_ranks * second_ranks) / denominator, -1.0, 1.0))


def _candidate_pairs(regions: list[DotBlotRegion]) -> set[tuple[int, int]]:
    if len(regions) <= DOT_BLOT_ALL_PAIRS_LIMIT:
        return set(itertools.combinations(range(len(regions)), 2))

    index: dict[tuple[int, ...], list[int]] = defaultdict(list)
    pairs: set[tuple[int, int]] = set()
    for region_index, region in enumerate(regions):
        for key in _layout_signatures(region):
            bucket = index[key]
            pairs.update((previous, region_index) for previous in bucket)
            if len(bucket) < DOT_BLOT_INDEX_BUCKET_LIMIT:
                bucket.append(region_index)
            else:
                bucket[region_index % DOT_BLOT_INDEX_BUCKET_LIMIT] = region_index
    return pairs


def _layout_signatures(region: DotBlotRegion) -> set[tuple[int, ...]]:
    signatures: set[tuple[int, ...]] = set()
    for count in (3, 4):
        if len(region.spots) < count:
            continue
        for indexes in itertools.combinations(range(len(region.spots)), count):
            interior = _subset_layout(region, indexes)[1:-1]
            for bins in (8, 12):
                for offset in (0.0, 0.5):
                    quantized = tuple(int(np.floor(value * bins + offset)) for value in interior)
                    signatures.add((count, bins, round(offset * 2), *quantized))
    return signatures


def _location(region: DotBlotRegion) -> EvidenceLocation:
    x, y, width, height = region.region
    parts = []
    if region.page_count > 1:
        parts.append(f"第 {region.page} 页")
    parts.append(f"区域 x={x}, y={y}, w={width}, h={height}")
    return EvidenceLocation(region.source_path, coordinate="；".join(parts))


def _match_details(
    first: DotBlotRegion,
    second: DotBlotRegion,
    match: _DotBlotMatch,
) -> dict[str, object]:
    return {
        "evidence_kind": "dot_blot",
        "matched_spot_count": match.matched_spot_count,
        "layout_error": match.layout_error,
        "layout_similarity": match.layout_similarity,
        "profile_similarity": match.profile_similarity,
        "transformation": "crop_scale_contrast",
        "first_spot_count": len(first.spots),
        "second_spot_count": len(second.spots),
        "first_page": first.page,
        "second_page": second.page,
        "first_region_x": first.region[0],
        "first_region_y": first.region[1],
        "first_region_width": first.region[2],
        "first_region_height": first.region[3],
        "second_region_x": second.region[0],
        "second_region_y": second.region[1],
        "second_region_width": second.region[2],
        "second_region_height": second.region[3],
        "alignment": "按斑点中心的归一化水平排列比较，允许裁剪、缩放和对比度变化",
    }


def _page_pair_key(first: DotBlotRegion, second: DotBlotRegion) -> tuple[str, str]:
    return tuple(
        sorted((f"{first.source_path}#{first.page}", f"{second.source_path}#{second.page}"))
    )
