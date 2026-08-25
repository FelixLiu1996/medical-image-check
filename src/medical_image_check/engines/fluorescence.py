from __future__ import annotations

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

FLUORESCENCE_SIZE = (96, 96)
FLUORESCENCE_MAX_DIMENSION = 1400
FLUORESCENCE_INDEX_BUCKET_LIMIT = 96
FLUORESCENCE_CANDIDATE_MIN_VOTES = 2
FLUORESCENCE_HASH_MAX_DISTANCE = 18
FLUORESCENCE_TRANSFORMS = (
    "identity",
    "flip_horizontal",
    "flip_vertical",
    "rotate_180",
)

_CHANNEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("blue", re.compile(r"(?:^|[^a-z0-9])(dapi|hoechst|blue|405)(?:[^a-z0-9]|$)", re.I)),
    ("green", re.compile(r"(?:^|[^a-z0-9])(fitc|gfp|green|488)(?:[^a-z0-9]|$)", re.I)),
    (
        "red",
        re.compile(
            r"(?:^|[^a-z0-9])(rfp|tritc|cy3|red|555|568|594)(?:[^a-z0-9]|$)",
            re.I,
        ),
    ),
    (
        "far_red",
        re.compile(r"(?:^|[^a-z0-9])(cy5|far[ _-]?red|647)(?:[^a-z0-9]|$)", re.I),
    ),
)
_MERGE_PATTERN = re.compile(r"(?:merge|merged|composite|overlay|合并|叠加)", re.I)
_FIELD_TOKEN_PATTERN = re.compile(
    r"(?:dapi|hoechst|blue|405|fitc|gfp|green|488|rfp|tritc|cy3|red|555|568|594|"
    r"cy5|far[ _-]?red|647|merge|merged|composite|overlay|合并|叠加)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class FluorescenceChannel:
    name: str
    structure: NDArray[np.float32]
    foreground_mask: NDArray[np.uint8]
    fingerprints: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class FluorescencePage:
    source_path: str
    page: int
    page_count: int
    width: int
    height: int
    inferred_role: str
    field_key: str
    channels: tuple[FluorescenceChannel, ...]


@dataclass(frozen=True, slots=True)
class _FluorescenceMatch:
    transform: str
    first_channel: str
    second_channel: str
    structure_similarity: float
    mask_iou: float
    normalized_mutual_information: float
    shift_x: float
    shift_y: float
    confidence: float


class FluorescenceDuplicateDetector:
    merge_rule_id = "image.fluorescence.merge_component"
    channel_rule_id = "image.fluorescence.same_field_channels"
    reuse_rule_id = "image.fluorescence.same_channel_reuse"

    def extract_from_pages(
        self,
        path: str | Path,
        pages: tuple[NDArray, ...],
        force: bool = False,
    ) -> tuple[FluorescencePage, ...]:
        source = str(Path(path))
        page_count = len(pages)
        role = _infer_role(Path(path).stem)
        field_key = _field_key(Path(path))
        extracted: list[FluorescencePage] = []
        for page_number, page in enumerate(pages, start=1):
            candidate = _extract_page(source, page_number, page_count, page, role, field_key, force)
            if candidate is not None:
                extracted.append(candidate)
        return tuple(extracted)

    def findings(
        self,
        pages: list[FluorescencePage],
        excluded_page_pairs: set[tuple[str, str]] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for candidate_index, (first_index, second_index) in enumerate(
            sorted(_candidate_pairs(pages))
        ):
            if checkpoint and candidate_index % 64 == 0:
                checkpoint()
            first = pages[first_index]
            second = pages[second_index]
            if excluded_page_pairs and _page_pair_key(first, second) in excluded_page_pairs:
                continue
            match = _best_match(first, second)
            if match is None:
                continue
            classification = _classify(first, second, match)
            if classification is None:
                continue
            rule_id, finding_type, risk, title, description, relation = classification
            locations = (_location(first), _location(second))
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
                    details=_match_details(first, second, match, relation),
                )
            )
        return findings


