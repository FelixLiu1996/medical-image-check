from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable
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
    ScanIssue,
    deterministic_finding_id,
)
from medical_image_check.infrastructure.images import (
    canonical_pixels,
    decode_image_pages,
    hamming_distance,
    normalized_similarity,
    to_gray8,
)

WESTERN_MAX_DIMENSION = 1400
WESTERN_STRUCTURE_SIZE = (96, 32)
WESTERN_STRIP_STRUCTURE_SIZE = (256, 64)
WESTERN_STRIP_PROFILE_SIZE = 128
WESTERN_STRIP_MIN_ASPECT_RATIO = 2.0
WESTERN_STRIP_MAX_HEIGHT = 160
WESTERN_STRIP_CANDIDATE_MIN_VOTES = 2
WESTERN_MAX_BANDS_PER_PAGE = 256
WESTERN_MAX_MULTI_BAND_REGIONS = 24
WESTERN_MAX_SINGLE_BAND_REGIONS = 24
WESTERN_INDEX_BUCKET_LIMIT = 128
WESTERN_CANDIDATE_MIN_VOTES = 2
WESTERN_HASH_MAX_DISTANCE = 20
WESTERN_AUTO_MIN_REGION_ASPECT_RATIO = 1.20
WESTERN_AUTO_MAX_BAND_CENTER_SPREAD_FACTOR = 2.0
WESTERN_AUTO_MIN_BACKGROUND_SIMILARITY = 0.30
WESTERN_AUTO_MIN_STRUCTURE_WITHOUT_BACKGROUND = 0.97
WESTERN_TRANSFORMS = (
    "identity",
    "flip_horizontal",
    "flip_vertical",
    "rotate_180",
)


@dataclass(frozen=True, slots=True)
class WesternBand:
    x: int
    y: int
    width: int
    height: int
    strength: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class WesternFingerprint:
    transform: str
    value: int


@dataclass(frozen=True, slots=True)
class WesternRegion:
    source_path: str
    page: int
    page_count: int
    panel: int
    region: tuple[int, int, int, int]
    polarity: str
    bands: tuple[WesternBand, ...]
    structure: NDArray[np.float32]
    band_mask: NDArray[np.float32]
    background_texture: NDArray[np.float32]
    fingerprints: tuple[WesternFingerprint, ...]
    single_band: bool = False
    horizontal_profile: NDArray[np.float32] | None = None
    strip_fallback: bool = False
    profile_fingerprints: tuple[WesternFingerprint, ...] = ()


@dataclass(frozen=True, slots=True)
class _WesternMatch:
    transform: str
    structure_similarity: float
    background_similarity: float
    mask_iou: float
    geometry_similarity: float
    matched_band_count: int
    confidence: float
    profile_similarity: float = 0.0
    strip_scale_ratio: float = 1.0
    strip_fallback: bool = False


