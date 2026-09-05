from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
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
DOT_BLOT_MAX_COMPONENTS = 32
DOT_BLOT_MAX_REGIONS_PER_PAGE = 8
DOT_BLOT_ALL_PAIRS_LIMIT = 64
DOT_BLOT_INDEX_BUCKET_LIMIT = 32
DOT_BLOT_PATCH_SIZE = 32
DOT_BLOT_MIN_PROFILE_SIMILARITY = 0.60
DOT_BLOT_MIN_APPEARANCE_SIMILARITY = 0.58
AUTO_DOT_MIN_APPEARANCE_SIMILARITY = 0.64
AUTO_DOT_MIN_SPOT_SIMILARITY = 0.44
AUTO_DOT_SHORT_SEQUENCE_MAX_LAYOUT_ERROR = 0.055
AUTO_DOT_GATE_MAX_DIMENSION = 512
AUTO_DOT_GATE_MIN_DIMENSION = 48
AUTO_DOT_GATE_MIN_ROUNDNESS = 0.70
AUTO_DOT_GATE_MIN_CIRCULARITY = 0.65
AUTO_DOT_GATE_MIN_SOLIDITY = 0.70
AUTO_DOT_GATE_MIN_FILL_RATIO = 0.42
AUTO_DOT_GATE_MAX_MEDIAN_CHROMA = 12.0
AUTO_DOT_GATE_MIN_VARIATION = 0.08
AUTO_DOT_GATE_MIN_DIAMETER_FRACTION = 0.10
AUTO_DOT_GATE_EMBEDDED_MIN_DIAMETER_FRACTION = 0.02
AUTO_DOT_GATE_EMBEDDED_MIN_PAGE_DIMENSION = 256
AUTO_DOT_GATE_EMBEDDED_MIN_ROUNDNESS = 0.68
AUTO_DOT_GATE_EMBEDDED_MIN_CIRCULARITY = 0.60
AUTO_DOT_GATE_EMBEDDED_MIN_SOLIDITY = 0.84
AUTO_DOT_GATE_EMBEDDED_MIN_FILL_RATIO = 0.60
AUTO_DOT_GATE_EMBEDDED_MAX_MEDIAN_CHROMA = 8.0
AUTO_DOT_GATE_EMBEDDED_MIN_GROUP_SIZE = 4
AUTO_DOT_GATE_EMBEDDED_MIN_SCORE = 12.0
AUTO_DOT_GATE_EMBEDDED_MIN_SPAN_FRACTION = 0.15
AUTO_DOT_GATE_EMBEDDED_MIN_VARIATION = 0.12
AUTO_DOT_GATE_EMBEDDED_MIN_DISTINCTIVENESS = 0.05


@dataclass(frozen=True, slots=True)
class DotBlotSpot:
    x: int
    y: int
    width: int
    height: int
    area: int
    darkness: float
    local_contrast: float
    circularity: float
    solidity: float
    appearance: NDArray[np.float32] = field(repr=False, compare=False)

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
    axis: tuple[float, float]
    angle_degrees: float
    extraction_score: float
    distinctiveness: float


@dataclass(frozen=True, slots=True)
class _DotBlotMatch:
    first_indexes: tuple[int, ...]
    second_indexes: tuple[int, ...]
    matched_spot_count: int
    layout_error: float
    layout_similarity: float
    profile_similarity: float
    appearance_similarity: float
    minimum_spot_similarity: float
    confidence: float
    scale_second_to_first: float
    rotation_degrees_second_to_first: float
    mirrored: bool


class DotBlotDuplicateDetector:
    rule_id = DOT_BLOT_RULE_ID

    def route_auto_pages(self, pages: tuple[NDArray, ...]) -> tuple[bool, ...]:
        """Return the pages that merit the expensive Dot blot extractor in AUTO mode."""

        return tuple(_auto_dot_gate(page) for page in pages)

    def extract_from_pages(
        self,
        path: str | Path,
        pages: tuple[NDArray, ...],
        *,
        eligible_pages: tuple[bool, ...] | None = None,
    ) -> tuple[DotBlotRegion, ...]:
        if eligible_pages is not None and len(eligible_pages) != len(pages):
            raise ValueError("Dot blot 页面准入结果与图片页数不一致")
        source = str(Path(path))
        page_count = len(pages)
        regions: list[DotBlotRegion] = []
        for page_number, page in enumerate(pages, start=1):
            if eligible_pages is not None and not eligible_pages[page_number - 1]:
                continue
            regions.extend(_extract_page(source, page_number, page_count, page))
        return tuple(regions)

    def findings(
        self,
        regions: list[DotBlotRegion],
        excluded_page_pairs: set[tuple[str, str]] | None = None,
        checkpoint: Callable[[], None] | None = None,
        on_candidate_count: Callable[[int], None] | None = None,
        candidate_pair_limit: int | None = None,
        per_page_pair_limit: int | None = None,
        strict_auto: bool = False,
    ) -> list[Finding]:
        best_by_page_pair: dict[
            tuple[str, str], tuple[DotBlotRegion, DotBlotRegion, _DotBlotMatch]
        ] = {}
        candidates = _select_candidate_pairs(
            regions,
            _candidate_pairs(regions, excluded_page_pairs),
            candidate_pair_limit,
            per_page_pair_limit,
        )
        if on_candidate_count:
            on_candidate_count(len(candidates))
        for pair_index, (first_index, second_index) in enumerate(candidates):
            if checkpoint and pair_index % 64 == 0:
                checkpoint()
            first = regions[first_index]
            second = regions[second_index]
            page_pair = _page_pair_key(first, second)
            match = _best_match(first, second, strict_auto=strict_auto)
            if match is None:
                continue
            previous = best_by_page_pair.get(page_pair)
            if previous is None or _page_match_rank(first, second, match) > _page_match_rank(
                *previous
            ):
                best_by_page_pair[page_pair] = (first, second, match)

        findings: list[Finding] = []
        for page_pair in sorted(best_by_page_pair):
            first, second, match = best_by_page_pair[page_pair]
            locations = (
                _location(first, match.first_indexes),
                _location(second, match.second_indexes),
            )
            risk = (
                RiskLevel.MEDIUM
                if (
                    match.matched_spot_count >= 4
                    and match.layout_error <= 0.04
                    and match.appearance_similarity >= 0.68
                )
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
                        "局部斑点子集的排列、形态和归一化局部图像在裁剪、缩放或旋转后"
                        "保持一致，需结合原始实验分组复核。"
                    ),
                    locations=locations,
                    confidence=match.confidence,
                    details=_match_details(first, second, match),
                )
            )
        return findings