def _extract_page(
    source_path: str,
    page: int,
    page_count: int,
    image: NDArray,
    inferred_role: str,
    field_key: str,
    force: bool = False,
) -> FluorescencePage | None:
    canonical = canonical_pixels(image)
    bgr = canonical[:, :, :3]
    if bgr.dtype != np.uint8:
        bgr = cv2.normalize(bgr, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    height, width = bgr.shape[:2]
    scale = min(1.0, FLUORESCENCE_MAX_DIMENSION / max(height, width, 1))
    if scale < 1.0:
        bgr = cv2.resize(
            bgr,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    colorful = _is_color_fluorescence(bgr)
    named = inferred_role != "unknown"
    if not force and not named and not colorful:
        return None

    planes = {"blue": bgr[:, :, 0], "green": bgr[:, :, 1], "red": bgr[:, :, 2]}
    channels: list[FluorescenceChannel] = []
    selected: list[tuple[str, NDArray[np.uint8]]] = []
    if np.mean(np.max(bgr, axis=2) - np.min(bgr, axis=2)) < 3.0:
        channel_name = inferred_role if inferred_role not in {"unknown", "merge"} else "gray"
        selected.append((channel_name, bgr[:, :, 1]))
    else:
        for name, plane in planes.items():
            if _signal_quality(plane):
                selected.append((name, plane))
    if not selected and named:
        strongest_name, strongest = max(planes.items(), key=lambda item: float(np.std(item[1])))
        selected.append((inferred_role if inferred_role != "merge" else strongest_name, strongest))

    for name, plane in selected:
        structure, mask = _channel_structure(plane)
        if float(np.std(structure)) < 0.05:
            continue
        fingerprints = tuple(
            (transform, _perceptual_hash(_apply_transform(structure, transform)))
            for transform in FLUORESCENCE_TRANSFORMS
        )
        channels.append(FluorescenceChannel(name, structure, mask, fingerprints))
    if not channels:
        return None

    return FluorescencePage(
        source_path=source_path,
        page=page,
        page_count=page_count,
        width=width,
        height=height,
        inferred_role=inferred_role,
        field_key=field_key,
        channels=tuple(channels),
    )


def _infer_role(stem: str) -> str:
    if _MERGE_PATTERN.search(stem):
        return "merge"
    for channel, pattern in _CHANNEL_PATTERNS:
        if pattern.search(stem):
            return channel
    return "unknown"


def _field_key(path: Path) -> str:
    stem = _FIELD_TOKEN_PATTERN.sub(" ", path.stem.lower())
    stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", stem).strip()
    return f"{path.parent.resolve()}::{stem}" if stem else ""


def _is_color_fluorescence(bgr: NDArray[np.uint8]) -> bool:
    maximum = np.max(bgr, axis=2)
    minimum = np.min(bgr, axis=2)
    dark_fraction = float(np.mean(maximum <= 55))
    bright_fraction = float(np.mean(maximum >= 110))
    colorful_fraction = float(np.mean((maximum - minimum) >= 24))
    return dark_fraction >= 0.35 and 0.001 <= bright_fraction <= 0.55 and colorful_fraction >= 0.001


def _signal_quality(plane: NDArray[np.uint8]) -> bool:
    low, high = np.percentile(plane, (20, 99.5))
    foreground_fraction = float(np.mean(plane >= max(float(low) + 12.0, float(high) * 0.35)))
    return float(high - low) >= 18.0 and 0.0005 <= foreground_fraction <= 0.65


def _channel_structure(
    plane: NDArray[np.uint8],
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    values = plane.astype(np.float32)
    low, high = np.percentile(values, (10, 99.5))
    normalized = np.clip((values - float(low)) / max(float(high - low), 1.0), 0.0, 1.0)
    normalized = cv2.GaussianBlur(normalized, (0, 0), sigmaX=0.8)
    resized = cv2.resize(normalized, FLUORESCENCE_SIZE, interpolation=cv2.INTER_AREA)
    positive = resized[resized > 0.02]
    threshold = max(0.12, float(np.percentile(positive, 68)) if positive.size else 1.0)
    mask = np.asarray(resized >= threshold, dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    return np.ascontiguousarray(resized, dtype=np.float32), mask


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
    raise ValueError(f"未知荧光图变换：{transform}")


def _candidate_pairs(pages: list[FluorescencePage]) -> set[tuple[int, int]]:
    index: dict[tuple[int, int], list[int]] = defaultdict(list)
    votes: dict[tuple[int, int], int] = defaultdict(int)
    field_groups: dict[str, list[int]] = defaultdict(list)
    for page_index, page in enumerate(pages):
        if page.field_key:
            for previous in field_groups[page.field_key]:
                if not _same_page(pages[previous], page):
                    votes[(previous, page_index)] += FLUORESCENCE_CANDIDATE_MIN_VOTES
            field_groups[page.field_key].append(page_index)
        seen_keys: set[tuple[int, int]] = set()
        for channel in page.channels:
            for _, fingerprint in channel.fingerprints:
                for band in range(8):
                    key = (band, (fingerprint >> (band * 8)) & 0xFF)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    bucket = index[key]
                    if len(bucket) <= FLUORESCENCE_INDEX_BUCKET_LIMIT:
                        for previous in bucket:
                            if not _same_page(pages[previous], page):
                                votes[(previous, page_index)] += 1
                        bucket.append(page_index)
    return {pair for pair, count in votes.items() if count >= FLUORESCENCE_CANDIDATE_MIN_VOTES}


def _same_page(first: FluorescencePage, second: FluorescencePage) -> bool:
    return first.source_path == second.source_path and first.page == second.page


def _best_match(
    first: FluorescencePage,
    second: FluorescencePage,
) -> _FluorescenceMatch | None:
    best: _FluorescenceMatch | None = None
    for first_channel in first.channels:
        first_hash = first_channel.fingerprints[0][1]
        for second_channel in second.channels:
            for transform, fingerprint in second_channel.fingerprints:
                name_related = _channel_relation_expected(first, second)
                if (
                    hamming_distance(first_hash, fingerprint) > FLUORESCENCE_HASH_MAX_DISTANCE
                    and not name_related
                ):
                    continue
                transformed = _apply_transform(second_channel.structure, transform)
                transformed_mask = _apply_transform(second_channel.foreground_mask, transform)
                aligned, aligned_mask, shift_x, shift_y = _align(
                    first_channel.structure,
                    transformed,
                    transformed_mask,
                )
                similarity = _normalized_correlation(first_channel.structure, aligned)
                mask_iou = _mask_iou(first_channel.foreground_mask, aligned_mask)
                mutual_information = _normalized_mutual_information(
                    first_channel.structure,
                    aligned,
                )
                confidence = max(
                    0.0,
                    min(1.0, 0.58 * similarity + 0.24 * mask_iou + 0.18 * mutual_information),
                )
                candidate = _FluorescenceMatch(
                    transform,
                    first_channel.name,
                    second_channel.name,
                    similarity,
                    mask_iou,
                    mutual_information,
                    shift_x,
                    shift_y,
                    confidence,
                )
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
    return best


def _align(
    first: NDArray[np.float32],
    second: NDArray[np.float32],
    second_mask: NDArray[np.uint8],
) -> tuple[NDArray[np.float32], NDArray[np.uint8], float, float]:
    try:
        (shift_x, shift_y), response = cv2.phaseCorrelate(first, second)
    except cv2.error:
        shift_x, shift_y, response = 0.0, 0.0, 0.0
    if response < 0.05 or abs(shift_x) > 12 or abs(shift_y) > 12:
        shift_x, shift_y = 0.0, 0.0
    matrix = np.asarray([[1.0, 0.0, -shift_x], [0.0, 1.0, -shift_y]], dtype=np.float32)
    size = (first.shape[1], first.shape[0])
    aligned = cv2.warpAffine(second, matrix, size, flags=cv2.INTER_LINEAR, borderValue=0)
    aligned_mask = cv2.warpAffine(
        second_mask,
        matrix,
        size,
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )
    return aligned, aligned_mask, float(-shift_x), float(-shift_y)


def _normalized_correlation(first: NDArray, second: NDArray) -> float:
    first_values = first.astype(np.float32) - float(np.mean(first))
    second_values = second.astype(np.float32) - float(np.mean(second))
    denominator = float(np.linalg.norm(first_values) * np.linalg.norm(second_values))
    if denominator <= 1e-8:
        return 0.0
    return max(0.0, min(1.0, float(np.sum(first_values * second_values)) / denominator))


def _mask_iou(first: NDArray[np.uint8], second: NDArray[np.uint8]) -> float:
    first_mask = first > 0
    second_mask = second > 0
    intersection = int(np.count_nonzero(first_mask & second_mask))
    union = int(np.count_nonzero(first_mask | second_mask))
    return intersection / max(union, 1)


def _normalized_mutual_information(first: NDArray, second: NDArray) -> float:
    first_bins = np.clip(np.floor(first.reshape(-1) * 15), 0, 15).astype(np.int32)
    second_bins = np.clip(np.floor(second.reshape(-1) * 15), 0, 15).astype(np.int32)
    joint = np.zeros((16, 16), dtype=np.float64)
    np.add.at(joint, (first_bins, second_bins), 1)
    joint /= max(float(np.sum(joint)), 1.0)
    first_probability = np.sum(joint, axis=1)
    second_probability = np.sum(joint, axis=0)
    nonzero = joint > 0
    expected = first_probability[:, None] * second_probability[None, :]
    mutual_information = float(np.sum(joint[nonzero] * np.log(joint[nonzero] / expected[nonzero])))
    first_nonzero = first_probability[first_probability > 0]
    second_nonzero = second_probability[second_probability > 0]
    first_entropy = -float(np.sum(first_nonzero * np.log(first_nonzero)))
    second_entropy = -float(np.sum(second_nonzero * np.log(second_nonzero)))
    return max(0.0, min(1.0, 2.0 * mutual_information / max(first_entropy + second_entropy, 1e-8)))


def _channel_relation_expected(first: FluorescencePage, second: FluorescencePage) -> bool:
    roles = {first.inferred_role, second.inferred_role}
    return (
        "merge" in roles
        or (
            first.inferred_role not in {"unknown", "merge"}
            and second.inferred_role not in {"unknown", "merge"}
            and first.inferred_role != second.inferred_role
        )
    ) and bool(first.field_key and first.field_key == second.field_key)


def _classify(
    first: FluorescencePage,
    second: FluorescencePage,
    match: _FluorescenceMatch,
) -> tuple[str, FindingType, RiskLevel, str, str, str] | None:
    roles = {first.inferred_role, second.inferred_role}
    merge_relation = "merge" in roles and len(roles) > 1
    different_named_channels = (
        first.inferred_role not in {"unknown", "merge"}
        and second.inferred_role not in {"unknown", "merge"}
        and first.inferred_role != second.inferred_role
    )
    if merge_relation:
        if match.structure_similarity < 0.78 or match.mask_iou < 0.32 or match.confidence < 0.68:
            return None
        return (
            FluorescenceDuplicateDetector.merge_rule_id,
            FindingType.NORMAL_RELATION,
            RiskLevel.LOW,
            "荧光单通道与 Merge 成分对应",
            "单通道结构与合并图中的对应颜色成分一致，默认属于正常实验图像关系。",
            "normal_merge_component",
        )
    if different_named_channels:
        if (
            match.structure_similarity < 0.58
            or match.normalized_mutual_information < 0.22
            or match.confidence < 0.50
        ):
            return None
        return (
            FluorescenceDuplicateDetector.channel_rule_id,
            FindingType.NORMAL_RELATION,
            RiskLevel.LOW,
            "荧光不同通道疑似同一视野",
            "不同荧光通道的空间结构经配准后对应，默认属于同一视野的正常通道关系。",
            "normal_same_field_channels",
        )
    if match.structure_similarity < 0.88 or match.mask_iou < 0.48 or match.confidence < 0.78:
        return None
    return (
        FluorescenceDuplicateDetector.reuse_rule_id,
        FindingType.SUSPECTED_REUSE,
        RiskLevel.MEDIUM if match.confidence >= 0.90 else RiskLevel.LOW,
        "荧光图同通道或合并图疑似复用",
        "同类荧光证据的空间结构和前景掩膜高度一致，需结合实验分组人工复核。",
        "suspected_same_channel_reuse",
    )


def _match_details(
    first: FluorescencePage,
    second: FluorescencePage,
    match: _FluorescenceMatch,
    relation: str,
) -> dict[str, str | int | float]:
    return {
        "evidence_kind": "fluorescence",
        "relationship_class": relation,
        "transform_second_to_first": match.transform,
        "first_inferred_role": first.inferred_role,
        "second_inferred_role": second.inferred_role,
        "first_channel": match.first_channel,
        "second_channel": match.second_channel,
        "first_region_x": 0,
        "first_region_y": 0,
        "first_region_width": first.width,
        "first_region_height": first.height,
        "second_region_x": 0,
        "second_region_y": 0,
        "second_region_width": second.width,
        "second_region_height": second.height,
        "structure_similarity": round(match.structure_similarity, 6),
        "foreground_mask_iou": round(match.mask_iou, 6),
        "normalized_mutual_information": round(match.normalized_mutual_information, 6),
        "alignment_shift_x": round(match.shift_x, 3),
        "alignment_shift_y": round(match.shift_y, 3),
    }


def _location(page: FluorescencePage) -> EvidenceLocation:
    coordinate = f"第 {page.page} 页；荧光角色 {page.inferred_role}"
    return EvidenceLocation(page.source_path, coordinate=coordinate)


def _page_pair_key(first: FluorescencePage, second: FluorescencePage) -> tuple[str, str]:
    first_key = f"{first.source_path}#{first.page}"
    second_key = f"{second.source_path}#{second.page}"
    return tuple(sorted((first_key, second_key)))