class WesternBlotDuplicateDetector:
    rule_id = "image.western_blot.panel_reuse"
    single_band_rule_id = "image.western_blot.single_band"

    def __init__(self, include_single_band: bool = False) -> None:
        self.include_single_band = include_single_band

    def scan(
        self,
        paths: Iterable[Path],
    ) -> tuple[list[Finding], list[ScanIssue]]:
        regions: list[WesternRegion] = []
        issues: list[ScanIssue] = []
        for path in paths:
            try:
                pages = decode_image_pages(path)
                regions.extend(self.extract_from_pages(path, pages))
            except (OSError, ValueError) as exc:
                issues.append(ScanIssue(str(path), f"无法处理 Western blot 候选：{exc}", "error"))
        return self.findings(regions), issues

    def extract_from_pages(
        self,
        path: str | Path,
        pages: tuple[NDArray, ...],
    ) -> tuple[WesternRegion, ...]:
        source = str(Path(path))
        extracted: list[WesternRegion] = []
        page_count = len(pages)
        for page_number, page in enumerate(pages, start=1):
            gray = to_gray8(canonical_pixels(page))
            extracted.extend(
                _extract_page_regions(
                    source,
                    page_number,
                    page_count,
                    gray,
                    self.include_single_band,
                )
            )
        return tuple(extracted)

    def findings(
        self,
        regions: list[WesternRegion],
        excluded_page_pairs: set[tuple[str, str]] | None = None,
        checkpoint: Callable[[], None] | None = None,
        *,
        strict_auto: bool = False,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for candidate_index, (first_index, second_index) in enumerate(
            sorted(_candidate_pairs(regions))
        ):
            if checkpoint and candidate_index % 64 == 0:
                checkpoint()
            first = regions[first_index]
            second = regions[second_index]
            if excluded_page_pairs and _page_pair_key(first, second) in excluded_page_pairs:
                continue
            if first.single_band != second.single_band:
                continue
            match = _best_match(first, second)
            if match is None:
                continue
            if strict_auto and not _auto_match_is_plausible(first, second, match):
                continue
            locations = (_location(first), _location(second))
            if first.strip_fallback:
                rule_id = self.single_band_rule_id
                risk = RiskLevel.LOW
                title = "Western blot 条带行高度相似"
                description = (
                    "窄条带行在缩放或翻转后具有高度相似的横向强度轮廓；"
                    "此类证据必须结合条带边缘和背景人工复核。"
                )
            elif first.single_band:
                rule_id = self.single_band_rule_id
                risk = RiskLevel.LOW
                title = "Western blot 单条带高度相似"
                description = (
                    "单条带的形状、局部纹理和背景证据高度相似；自然相似较常见，必须人工复核。"
                )
            else:
                rule_id = self.rule_id
                risk = _risk_level(first, second, match)
                title = "Western blot 面板或泳道疑似复用"
                description = (
                    "多个条带的形状、排列几何和背景纹理共同匹配，"
                    "可能存在裁剪、拉伸、翻转或曝光变化。"
                )
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(rule_id, locations),
                    rule_id=rule_id,
                    finding_type=FindingType.SUSPECTED_REUSE,
                    risk=risk,
                    title=title,
                    description=description,
                    locations=locations,
                    confidence=match.confidence,
                    details=_match_details(first, second, match),
                )
            )
        return findings