def _auto_dot_gate(image: NDArray) -> bool:
    """Cheaply reject pages whose dark components do not resemble a spot array.

    This gate is deliberately used only by AUTO routing. Explicit Dot blot mode
    keeps the full extractor so that routing heuristics cannot suppress a user-
    selected specialist.
    """

    canonical = canonical_pixels(image)
    bgr = canonical[:, :, :3]
    original_height, original_width = bgr.shape[:2]
    scale = min(
        1.0,
        AUTO_DOT_GATE_MAX_DIMENSION / max(original_height, original_width, 1),
    )
    if scale < 1.0:
        bgr = cv2.resize(
            bgr,
            (
                max(1, round(original_width * scale)),
                max(1, round(original_height * scale)),
            ),
            interpolation=cv2.INTER_AREA,
        )
    gray = to_gray8(bgr)
    height, width = gray.shape[:2]
    if (
        min(height, width) < AUTO_DOT_GATE_MIN_DIMENSION
        or max(height, width) < 96
        or float(np.std(gray)) < 4.0
    ):
        return False

    median = float(np.median(gray))
    bright_fraction = float(np.mean(gray >= 160))
    if median < 72 and bright_fraction < 0.30:
        return False

    binaries: list[tuple[NDArray[np.uint8], bool]] = [
        (np.asarray(gray <= float(np.percentile(gray, 12)), dtype=np.uint8), False)
    ]
    sigma = max(6.0, min(height, width) * 0.09)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma).astype(np.float32)
    response = background - gray.astype(np.float32)
    positive = response[response >= 3.0]
    if positive.size:
        for quantile in (25, 55):
            threshold = max(4.0, float(np.percentile(positive, quantile)))
            binaries.append((np.asarray(response >= threshold, dtype=np.uint8), False))
        embedded_threshold = max(4.0, float(np.percentile(positive, 10)))
        binaries.append((np.asarray(response >= embedded_threshold, dtype=np.uint8), True))

    standard_minimum_diameter = max(
        5.0,
        min(height, width) * AUTO_DOT_GATE_MIN_DIAMETER_FRACTION,
    )
    embedded_minimum_diameter = max(
        5.0,
        min(height, width) * AUTO_DOT_GATE_EMBEDDED_MIN_DIAMETER_FRACTION,
    )
    for binary, embedded_only in binaries:
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
        components = _components(gray, binary)
        if not embedded_only:
            standard_spots = [
                spot
                for spot in components
                if _auto_dot_spot_is_eligible(spot, bgr, standard_minimum_diameter)
            ]
            if _auto_dot_group_is_eligible(standard_spots, width, height, embedded=False):
                return True
        if min(height, width) < AUTO_DOT_GATE_EMBEDDED_MIN_PAGE_DIMENSION:
            continue
        embedded_spots = [
            spot
            for spot in components
            if _auto_dot_spot_is_eligible(
                spot,
                bgr,
                embedded_minimum_diameter,
                embedded=True,
            )
        ]
        if _auto_dot_group_is_eligible(embedded_spots, width, height, embedded=True):
            return True
    return False


def _auto_dot_spot_is_eligible(
    spot: DotBlotSpot,
    bgr: NDArray,
    minimum_diameter: float,
    *,
    embedded: bool = False,
) -> bool:
    roundness = min(spot.width, spot.height) / max(spot.width, spot.height)
    fill_ratio = spot.area / max(spot.width * spot.height, 1)
    minimum_roundness = (
        AUTO_DOT_GATE_EMBEDDED_MIN_ROUNDNESS if embedded else AUTO_DOT_GATE_MIN_ROUNDNESS
    )
    minimum_circularity = (
        AUTO_DOT_GATE_EMBEDDED_MIN_CIRCULARITY if embedded else AUTO_DOT_GATE_MIN_CIRCULARITY
    )
    minimum_solidity = (
        AUTO_DOT_GATE_EMBEDDED_MIN_SOLIDITY if embedded else AUTO_DOT_GATE_MIN_SOLIDITY
    )
    minimum_fill_ratio = (
        AUTO_DOT_GATE_EMBEDDED_MIN_FILL_RATIO if embedded else AUTO_DOT_GATE_MIN_FILL_RATIO
    )
    if (
        max(spot.width, spot.height) < minimum_diameter
        or roundness < minimum_roundness
        or spot.circularity < minimum_circularity
        or spot.solidity < minimum_solidity
        or fill_ratio < minimum_fill_ratio
    ):
        return False
    local = bgr[spot.y : spot.y + spot.height, spot.x : spot.x + spot.width].astype(np.float32)
    if local.size == 0:
        return False
    chroma = np.max(local, axis=2) - np.min(local, axis=2)
    maximum_chroma = (
        AUTO_DOT_GATE_EMBEDDED_MAX_MEDIAN_CHROMA if embedded else AUTO_DOT_GATE_MAX_MEDIAN_CHROMA
    )
    return float(np.median(chroma)) <= maximum_chroma