def _extract_page_regions(
    source_path: str,
    page: int,
    page_count: int,
    gray: NDArray[np.uint8],
    include_single_band: bool,
) -> list[WesternRegion]:
    height, width = gray.shape[:2]
    scale = min(1.0, WESTERN_MAX_DIMENSION / max(height, width, 1))
    if scale < 1.0:
        processing = cv2.resize(
            gray,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        processing = gray

    dark_response, light_response = _foreground_responses(processing)
    dark_bands, dark_mask = _detect_bands(dark_response)
    light_bands, light_mask = _detect_bands(light_response)
    dark_score = _band_set_score(dark_bands)
    light_score = _band_set_score(light_bands)
    strip_region = (
        _strip_fallback_region(source_path, page, page_count, processing, scale)
        if include_single_band
        else None
    )
    if dark_score <= 0 and light_score <= 0:
        return [strip_region] if strip_region is not None else []
    if light_score > dark_score:
        response, bands, mask, polarity = light_response, light_bands, light_mask, "light"
    else:
        response, bands, mask, polarity = dark_response, dark_bands, dark_mask, "dark"
    if len(bands) > WESTERN_MAX_BANDS_PER_PAGE:
        return [strip_region] if strip_region is not None else []

    row_groups = _group_band_rows(bands, processing.shape[0], processing.shape[1])
    region_groups = [group for group in row_groups if len(group) >= 2]
    region_groups = sorted(
        region_groups,
        key=lambda group: (-len(group), -sum(band.strength for band in group)),
    )[:WESTERN_MAX_MULTI_BAND_REGIONS]
    region_groups.sort(key=lambda group: (min(band.center_y for band in group), group[0].center_x))
    if include_single_band:
        strongest_bands = sorted(bands, key=lambda band: -band.strength)[
            :WESTERN_MAX_SINGLE_BAND_REGIONS
        ]
        strongest_bands.sort(key=lambda band: (band.center_y, band.center_x))
        region_groups.extend((band,) for band in strongest_bands)

    regions: list[WesternRegion] = []
    seen_boxes: set[tuple[int, int, int, int, bool]] = set()
    for panel_index, group in enumerate(region_groups, start=1):
        single_band = len(group) == 1
        box = _padded_region(group, processing.shape[0], processing.shape[1])
        key = (*box, single_band)
        if key in seen_boxes:
            continue
        seen_boxes.add(key)
        x, y, region_width, region_height = box
        if region_width < 8 or region_height < 6:
            continue
        structure = _standardize(
            cv2.resize(
                response[y : y + region_height, x : x + region_width],
                WESTERN_STRUCTURE_SIZE,
                interpolation=cv2.INTER_AREA,
            )
        )
        resized_mask = cv2.resize(
            mask[y : y + region_height, x : x + region_width],
            WESTERN_STRUCTURE_SIZE,
            interpolation=cv2.INTER_NEAREST,
        ).astype(np.float32)
        background = _background_texture(
            processing[y : y + region_height, x : x + region_width],
            mask[y : y + region_height, x : x + region_width],
        )
        fingerprints = tuple(
            WesternFingerprint(transform, _perceptual_hash(_apply_transform(structure, transform)))
            for transform in WESTERN_TRANSFORMS
        )
        regions.append(
            WesternRegion(
                source_path=source_path,
                page=page,
                page_count=page_count,
                panel=panel_index,
                region=_restore_box(box, scale),
                polarity=polarity,
                bands=tuple(_restore_band(band, scale) for band in group),
                structure=structure,
                band_mask=resized_mask,
                background_texture=background,
                fingerprints=fingerprints,
                single_band=single_band,
            )
        )
    if strip_region is not None:
        regions.append(strip_region)
    return regions


def _strip_fallback_region(
    source_path: str,
    page: int,
    page_count: int,
    gray: NDArray[np.uint8],
    scale: float,
) -> WesternRegion | None:
    height, width = gray.shape[:2]
    if (
        height < 10
        or width < 48
        or height > WESTERN_STRIP_MAX_HEIGHT
        or width / max(height, 1) < WESTERN_STRIP_MIN_ASPECT_RATIO
    ):
        return None

    dark_response, light_response = _foreground_responses(gray)
    dark_strength = float(np.percentile(dark_response, 95))
    light_strength = float(np.percentile(light_response, 95))
    if light_strength > dark_strength:
        response, polarity = light_response, "light"
    else:
        response, polarity = dark_response, "dark"
    positive = response[response > 0]
    threshold = float(np.percentile(positive, 72)) if positive.size else float("inf")
    mask = np.asarray(response >= max(3.0, threshold), dtype=np.uint8)
    structure = _standardize(
        cv2.resize(
            cv2.GaussianBlur(gray, (0, 0), sigmaX=0.8, sigmaY=0.8),
            WESTERN_STRIP_STRUCTURE_SIZE,
            interpolation=cv2.INTER_AREA,
        )
    )
    profile = np.mean(gray.astype(np.float32), axis=0).reshape(1, -1)
    profile = cv2.resize(
        profile,
        (WESTERN_STRIP_PROFILE_SIZE, 1),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)
    profile = _standardize(profile)
    resized_mask = cv2.resize(
        mask,
        WESTERN_STRIP_STRUCTURE_SIZE,
        interpolation=cv2.INTER_NEAREST,
    ).astype(np.float32)
    fingerprints = tuple(
        WesternFingerprint(transform, _perceptual_hash(_apply_transform(structure, transform)))
        for transform in WESTERN_TRANSFORMS
    )
    profile_fingerprints = (
        WesternFingerprint("identity", _profile_hash(profile)),
        WesternFingerprint("flip_horizontal", _profile_hash(profile[::-1])),
    )
    return WesternRegion(
        source_path=source_path,
        page=page,
        page_count=page_count,
        panel=WESTERN_MAX_MULTI_BAND_REGIONS + WESTERN_MAX_SINGLE_BAND_REGIONS + 1,
        region=_restore_box((0, 0, width, height), scale),
        polarity=polarity,
        bands=(),
        structure=structure,
        band_mask=resized_mask,
        background_texture=np.zeros(WESTERN_STRUCTURE_SIZE[::-1], dtype=np.float32),
        fingerprints=fingerprints,
        single_band=True,
        horizontal_profile=profile,
        strip_fallback=True,
        profile_fingerprints=profile_fingerprints,
    )


def _foreground_responses(
    gray: NDArray[np.uint8],
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    values = gray.astype(np.float32)
    sigma = max(3.0, min(gray.shape[:2]) / 28.0)
    background = cv2.GaussianBlur(values, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return np.maximum(background - values, 0), np.maximum(values - background, 0)


def _detect_bands(
    response: NDArray[np.float32],
) -> tuple[list[WesternBand], NDArray[np.uint8]]:
    height, width = response.shape[:2]
    positive = response[response > 0]
    if positive.size < 8:
        return [], np.zeros_like(response, dtype=np.uint8)
    median = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median)))
    percentile = float(np.percentile(positive, 90))
    threshold = max(4.0, median + 3.0 * mad, percentile * 0.48)
    binary = np.asarray(response >= threshold, dtype=np.uint8)
    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)),
    )
    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, width // 300), 2)),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    bands: list[WesternBand] = []
    minimum_width = max(5, round(width * 0.006))
    maximum_height = max(6, round(height * 0.14))
    minimum_area = max(8, round(height * width * 0.000015))
    filtered_mask = np.zeros_like(binary)
    for label in range(1, count):
        x, y, band_width, band_height, area = (int(value) for value in stats[label])
        if (
            band_width < minimum_width
            or band_height < 2
            or band_height > maximum_height
            or area < minimum_area
            or band_width / max(band_height, 1) < 1.25
            or band_width > width * 0.5
        ):
            continue
        component = labels[y : y + band_height, x : x + band_width] == label
        fill_ratio = float(np.count_nonzero(component)) / max(band_width * band_height, 1)
        if fill_ratio < 0.16:
            continue
        local_response = response[y : y + band_height, x : x + band_width]
        strength = float(np.mean(local_response[component]))
        bands.append(WesternBand(x, y, band_width, band_height, strength))
        filtered_mask[labels == label] = 1
    return bands, filtered_mask