def _auto_dot_group_is_eligible(
    spots: list[DotBlotSpot],
    width: int,
    height: int,
    *,
    embedded: bool,
) -> bool:
    minimum_group_size = AUTO_DOT_GATE_EMBEDDED_MIN_GROUP_SIZE if embedded else 3
    if len(spots) < minimum_group_size:
        return False
    aligned_groups = _aligned_groups(spots, width, height)
    chart_bar_ids = {
        id(spot)
        for _, group, axis in aligned_groups
        if len(group) >= minimum_group_size and _looks_like_bar_series(group, axis)
        for spot in group
    }
    if chart_bar_ids:
        spots = [spot for spot in spots if id(spot) not in chart_bar_ids]
        if len(spots) < minimum_group_size:
            return False
        aligned_groups = _aligned_groups(spots, width, height)
    diagonal = math.hypot(width, height)
    for score, group, axis in aligned_groups:
        if len(group) < minimum_group_size:
            continue
        areas = np.asarray([spot.area for spot in group], dtype=np.float32)
        contrasts = np.asarray([spot.local_contrast for spot in group], dtype=np.float32)
        variation = max(
            float(np.std(areas) / max(np.mean(areas), 1e-6)),
            float(np.std(contrasts) / max(np.mean(contrasts), 1e-6)),
        )
        if not embedded:
            if variation >= AUTO_DOT_GATE_MIN_VARIATION:
                return True
            continue
        centers = np.asarray(
            [(spot.center_x, spot.center_y) for spot in group],
            dtype=np.float32,
        )
        projections = centers @ np.asarray(axis, dtype=np.float32)
        span_fraction = float(np.ptp(projections)) / max(diagonal, 1.0)
        if (
            score >= AUTO_DOT_GATE_EMBEDDED_MIN_SCORE
            and span_fraction >= AUTO_DOT_GATE_EMBEDDED_MIN_SPAN_FRACTION
            and variation >= AUTO_DOT_GATE_EMBEDDED_MIN_VARIATION
            and _spot_distinctiveness(group) >= AUTO_DOT_GATE_EMBEDDED_MIN_DISTINCTIVENESS
        ):
            return True
    return False


def _looks_like_bar_series(
    spots: tuple[DotBlotSpot, ...],
    axis: tuple[float, float],
) -> bool:
    """Reject chart bars that otherwise resemble a row of dark spots.

    Bars share a baseline and a nearly constant thickness while their lengths
    or rectangular aspect ratios vary.  Real Dot blot spots can share a centre
    line, but differently sized spots do not keep the same outer edge.
    """

    if len(spots) < 3:
        return False
    horizontal_series = abs(axis[0]) >= abs(axis[1])
    if horizontal_series:
        thicknesses = np.asarray([spot.width for spot in spots], dtype=np.float32)
        lengths = np.asarray([spot.height for spot in spots], dtype=np.float32)
        leading_edges = np.asarray([spot.y for spot in spots], dtype=np.float32)
        trailing_edges = np.asarray([spot.y + spot.height for spot in spots], dtype=np.float32)
    else:
        thicknesses = np.asarray([spot.height for spot in spots], dtype=np.float32)
        lengths = np.asarray([spot.width for spot in spots], dtype=np.float32)
        leading_edges = np.asarray([spot.x for spot in spots], dtype=np.float32)
        trailing_edges = np.asarray([spot.x + spot.width for spot in spots], dtype=np.float32)
    thickness_cv = float(np.std(thicknesses) / max(np.mean(thicknesses), 1e-6))
    edge_spread = min(float(np.ptp(leading_edges)), float(np.ptp(trailing_edges)))
    center_spread = float(np.ptp(leading_edges + lengths / 2.0))
    edge_tolerance = max(2.0, float(np.median(lengths)) * 0.12)
    median_roundness = float(
        np.median([min(spot.width, spot.height) / max(spot.width, spot.height) for spot in spots])
    )
    return (
        thickness_cv <= 0.12
        and edge_spread <= edge_tolerance
        and (center_spread >= edge_spread * 2.0 + 2.0 or median_roundness <= 0.72)
    )


def _extract_page(
    source_path: str,
    page: int,
    page_count: int,
    image: NDArray,
) -> tuple[DotBlotRegion, ...]:
    gray = to_gray8(canonical_pixels(image))
    height, width = gray.shape[:2]
    if height < 24 or width < 48 or float(np.std(gray)) < 4.0:
        return ()

    median = float(np.median(gray))
    bright_fraction = float(np.mean(gray >= 160))
    if median < 72 and bright_fraction < 0.30:
        return ()

    candidates: list[DotBlotRegion] = []
    for binary in _binary_candidates(gray):
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
        for score, group, axis in _aligned_groups(spots, width, height):
            candidates.append(
                _make_region(
                    source_path,
                    page,
                    page_count,
                    width,
                    height,
                    score,
                    group,
                    axis,
                )
            )

    selected: list[DotBlotRegion] = []
    for candidate in sorted(candidates, key=lambda item: item.extraction_score, reverse=True):
        if any(_same_row_region(candidate, existing) for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= DOT_BLOT_MAX_REGIONS_PER_PAGE:
            break
    return tuple(selected)


def _binary_candidates(gray: NDArray[np.uint8]) -> tuple[NDArray[np.uint8], ...]:
    candidates = [
        np.asarray(gray <= float(np.percentile(gray, quantile)), dtype=np.uint8)
        for quantile in DOT_BLOT_QUANTILES
    ]
    sigma = max(6.0, min(gray.shape[:2]) * 0.09)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma).astype(np.float32)
    response = background - gray.astype(np.float32)
    positive = response[response >= 3.0]
    if positive.size:
        response_peak = float(np.percentile(positive, 99.5))
        for fraction in (0.06, 0.10, 0.16):
            threshold = max(4.0, response_peak * fraction)
            candidates.append(np.asarray(response >= threshold, dtype=np.uint8))
        for quantile in (10, 25, 40, 55, 70, 82):
            threshold = max(4.0, float(np.percentile(positive, quantile)))
            candidates.append(np.asarray(response >= threshold, dtype=np.uint8))
    return tuple(candidates)