def _band_set_score(bands: list[WesternBand]) -> float:
    if not bands:
        return 0.0
    horizontal_quality = sum(min(6.0, band.width / max(band.height, 1)) for band in bands)
    return len(bands) * 2.0 + horizontal_quality


def _group_band_rows(
    bands: list[WesternBand],
    image_height: int,
    image_width: int,
) -> list[tuple[WesternBand, ...]]:
    if not bands:
        return []
    median_height = float(np.median([band.height for band in bands]))
    tolerance = max(4.0, median_height * 1.8)
    rows: list[list[WesternBand]] = []
    for band in sorted(bands, key=lambda item: (item.center_y, item.center_x)):
        target: list[WesternBand] | None = None
        best_distance = float("inf")
        for row in rows:
            center = float(np.mean([item.center_y for item in row]))
            distance = abs(center - band.center_y)
            if distance <= tolerance and distance < best_distance:
                target = row
                best_distance = distance
        if target is None:
            rows.append([band])
        else:
            target.append(band)

    groups: list[tuple[WesternBand, ...]] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: item.center_x)
        median_width = float(np.median([band.width for band in ordered]))
        maximum_gap = max(median_width * 4.0, image_width * 0.18)
        chunk: list[WesternBand] = []
        previous_right: float | None = None
        for band in ordered:
            if previous_right is not None and band.x - previous_right > maximum_gap:
                if chunk:
                    groups.append(tuple(chunk))
                chunk = []
            chunk.append(band)
            previous_right = float(band.x + band.width)
        if chunk:
            groups.append(tuple(chunk))
    return groups


def _padded_region(
    bands: tuple[WesternBand, ...],
    image_height: int,
    image_width: int,
) -> tuple[int, int, int, int]:
    median_width = float(np.median([band.width for band in bands]))
    median_height = float(np.median([band.height for band in bands]))
    left = max(0, math.floor(min(band.x for band in bands) - median_width * 0.65))
    right = min(
        image_width,
        math.ceil(max(band.x + band.width for band in bands) + median_width * 0.65),
    )
    top = max(0, math.floor(min(band.y for band in bands) - median_height * 2.5))
    bottom = min(
        image_height,
        math.ceil(max(band.y + band.height for band in bands) + median_height * 2.5),
    )
    return left, top, right - left, bottom - top


def _background_texture(
    gray: NDArray[np.uint8],
    band_mask: NDArray[np.uint8],
) -> NDArray[np.float32]:
    values = gray.astype(np.float32)
    smooth = cv2.GaussianBlur(values, (0, 0), sigmaX=2.0, sigmaY=2.0)
    texture = values - smooth
    masked = cv2.dilate(
        band_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )
    texture[masked > 0] = 0
    resized = cv2.resize(texture, WESTERN_STRUCTURE_SIZE, interpolation=cv2.INTER_AREA)
    return _standardize(resized)


def _standardize(values: NDArray) -> NDArray[np.float32]:
    result = values.astype(np.float32)
    mean = float(np.mean(result))
    deviation = float(np.std(result))
    if deviation <= 1e-6:
        return np.zeros_like(result, dtype=np.float32)
    return np.ascontiguousarray((result - mean) / deviation, dtype=np.float32)


def _perceptual_hash(values: NDArray[np.float32]) -> int:
    resized = cv2.resize(values, (32, 32), interpolation=cv2.INTER_AREA)
    coefficients = cv2.dct(resized.astype(np.float32))[:8, :8]
    threshold = float(np.median(coefficients.reshape(-1)[1:]))
    bits = coefficients > threshold
    return int.from_bytes(np.packbits(bits.reshape(-1)).tobytes(), "big")


def _profile_hash(profile: NDArray[np.float32]) -> int:
    resized = cv2.resize(
        profile.reshape(1, -1),
        (64, 1),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)
    bits = resized > float(np.median(resized))
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def _apply_transform(values: NDArray, transform: str) -> NDArray:
    if transform == "identity":
        return values
    if transform == "flip_horizontal":
        return np.fliplr(values)
    if transform == "flip_vertical":
        return np.flipud(values)
    if transform == "rotate_180":
        return np.rot90(values, 2)
    raise ValueError(f"未知 Western blot 变换：{transform}")


def _candidate_pairs(regions: list[WesternRegion]) -> set[tuple[int, int]]:
    index: dict[tuple[bool, int, int], list[int]] = defaultdict(list)
    votes: dict[tuple[int, int], int] = defaultdict(int)
    for region_index, region in enumerate(regions):
        if region.strip_fallback:
            continue
        seen_keys: set[tuple[bool, int, int]] = set()
        for fingerprint in region.fingerprints:
            for band in range(8):
                key = (region.single_band, band, (fingerprint.value >> (band * 8)) & 0xFF)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                bucket = index[key]
                if len(bucket) <= WESTERN_INDEX_BUCKET_LIMIT:
                    for previous in bucket:
                        if not _same_or_overlapping_region(regions[previous], region):
                            votes[(previous, region_index)] += 1
                    bucket.append(region_index)
    candidates = {pair for pair, count in votes.items() if count >= WESTERN_CANDIDATE_MIN_VOTES}
    profile_index: dict[tuple[int, int], list[int]] = defaultdict(list)
    profile_votes: dict[tuple[int, int], int] = defaultdict(int)
    for region_index, region in enumerate(regions):
        if not region.strip_fallback:
            continue
        seen_keys: set[tuple[int, int]] = set()
        for fingerprint in region.profile_fingerprints:
            for band in range(8):
                key = (band, (fingerprint.value >> (band * 8)) & 0xFF)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                bucket = profile_index[key]
                if len(bucket) <= WESTERN_INDEX_BUCKET_LIMIT:
                    for previous in bucket:
                        if not _same_or_overlapping_region(regions[previous], region):
                            profile_votes[(previous, region_index)] += 1
                    bucket.append(region_index)
    candidates.update(
        pair for pair, count in profile_votes.items() if count >= WESTERN_STRIP_CANDIDATE_MIN_VOTES
    )
    return candidates