def _components(gray: NDArray[np.uint8], binary: NDArray[np.uint8]) -> list[DotBlotSpot]:
    height, width = gray.shape[:2]
    count, labels, stats, centers = cv2.connectedComponentsWithStats(binary, connectivity=8)
    minimum_area = max(10, round(height * width * 0.00025))
    spots: list[DotBlotSpot] = []
    for label in range(1, count):
        x, y, component_width, component_height, area = (int(value) for value in stats[label])
        aspect = component_width / max(component_height, 1)
        if (
            area < minimum_area
            or component_width < 4
            or component_height < 4
            or component_width > width * 0.42
            or component_height > height * 0.70
            or not 0.30 <= aspect <= 3.20
        ):
            continue
        fill_ratio = area / max(component_width * component_height, 1)
        if fill_ratio < 0.17:
            continue

        component_mask = np.asarray(
            labels[y : y + component_height, x : x + component_width] == label,
            dtype=np.uint8,
        )
        contours, _ = cv2.findContours(
            component_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        contour_area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        circularity = 4.0 * math.pi * contour_area / max(perimeter * perimeter, 1e-6)
        hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
        solidity = contour_area / max(hull_area, 1e-6)
        if circularity < 0.13 or solidity < 0.32:
            continue

        component_values = gray[y : y + component_height, x : x + component_width][
            component_mask.astype(bool)
        ]
        component_mean = float(np.mean(component_values))
        local_background = _local_background(
            gray,
            x,
            y,
            component_width,
            component_height,
        )
        local_contrast = local_background - component_mean
        if local_contrast < 4.0:
            continue

        center_x, center_y = (float(value) for value in centers[label])
        restored_x = round(center_x - component_width / 2)
        restored_y = round(center_y - component_height / 2)
        appearance = _spot_appearance(
            gray,
            center_x,
            center_y,
            max(component_width, component_height),
        )
        spots.append(
            DotBlotSpot(
                max(0, restored_x),
                max(0, restored_y),
                component_width,
                component_height,
                area,
                255.0 - component_mean,
                local_contrast,
                float(np.clip(circularity, 0.0, 1.0)),
                float(np.clip(solidity, 0.0, 1.0)),
                appearance,
            )
        )
    return sorted(spots, key=lambda item: (item.center_x, item.center_y))[:DOT_BLOT_MAX_COMPONENTS]


def _local_background(
    gray: NDArray[np.uint8],
    x: int,
    y: int,
    width: int,
    height: int,
) -> float:
    padding = max(3, round(max(width, height) * 0.35))
    left = max(0, x - padding)
    top = max(0, y - padding)
    right = min(gray.shape[1], x + width + padding)
    bottom = min(gray.shape[0], y + height + padding)
    window = gray[top:bottom, left:right]
    if window.size == 0:
        return 255.0
    ring = np.ones(window.shape, dtype=bool)
    inner_left = x - left
    inner_top = y - top
    ring[inner_top : inner_top + height, inner_left : inner_left + width] = False
    values = window[ring]
    if values.size == 0:
        values = window.reshape(-1)
    return float(np.percentile(values, 70))


def _spot_appearance(
    gray: NDArray[np.uint8],
    center_x: float,
    center_y: float,
    component_size: int,
) -> NDArray[np.float32]:
    side = max(12, round(component_size * 1.70))
    patch = cv2.getRectSubPix(gray, (side, side), (center_x, center_y))
    patch = cv2.resize(
        patch,
        (DOT_BLOT_PATCH_SIZE, DOT_BLOT_PATCH_SIZE),
        interpolation=cv2.INTER_AREA if side >= DOT_BLOT_PATCH_SIZE else cv2.INTER_CUBIC,
    ).astype(np.float32)
    border = np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1]))
    background = float(np.percentile(border, 75))
    signal = background - patch
    high = float(np.percentile(signal, 98))
    if high <= 2.0:
        return np.zeros_like(signal, dtype=np.float32)
    signal = np.clip(signal / high, 0.0, 1.0)
    return cv2.GaussianBlur(signal, (3, 3), 0.0).astype(np.float32)


def _aligned_groups(
    spots: list[DotBlotSpot],
    page_width: int,
    page_height: int,
) -> list[tuple[float, tuple[DotBlotSpot, ...], tuple[float, float]]]:
    centers = np.asarray([(spot.center_x, spot.center_y) for spot in spots], dtype=np.float32)
    diameters = np.asarray([max(spot.width, spot.height) for spot in spots], dtype=np.float32)
    median_diameter = float(np.median(diameters))
    diagonal = math.hypot(page_width, page_height)
    line_tolerance = max(6.0, median_diameter * 0.72, diagonal * 0.012)
    minimum_span = max(median_diameter * 2.0, diagonal * 0.08)
    best_by_spots: dict[
        tuple[int, ...], tuple[float, tuple[DotBlotSpot, ...], tuple[float, float]]
    ] = {}

    for first_index, second_index in itertools.combinations(range(len(spots)), 2):
        direction = centers[second_index] - centers[first_index]
        norm = float(np.linalg.norm(direction))
        if norm < max(10.0, median_diameter * 0.65):
            continue
        axis = direction / norm
        if axis[0] < 0 or (abs(float(axis[0])) <= 1e-6 and axis[1] < 0):
            axis = -axis
        perpendicular = np.asarray((-axis[1], axis[0]), dtype=np.float32)
        anchor = centers[first_index]
        distances = np.abs((centers - anchor) @ perpendicular)
        inliers = np.flatnonzero(distances <= line_tolerance)
        if len(inliers) < 3:
            continue
        projections = centers[inliers] @ axis
        ordered = inliers[np.argsort(projections)]
        maximum_length = min(12, len(ordered))
        for length in range(3, maximum_length + 1):
            for start in range(len(ordered) - length + 1):
                indexes = tuple(int(value) for value in ordered[start : start + length])
                subset_centers = centers[list(indexes)]
                refined_axis = _principal_axis(subset_centers, axis)
                subset_projections = subset_centers @ refined_axis
                order = np.argsort(subset_projections)
                indexes = tuple(indexes[int(position)] for position in order)
                subset_projections = subset_projections[order]
                span = float(subset_projections[-1] - subset_projections[0])
                if span < minimum_span:
                    continue
                gaps = np.diff(subset_projections)
                if np.min(gaps) < max(2.0, median_diameter * 0.22):
                    continue
                gap_cv = float(np.std(gaps) / max(np.mean(gaps), 1e-6))
                if gap_cv > 0.62:
                    continue
                refined_perpendicular = np.asarray(
                    (-refined_axis[1], refined_axis[0]), dtype=np.float32
                )
                centered = subset_centers - np.mean(subset_centers, axis=0)
                cross_spread = float(
                    np.std(centered @ refined_perpendicular) / max(median_diameter, 1.0)
                )
                if cross_spread > 0.72:
                    continue
                group = tuple(spots[index] for index in indexes)
                shape_quality = float(
                    np.mean(
                        [
                            min(1.0, spot.circularity / 0.72) * 0.55
                            + min(1.0, spot.solidity / 0.90) * 0.45
                            for spot in group
                        ]
                    )
                )
                contrast_quality = min(
                    1.0,
                    float(np.median([spot.local_contrast for spot in group])) / 45.0,
                )
                score = (
                    min(length, 8) * 1.35
                    + max(0.0, 1.0 - gap_cv) * 3.0
                    + max(0.0, 1.0 - cross_spread) * 2.0
                    + shape_quality * 1.6
                    + contrast_quality
                    + min(1.0, span / max(diagonal * 0.38, 1.0))
                )
                key = tuple(sorted(indexes))
                candidate = (
                    score,
                    group,
                    (float(refined_axis[0]), float(refined_axis[1])),
                )
                previous = best_by_spots.get(key)
                if previous is None or score > previous[0]:
                    best_by_spots[key] = candidate
    return sorted(best_by_spots.values(), key=lambda item: item[0], reverse=True)[:32]


def _principal_axis(
    points: NDArray[np.float32],
    fallback: NDArray[np.float32],
) -> NDArray[np.float32]:
    centered = points - np.mean(points, axis=0)
    _, _, vectors = np.linalg.svd(centered, full_matrices=False)
    axis = vectors[0].astype(np.float32)
    if float(np.dot(axis, fallback)) < 0:
        axis = -axis
    if axis[0] < 0 or (abs(float(axis[0])) <= 1e-6 and axis[1] < 0):
        axis = -axis
    return axis / max(float(np.linalg.norm(axis)), 1e-6)


def _make_region(
    source_path: str,
    page: int,
    page_count: int,
    page_width: int,
    page_height: int,
    score: float,
    spots: tuple[DotBlotSpot, ...],
    axis: tuple[float, float],
) -> DotBlotRegion:
    left = min(spot.x for spot in spots)
    top = min(spot.y for spot in spots)
    right = max(spot.x + spot.width for spot in spots)
    bottom = max(spot.y + spot.height for spot in spots)
    pad_x = max(2, round((right - left) * 0.03))
    pad_y = max(2, round((bottom - top) * 0.15))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(page_width, right + pad_x)
    bottom = min(page_height, bottom + pad_y)
    projections = [spot.center_x * axis[0] + spot.center_y * axis[1] for spot in spots]
    span = max(projections[-1] - projections[0], 1.0)
    layout = tuple((value - projections[0]) / span for value in projections)
    angle = math.degrees(math.atan2(axis[1], axis[0]))
    return DotBlotRegion(
        source_path=source_path,
        page=page,
        page_count=page_count,
        page_width=page_width,
        page_height=page_height,
        region=(left, top, right - left, bottom - top),
        spots=spots,
        layout_x=layout,
        axis=axis,
        angle_degrees=angle,
        extraction_score=score,
        distinctiveness=_spot_distinctiveness(spots),
    )


def _same_row_region(first: DotBlotRegion, second: DotBlotRegion) -> bool:
    if _angle_difference(first.angle_degrees, second.angle_degrees) > 12.0:
        return False
    if _rectangle_iou(first.region, second.region) >= 0.42:
        return True
    first_center = _rectangle_center(first.region)
    second_center = _rectangle_center(second.region)
    distance = math.dist(first_center, second_center)
    scale = max(10.0, min(first.region[2], second.region[2]) * 0.12)
    width_ratio = first.region[2] / max(second.region[2], 1)
    return distance <= scale and 0.68 <= width_ratio <= 1.47


def _best_match(
    first: DotBlotRegion,
    second: DotBlotRegion,
    *,
    strict_auto: bool = False,
) -> _DotBlotMatch | None:
    best: _DotBlotMatch | None = None
    appearance_caches: dict[bool, _SequenceAppearanceCache] = {}
    first_layouts: dict[tuple[int, ...], tuple[float, ...]] = {}
    second_layouts: dict[tuple[int, ...], tuple[float, ...]] = {}
    first_subsets: dict[tuple[int, ...], tuple[DotBlotSpot, ...]] = {}
    second_subsets: dict[tuple[int, ...], tuple[DotBlotSpot, ...]] = {}
    first_distinctiveness: dict[tuple[int, ...], float] = {}
    second_distinctiveness: dict[tuple[int, ...], float] = {}
    relative_rotations = {
        reversed_order: _relative_rotation(first, second, reversed_order)
        for reversed_order in (False, True)
    }
    maximum_count = min(8, len(first.spots), len(second.spots))
    for count in range(maximum_count, 2, -1):
        if count == 3 and len(first.spots) == 3 and len(second.spots) == 3:
            continue
        maximum_error = 0.065 if count >= 4 else 0.048
        for first_indexes in _near_contiguous_subsets(len(first.spots), count):
            first_layout = first_layouts.get(first_indexes)
            if first_layout is None:
                first_layout = _subset_layout(first, first_indexes)
                first_layouts[first_indexes] = first_layout
            for natural_second_indexes in _near_contiguous_subsets(len(second.spots), count):
                for reversed_order in (False, True):
                    second_indexes = (
                        tuple(reversed(natural_second_indexes))
                        if reversed_order
                        else natural_second_indexes
                    )
                    second_layout = second_layouts.get(second_indexes)
                    if second_layout is None:
                        second_layout = _subset_layout(second, second_indexes)
                        second_layouts[second_indexes] = second_layout
                    error = float(
                        np.sqrt(
                            np.mean((np.asarray(first_layout) - np.asarray(second_layout)) ** 2)
                        )
                    )
                    if error > maximum_error:
                        continue
                    first_spots = first_subsets.get(first_indexes)
                    if first_spots is None:
                        first_spots = tuple(first.spots[index] for index in first_indexes)
                        first_subsets[first_indexes] = first_spots
                    second_spots = second_subsets.get(second_indexes)
                    if second_spots is None:
                        second_spots = tuple(second.spots[index] for index in second_indexes)
                        second_subsets[second_indexes] = second_spots
                    profile_similarity = _profile_similarity(first_spots, second_spots)
                    minimum_profile = 0.42 if count == 3 else DOT_BLOT_MIN_PROFILE_SIMILARITY
                    if profile_similarity < minimum_profile:
                        continue
                    first_subset_distinctiveness = first_distinctiveness.get(first_indexes)
                    if first_subset_distinctiveness is None:
                        first_subset_distinctiveness = _spot_distinctiveness(first_spots)
                        first_distinctiveness[first_indexes] = first_subset_distinctiveness
                    second_subset_distinctiveness = second_distinctiveness.get(second_indexes)
                    if second_subset_distinctiveness is None:
                        second_subset_distinctiveness = _spot_distinctiveness(second_spots)
                        second_distinctiveness[second_indexes] = second_subset_distinctiveness
                    distinctiveness = min(
                        first_subset_distinctiveness,
                        second_subset_distinctiveness,
                    )
                    if max(first_subset_distinctiveness, second_subset_distinctiveness) < 0.30:
                        continue
                    appearance_cache = appearance_caches.get(reversed_order)
                    if appearance_cache is None:
                        appearance_cache = _SequenceAppearanceCache(
                            first,
                            second,
                            relative_rotations[reversed_order],
                        )
                        appearance_caches[reversed_order] = appearance_cache
                    appearance, minimum_spot, mirrored = _sequence_appearance_similarity(
                        first_indexes,
                        second_indexes,
                        appearance_cache,
                    )
                    if appearance < DOT_BLOT_MIN_APPEARANCE_SIMILARITY or minimum_spot < 0.34:
                        continue
                    if strict_auto and (
                        appearance < AUTO_DOT_MIN_APPEARANCE_SIMILARITY
                        or minimum_spot < AUTO_DOT_MIN_SPOT_SIMILARITY
                        or (count <= 4 and error > AUTO_DOT_SHORT_SEQUENCE_MAX_LAYOUT_ERROR)
                    ):
                        continue
                    if count == 3 and (
                        appearance < 0.68
                        or minimum_spot < 0.40
                        or (distinctiveness < 0.14 and appearance < 0.80)
                    ):
                        continue
                    similarity = max(0.0, 1.0 - error / maximum_error)
                    count_support = min(count, 6) / 6.0
                    confidence = min(
                        0.97,
                        0.36
                        + similarity * 0.16
                        + appearance * 0.28
                        + profile_similarity * 0.10
                        + count_support * 0.07,
                    )
                    first_span = _subset_span(first, first_indexes)
                    second_span = _subset_span(second, second_indexes)
                    candidate = _DotBlotMatch(
                        first_indexes=first_indexes,
                        second_indexes=second_indexes,
                        matched_spot_count=count,
                        layout_error=error,
                        layout_similarity=similarity,
                        profile_similarity=profile_similarity,
                        appearance_similarity=appearance,
                        minimum_spot_similarity=minimum_spot,
                        confidence=confidence,
                        scale_second_to_first=first_span / max(second_span, 1e-6),
                        rotation_degrees_second_to_first=relative_rotations[reversed_order],
                        mirrored=mirrored,
                    )
                    if best is None or _match_rank(candidate) > _match_rank(best):
                        best = candidate
        if best is not None and best.matched_spot_count == count:
            break
    return best