def _same_or_overlapping_region(first: WesternRegion, second: WesternRegion) -> bool:
    if first.source_path != second.source_path or first.page != second.page:
        return False
    if first.panel == second.panel:
        return True
    return _intersection_over_union(first.region, second.region) > 0.2


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


def _best_match(first: WesternRegion, second: WesternRegion) -> _WesternMatch | None:
    if first.strip_fallback or second.strip_fallback:
        if not (first.strip_fallback and second.strip_fallback):
            return None
        return _best_strip_match(first, second)
    if abs(len(first.bands) - len(second.bands)) > max(
        1, min(len(first.bands), len(second.bands)) // 2
    ):
        return None
    first_hash = first.fingerprints[0].value
    best: _WesternMatch | None = None
    for fingerprint in second.fingerprints:
        if hamming_distance(first_hash, fingerprint.value) > WESTERN_HASH_MAX_DISTANCE:
            continue
        transformed_structure = _apply_transform(second.structure, fingerprint.transform)
        structure_similarity = normalized_similarity(first.structure, transformed_structure)
        transformed_mask = _apply_transform(second.band_mask, fingerprint.transform)
        mask_iou = _mask_iou(first.band_mask, transformed_mask)
        transformed_background = _apply_transform(second.background_texture, fingerprint.transform)
        background_similarity = abs(
            normalized_similarity(first.background_texture, transformed_background)
        )
        geometry_similarity, matched_band_count = _geometry_similarity(
            first,
            second,
            fingerprint.transform,
        )
        if first.single_band:
            accepted = (
                structure_similarity >= 0.90
                and mask_iou >= 0.38
                and background_similarity >= 0.55
                and geometry_similarity >= 0.72
            )
        else:
            accepted = (
                matched_band_count >= 2
                and geometry_similarity >= 0.58
                and mask_iou >= 0.35
                and structure_similarity >= 0.86
                and (structure_similarity >= 0.93 or background_similarity >= 0.70)
            )
        if not accepted:
            continue
        confidence = max(
            0.0,
            min(
                1.0,
                0.48 * structure_similarity
                + 0.24 * background_similarity
                + 0.16 * geometry_similarity
                + 0.12 * mask_iou,
            ),
        )
        candidate = _WesternMatch(
            transform=fingerprint.transform,
            structure_similarity=structure_similarity,
            background_similarity=background_similarity,
            mask_iou=mask_iou,
            geometry_similarity=geometry_similarity,
            matched_band_count=matched_band_count,
            confidence=confidence,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def _auto_match_is_plausible(
    first: WesternRegion,
    second: WesternRegion,
    match: _WesternMatch,
) -> bool:
    for region in (first, second):
        _, _, width, height = region.region
        if not region.single_band and width / max(height, 1) < WESTERN_AUTO_MIN_REGION_ASPECT_RATIO:
            return False
        if region.bands:
            band_heights = [band.height for band in region.bands]
            center_spread = max(band.center_y for band in region.bands) - min(
                band.center_y for band in region.bands
            )
            if center_spread > max(
                4.0,
                float(np.median(band_heights)) * WESTERN_AUTO_MAX_BAND_CENTER_SPREAD_FACTOR,
            ):
                return False
    return (
        match.background_similarity >= WESTERN_AUTO_MIN_BACKGROUND_SIMILARITY
        or match.structure_similarity >= WESTERN_AUTO_MIN_STRUCTURE_WITHOUT_BACKGROUND
    )


def _best_strip_match(first: WesternRegion, second: WesternRegion) -> _WesternMatch | None:
    if first.horizontal_profile is None or second.horizontal_profile is None:
        return None
    first_width = first.region[2]
    second_width = second.region[2]
    scale_ratio = max(
        first_width / max(second_width, 1),
        second_width / max(first_width, 1),
    )
    best: _WesternMatch | None = None
    for transform in WESTERN_TRANSFORMS:
        transformed_structure = _apply_transform(second.structure, transform)
        structure_similarity = abs(normalized_similarity(first.structure, transformed_structure))
        transformed_profile = (
            second.horizontal_profile[::-1]
            if transform in {"flip_horizontal", "rotate_180"}
            else second.horizontal_profile
        )
        profile_similarity = abs(
            normalized_similarity(first.horizontal_profile, transformed_profile)
        )
        horizontally_flipped = transform in {"flip_horizontal", "rotate_180"}
        accepted = (
            horizontally_flipped and structure_similarity >= 0.78 and profile_similarity >= 0.90
        ) or (
            not horizontally_flipped
            and scale_ratio >= 1.30
            and structure_similarity >= 0.88
            and profile_similarity >= 0.97
        )
        if not accepted:
            continue
        confidence = min(1.0, 0.58 * structure_similarity + 0.42 * profile_similarity)
        candidate = _WesternMatch(
            transform=transform,
            structure_similarity=structure_similarity,
            background_similarity=0.0,
            mask_iou=0.0,
            geometry_similarity=profile_similarity,
            matched_band_count=0,
            confidence=confidence,
            profile_similarity=profile_similarity,
            strip_scale_ratio=scale_ratio,
            strip_fallback=True,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def _mask_iou(first: NDArray[np.float32], second: NDArray[np.float32]) -> float:
    first_mask = first >= 0.5
    second_mask = second >= 0.5
    intersection = int(np.count_nonzero(first_mask & second_mask))
    union = int(np.count_nonzero(first_mask | second_mask))
    return intersection / max(union, 1)


def _geometry_similarity(
    first: WesternRegion,
    second: WesternRegion,
    transform: str,
) -> tuple[float, int]:
    first_geometry = _normalized_band_geometry(first)
    second_geometry = _normalized_band_geometry(second)
    second_geometry = _transform_geometry(second_geometry, transform)
    distances: list[float] = []
    matched = 0
    remaining = list(range(len(second_geometry)))
    for first_band in first_geometry:
        if not remaining:
            break
        choices = [
            (
                math.hypot(
                    first_band[0] - second_geometry[index][0],
                    first_band[1] - second_geometry[index][1],
                )
                + 0.35
                * abs(math.log(max(first_band[2], 1e-6) / max(second_geometry[index][2], 1e-6)))
                + 0.35
                * abs(math.log(max(first_band[3], 1e-6) / max(second_geometry[index][3], 1e-6))),
                index,
            )
            for index in remaining
        ]
        distance, selected = min(choices)
        remaining.remove(selected)
        distances.append(distance)
        if distance <= 0.30:
            matched += 1
    if not distances:
        return 0.0, 0
    mean_distance = float(
        np.mean(sorted(distances)[: max(1, min(len(first_geometry), len(second_geometry)))])
    )
    return math.exp(-3.2 * mean_distance), matched


def _normalized_band_geometry(region: WesternRegion) -> list[tuple[float, float, float, float]]:
    region_x, region_y, region_width, region_height = region.region
    return [
        (
            (band.center_x - region_x) / max(region_width, 1),
            (band.center_y - region_y) / max(region_height, 1),
            band.width / max(region_width, 1),
            band.height / max(region_height, 1),
        )
        for band in region.bands
    ]


def _transform_geometry(
    geometry: list[tuple[float, float, float, float]],
    transform: str,
) -> list[tuple[float, float, float, float]]:
    transformed: list[tuple[float, float, float, float]] = []
    for x, y, width, height in geometry:
        if transform in {"flip_horizontal", "rotate_180"}:
            x = 1.0 - x
        if transform in {"flip_vertical", "rotate_180"}:
            y = 1.0 - y
        transformed.append((x, y, width, height))
    return transformed


def _risk_level(
    first: WesternRegion,
    second: WesternRegion,
    match: _WesternMatch,
) -> RiskLevel:
    same_page = first.source_path == second.source_path and first.page == second.page
    if (
        not same_page
        and match.matched_band_count >= 3
        and match.structure_similarity >= 0.94
        and match.background_similarity >= 0.82
        and match.geometry_similarity >= 0.82
    ):
        return RiskLevel.HIGH
    if (
        match.structure_similarity >= 0.88
        and match.background_similarity >= 0.55
        and match.geometry_similarity >= 0.70
    ) or (
        match.matched_band_count >= 3
        and match.structure_similarity >= 0.92
        and match.geometry_similarity >= 0.85
    ):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _match_details(
    first: WesternRegion,
    second: WesternRegion,
    match: _WesternMatch,
) -> dict[str, str | int | float | bool | list[dict[str, int | float]]]:
    first_x, first_y, first_width, first_height = first.region
    second_x, second_y, second_width, second_height = second.region
    return {
        "evidence_kind": "western_blot",
        "transform_second_to_first": match.transform,
        "first_panel": first.panel,
        "second_panel": second.panel,
        "first_polarity": first.polarity,
        "second_polarity": second.polarity,
        "first_region_x": first_x,
        "first_region_y": first_y,
        "first_region_width": first_width,
        "first_region_height": first_height,
        "second_region_x": second_x,
        "second_region_y": second_y,
        "second_region_width": second_width,
        "second_region_height": second_height,
        "first_band_count": len(first.bands),
        "second_band_count": len(second.bands),
        "matched_band_count": match.matched_band_count,
        "structure_similarity": round(match.structure_similarity, 6),
        "background_similarity": round(match.background_similarity, 6),
        "band_mask_iou": round(match.mask_iou, 6),
        "geometry_similarity": round(match.geometry_similarity, 6),
        "single_band_mode": first.single_band,
        "strip_fallback": match.strip_fallback,
        "horizontal_profile_similarity": round(match.profile_similarity, 6),
        "strip_scale_ratio": round(match.strip_scale_ratio, 6),
        "first_bands": [_band_details(band) for band in first.bands],
        "second_bands": [_band_details(band) for band in second.bands],
    }


def _band_details(band: WesternBand) -> dict[str, int | float]:
    return {
        "x": band.x,
        "y": band.y,
        "width": band.width,
        "height": band.height,
        "strength": round(band.strength, 4),
    }


def _location(region: WesternRegion) -> EvidenceLocation:
    parts: list[str] = []
    if region.page_count > 1:
        parts.append(f"第 {region.page} 页")
    parts.append(f"Western 面板 {region.panel}")
    if region.strip_fallback:
        parts.append("窄条带行")
    elif region.single_band:
        parts.append("单条带")
    return EvidenceLocation(region.source_path, coordinate="；".join(parts))


def _page_pair_key(first: WesternRegion, second: WesternRegion) -> tuple[str, str]:
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


def _restore_band(band: WesternBand, scale: float) -> WesternBand:
    if scale >= 1.0:
        return band
    return WesternBand(
        x=round(band.x / scale),
        y=round(band.y / scale),
        width=max(1, round(band.width / scale)),
        height=max(1, round(band.height / scale)),
        strength=band.strength,
    )