@cache
def _near_contiguous_subsets(length: int, count: int) -> tuple[tuple[int, ...], ...]:
    subsets: list[tuple[int, ...]] = []
    for indexes in itertools.combinations(range(length), count):
        if indexes[-1] - indexes[0] + 1 <= count + 1:
            subsets.append(indexes)
    return tuple(subsets)


def _subset_layout(region: DotBlotRegion, indexes: tuple[int, ...]) -> tuple[float, ...]:
    projections = [
        region.spots[index].center_x * region.axis[0]
        + region.spots[index].center_y * region.axis[1]
        for index in indexes
    ]
    if projections[-1] < projections[0]:
        projections = [-value for value in projections]
    span = max(projections[-1] - projections[0], 1.0)
    return tuple((value - projections[0]) / span for value in projections)


def _subset_span(region: DotBlotRegion, indexes: tuple[int, ...]) -> float:
    spots = [region.spots[index] for index in indexes]
    first = spots[0]
    last = spots[-1]
    return math.hypot(last.center_x - first.center_x, last.center_y - first.center_y)


def _relative_rotation(
    first: DotBlotRegion,
    second: DotBlotRegion,
    reversed_order: bool,
) -> float:
    second_angle = second.angle_degrees + (180.0 if reversed_order else 0.0)
    return _normalize_angle(first.angle_degrees - second_angle)


class _SequenceAppearanceCache:
    """Cache only spot pairs reached by the cheap layout/profile filters."""

    def __init__(
        self,
        first: DotBlotRegion,
        second: DotBlotRegion,
        rotation: float,
    ) -> None:
        self._first = first
        self._second = second
        self._rotation = rotation
        self._transformed: dict[
            int,
            tuple[NDArray[np.float32], NDArray[np.float32]],
        ] = {}
        self._similarities: dict[tuple[int, int], tuple[float, float]] = {}

    def similarities(self, first_index: int, second_index: int) -> tuple[float, float]:
        key = (first_index, second_index)
        cached = self._similarities.get(key)
        if cached is not None:
            return cached

        transformed = self._transformed.get(second_index)
        if transformed is None:
            rotated = _rotate_patch(
                self._second.spots[second_index].appearance,
                self._rotation,
            )
            transformed = (rotated, cv2.flip(rotated, 1))
            self._transformed[second_index] = transformed

        first_appearance = self._first.spots[first_index].appearance
        # The former matrices stored float32 values. Preserve that rounding so
        # lazy evaluation cannot change a threshold or match-rank decision.
        similarities = (
            float(np.float32(_patch_similarity(first_appearance, transformed[0]))),
            float(np.float32(_patch_similarity(first_appearance, transformed[1]))),
        )
        self._similarities[key] = similarities
        return similarities


def _sequence_appearance_similarity(
    first_indexes: tuple[int, ...],
    second_indexes: tuple[int, ...],
    cache: _SequenceAppearanceCache,
) -> tuple[float, float, bool]:
    similarities = [
        cache.similarities(first_index, second_index)
        for first_index, second_index in zip(first_indexes, second_indexes, strict=True)
    ]
    direct = [item[0] for item in similarities]
    mirrored = [item[1] for item in similarities]
    direct_mean = float(np.mean(direct))
    mirrored_mean = float(np.mean(mirrored))
    use_mirror = mirrored_mean > direct_mean + 0.025
    selected = mirrored if use_mirror else direct
    return float(np.mean(selected)), float(np.min(selected)), use_mirror


def _rotate_patch(patch: NDArray[np.float32], angle: float) -> NDArray[np.float32]:
    if abs(angle) < 0.5:
        return patch
    center = ((patch.shape[1] - 1) / 2.0, (patch.shape[0] - 1) / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        patch,
        matrix,
        (patch.shape[1], patch.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    ).astype(np.float32)


def _patch_similarity(first: NDArray[np.float32], second: NDArray[np.float32]) -> float:
    padding = 2
    search = cv2.copyMakeBorder(
        second,
        padding,
        padding,
        padding,
        padding,
        cv2.BORDER_CONSTANT,
        value=0.0,
    )
    response = cv2.matchTemplate(search, first, cv2.TM_CCOEFF_NORMED)
    _, ncc, _, location = cv2.minMaxLoc(response)
    left, top = location
    aligned = search[top : top + first.shape[0], left : left + first.shape[1]]
    first_mask = first >= 0.28
    second_mask = aligned >= 0.28
    intersection = float(np.logical_and(first_mask, second_mask).sum())
    union = float(np.logical_or(first_mask, second_mask).sum())
    mask_iou = intersection / max(union, 1.0)
    first_gradient = cv2.Laplacian(first, cv2.CV_32F)
    second_gradient = cv2.Laplacian(aligned, cv2.CV_32F)
    gradient_similarity = max(0.0, _normalized_correlation(first_gradient, second_gradient))
    return float(
        np.clip(max(0.0, ncc) * 0.58 + mask_iou * 0.27 + gradient_similarity * 0.15, 0.0, 1.0)
    )


def _normalized_correlation(first: NDArray, second: NDArray) -> float:
    first_values = first.astype(np.float32).reshape(-1)
    second_values = second.astype(np.float32).reshape(-1)
    first_values -= float(np.mean(first_values))
    second_values -= float(np.mean(second_values))
    denominator = float(np.linalg.norm(first_values) * np.linalg.norm(second_values))
    if denominator <= 1e-6:
        return 0.0
    return float(np.clip(np.dot(first_values, second_values) / denominator, -1.0, 1.0))


def _profile_similarity(
    first: tuple[DotBlotSpot, ...],
    second: tuple[DotBlotSpot, ...],
) -> float:
    first_contrast = np.asarray([spot.local_contrast for spot in first], dtype=np.float32)
    second_contrast = np.asarray([spot.local_contrast for spot in second], dtype=np.float32)
    first_area = np.asarray([spot.area for spot in first], dtype=np.float32)
    second_area = np.asarray([spot.area for spot in second], dtype=np.float32)
    contrast_similarity = _scale_invariant_profile_similarity(first_contrast, second_contrast)
    area_similarity = _scale_invariant_profile_similarity(first_area, second_area)
    shape_similarity = float(
        np.mean(
            [
                max(0.0, 1.0 - abs(left.circularity - right.circularity)) * 0.55
                + max(0.0, 1.0 - abs(left.solidity - right.solidity)) * 0.45
                for left, right in zip(first, second, strict=True)
            ]
        )
    )
    return float(contrast_similarity * 0.42 + area_similarity * 0.33 + shape_similarity * 0.25)


def _scale_invariant_profile_similarity(first: NDArray, second: NDArray) -> float:
    first_values = first.astype(np.float32) / max(float(np.median(first)), 1e-6)
    second_values = second.astype(np.float32) / max(float(np.median(second)), 1e-6)
    log_error = float(
        np.mean(np.abs(np.log(np.maximum(first_values, 1e-4) / np.maximum(second_values, 1e-4))))
    )
    return float(math.exp(-log_error))


def _spot_distinctiveness(spots: tuple[DotBlotSpot, ...]) -> float:
    if len(spots) < 2:
        return 0.0
    contrasts = np.asarray([spot.local_contrast for spot in spots], dtype=np.float32)
    areas = np.asarray([spot.area for spot in spots], dtype=np.float32)
    contrast_cv = float(np.std(contrasts) / max(np.mean(contrasts), 1e-6))
    area_cv = float(np.std(areas) / max(np.mean(areas), 1e-6))
    patch_differences = [
        1.0 - max(0.0, _normalized_correlation(first.appearance, second.appearance))
        for first, second in itertools.combinations(spots, 2)
    ]
    patch_diversity = float(np.mean(patch_differences)) if patch_differences else 0.0
    return float(np.clip(contrast_cv * 0.30 + area_cv * 0.35 + patch_diversity * 0.35, 0.0, 1.0))


def _candidate_pairs(
    regions: list[DotBlotRegion],
    excluded_page_pairs: set[tuple[str, str]] | None = None,
) -> set[tuple[int, int]]:
    if len(regions) <= DOT_BLOT_ALL_PAIRS_LIMIT:
        return {
            (first, second)
            for first, second in itertools.combinations(range(len(regions)), 2)
            if _candidate_pair_is_eligible(
                regions[first],
                regions[second],
                excluded_page_pairs,
            )
        }

    index: dict[tuple[int, ...], list[int]] = defaultdict(list)
    pairs: set[tuple[int, int]] = set()
    for region_index, region in enumerate(regions):
        for key in _layout_signatures(region):
            bucket = index[key]
            pairs.update(
                (previous, region_index)
                for previous in bucket
                if _candidate_pair_is_eligible(
                    regions[previous],
                    region,
                    excluded_page_pairs,
                )
            )
            if len(bucket) < DOT_BLOT_INDEX_BUCKET_LIMIT:
                bucket.append(region_index)
            else:
                bucket[region_index % DOT_BLOT_INDEX_BUCKET_LIMIT] = region_index
    return pairs


def _candidate_pair_is_eligible(
    first: DotBlotRegion,
    second: DotBlotRegion,
    excluded_page_pairs: set[tuple[str, str]] | None,
) -> bool:
    if first.source_path == second.source_path and first.page == second.page:
        return False
    return not excluded_page_pairs or _page_pair_key(first, second) not in excluded_page_pairs


def _select_candidate_pairs(
    regions: list[DotBlotRegion],
    candidates: set[tuple[int, int]],
    pair_limit: int | None,
    per_page_pair_limit: int | None,
) -> list[tuple[int, int]]:
    if pair_limit is None and per_page_pair_limit is None:
        return sorted(candidates)
    if pair_limit is not None and pair_limit <= 0:
        return []
    if per_page_pair_limit is not None and per_page_pair_limit <= 0:
        return []

    signatures = [_layout_signatures(region) for region in regions]

    def rank(pair: tuple[int, int]) -> tuple[int, float, float, int, int, int]:
        first_index, second_index = pair
        first = regions[first_index]
        second = regions[second_index]
        return (
            len(signatures[first_index] & signatures[second_index]),
            min(first.extraction_score, second.extraction_score),
            min(first.distinctiveness, second.distinctiveness),
            min(len(first.spots), len(second.spots)),
            -first_index,
            -second_index,
        )

    ordered = sorted(candidates, key=rank, reverse=True)
    selected: list[tuple[int, int]] = []
    selected_pairs: set[tuple[int, int]] = set()
    page_pair_counts: dict[tuple[str, str], int] = defaultdict(int)

    def select(pair: tuple[int, int]) -> bool:
        page_pair = _page_pair_key(regions[pair[0]], regions[pair[1]])
        if per_page_pair_limit is not None and page_pair_counts[page_pair] >= per_page_pair_limit:
            return False
        selected.append(pair)
        selected_pairs.add(pair)
        page_pair_counts[page_pair] += 1
        return True

    if pair_limit is not None:
        covered_page_pairs: set[tuple[str, str]] = set()
        for pair in ordered:
            page_pair = _page_pair_key(regions[pair[0]], regions[pair[1]])
            if page_pair in covered_page_pairs:
                continue
            covered_page_pairs.add(page_pair)
            select(pair)
            if len(selected) >= pair_limit:
                return sorted(selected)

    for pair in ordered:
        if pair in selected_pairs:
            continue
        if pair_limit is not None and len(selected) >= pair_limit:
            break
        select(pair)
    return sorted(selected)


def _layout_signatures(region: DotBlotRegion) -> set[tuple[int, ...]]:
    signatures: set[tuple[int, ...]] = set()
    for count in (3, 4):
        if len(region.spots) < count:
            continue
        for indexes in _near_contiguous_subsets(len(region.spots), count):
            interior = _subset_layout(region, indexes)[1:-1]
            for bins in (8, 12):
                for offset in (0.0, 0.5):
                    quantized = tuple(int(np.floor(value * bins + offset)) for value in interior)
                    signatures.add((count, bins, round(offset * 2), *quantized))
    return signatures


def _location(region: DotBlotRegion, indexes: tuple[int, ...]) -> EvidenceLocation:
    x, y, width, height = _matched_rectangle(region, indexes)
    parts = []
    if region.page_count > 1:
        parts.append(f"第 {region.page} 页")
    parts.append(f"区域 x={x}, y={y}, w={width}, h={height}")
    return EvidenceLocation(region.source_path, coordinate="；".join(parts))


def _matched_rectangle(
    region: DotBlotRegion,
    indexes: tuple[int, ...],
) -> tuple[int, int, int, int]:
    spots = tuple(region.spots[index] for index in indexes)
    left = min(spot.x for spot in spots)
    top = min(spot.y for spot in spots)
    right = max(spot.x + spot.width for spot in spots)
    bottom = max(spot.y + spot.height for spot in spots)
    pad_x = max(2, round((right - left) * 0.04))
    pad_y = max(2, round((bottom - top) * 0.18))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(region.page_width, right + pad_x)
    bottom = min(region.page_height, bottom + pad_y)
    return left, top, right - left, bottom - top


def _match_details(
    first: DotBlotRegion,
    second: DotBlotRegion,
    match: _DotBlotMatch,
) -> dict[str, object]:
    first_rectangle = _matched_rectangle(first, match.first_indexes)
    second_rectangle = _matched_rectangle(second, match.second_indexes)
    return {
        "evidence_kind": "dot_blot",
        "matched_spot_count": match.matched_spot_count,
        "layout_error": match.layout_error,
        "layout_similarity": match.layout_similarity,
        "profile_similarity": match.profile_similarity,
        "appearance_similarity": match.appearance_similarity,
        "minimum_spot_similarity": match.minimum_spot_similarity,
        "transformation": "partial_subset_scale_rotate_contrast",
        "scale_second_to_first": match.scale_second_to_first,
        "rotation_degrees_second_to_first": match.rotation_degrees_second_to_first,
        "mirrored": match.mirrored,
        "first_spot_count": len(first.spots),
        "second_spot_count": len(second.spots),
        "first_matched_spot_indexes": [index + 1 for index in match.first_indexes],
        "second_matched_spot_indexes": [index + 1 for index in match.second_indexes],
        "first_distinctiveness": first.distinctiveness,
        "second_distinctiveness": second.distinctiveness,
        "first_page": first.page,
        "second_page": second.page,
        "first_region_x": first_rectangle[0],
        "first_region_y": first_rectangle[1],
        "first_region_width": first_rectangle[2],
        "first_region_height": first_rectangle[3],
        "second_region_x": second_rectangle[0],
        "second_region_y": second_rectangle[1],
        "second_region_width": second_rectangle[2],
        "second_region_height": second_rectangle[3],
        "alignment": "按近连续斑点子集进行几何与局部形态联合验证，允许裁剪、缩放、旋转、"
        + "镜像和对比度变化",
    }


def _match_rank(match: _DotBlotMatch) -> tuple[int, float, float, float]:
    return (
        match.matched_spot_count,
        match.confidence,
        match.appearance_similarity,
        match.profile_similarity,
    )


def _page_match_rank(
    first: DotBlotRegion,
    second: DotBlotRegion,
    match: _DotBlotMatch,
) -> tuple[int, int, float, float]:
    return (
        match.matched_spot_count,
        max(len(first.spots), len(second.spots)),
        match.confidence,
        match.appearance_similarity,
    )


def _page_pair_key(first: DotBlotRegion, second: DotBlotRegion) -> tuple[str, str]:
    return tuple(
        sorted((f"{first.source_path}#{first.page}", f"{second.source_path}#{second.page}"))
    )


def _rectangle_iou(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    first_left, first_top, first_width, first_height = first
    second_left, second_top, second_width, second_height = second
    intersection_width = max(
        0,
        min(first_left + first_width, second_left + second_width) - max(first_left, second_left),
    )
    intersection_height = max(
        0,
        min(first_top + first_height, second_top + second_height) - max(first_top, second_top),
    )
    intersection = intersection_width * intersection_height
    union = first_width * first_height + second_width * second_height - intersection
    return intersection / max(union, 1)


def _rectangle_center(region: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, width, height = region
    return x + width / 2, y + height / 2


def _normalize_angle(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _angle_difference(first: float, second: float) -> float:
    difference = abs(_normalize_angle(first - second))
    return min(difference, abs(180.0 - difference))
