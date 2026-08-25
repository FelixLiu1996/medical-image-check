from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
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
from medical_image_check.engines.fluorescence import (
    FluorescenceDuplicateDetector,
    FluorescencePage,
)
from medical_image_check.engines.pathology import (
    PathologyDuplicateDetector,
    PathologyRegion,
)
from medical_image_check.engines.western_blot import (
    WesternBlotDuplicateDetector,
    WesternRegion,
)
from medical_image_check.infrastructure.images import (
    ImageFeature,
    apply_transform,
    decode_image_pages,
    extract_image_features_from_pages,
    hamming_distance,
    normalized_similarity,
)


class ImageDuplicateDetector:
    file_rule_id = "image.file.sha256"
    pixel_rule_id = "image.pixel.sha256"
    perceptual_rule_id = "image.global.perceptual"
    local_rule_id = "image.local.geometric"

    def __init__(self, western_single_band_enabled: bool = False) -> None:
        self._fluorescence_detector = FluorescenceDuplicateDetector()
        self._pathology_detector = PathologyDuplicateDetector()
        self._western_detector = WesternBlotDuplicateDetector(western_single_band_enabled)

    def scan(
        self,
        paths: Iterable[Path],
        on_file: Callable[[Path], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[list[Finding], list[ScanIssue]]:
        file_hashes: dict[str, list[Path]] = defaultdict(list)
        features: list[ImageFeature] = []
        fluorescence_pages: list[FluorescencePage] = []
        pathology_regions: list[PathologyRegion] = []
        western_regions: list[WesternRegion] = []
        issues: list[ScanIssue] = []

        for path in paths:
            if checkpoint:
                checkpoint()
            try:
                data = path.read_bytes()
                pages = decode_image_pages(path, data)
                extracted = extract_image_features_from_pages(path, pages)
                file_hashes[sha256(data).hexdigest()].append(path)
                features.extend(extracted)
                fluorescence_pages.extend(
                    self._fluorescence_detector.extract_from_pages(path, pages)
                )
                pathology_regions.extend(self._pathology_detector.extract_from_pages(path, pages))
                western_regions.extend(self._western_detector.extract_from_pages(path, pages))
            except (OSError, ValueError) as exc:
                issues.append(ScanIssue(str(path), f"无法处理图片：{exc}", "error"))
            finally:
                if on_file:
                    on_file(path)

        if checkpoint:
            checkpoint()
        findings, exact_pairs = self._file_findings(file_hashes)
        pixel_findings, pixel_pairs = self._pixel_findings(features, file_hashes)
        findings.extend(pixel_findings)
        exact_pairs.update(pixel_pairs)
        source_duplicate_pairs = set(exact_pairs)
        perceptual_findings, perceptual_pairs = self._perceptual_findings(
            features,
            exact_pairs,
            checkpoint,
        )
        findings.extend(perceptual_findings)
        exact_pairs.update(perceptual_pairs)
        findings.extend(self._local_findings(features, exact_pairs, checkpoint))
        findings.extend(
            self._fluorescence_detector.findings(
                fluorescence_pages,
                source_duplicate_pairs,
                checkpoint,
            )
        )
        findings.extend(
            self._pathology_detector.findings(
                pathology_regions,
                source_duplicate_pairs,
                checkpoint,
            )
        )
        findings.extend(
            self._western_detector.findings(
                western_regions,
                source_duplicate_pairs,
                checkpoint,
            )
        )
        return findings, issues

    def _file_findings(
        self, hashes: dict[str, list[Path]]
    ) -> tuple[list[Finding], set[tuple[str, str]]]:
        findings: list[Finding] = []
        pairs: set[tuple[str, str]] = set()
        for digest, duplicate_paths in hashes.items():
            if len(duplicate_paths) < 2:
                continue
            ordered = sorted(duplicate_paths, key=str)
            locations = tuple(EvidenceLocation(str(path)) for path in ordered)
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.file_rule_id, locations),
                    rule_id=self.file_rule_id,
                    finding_type=FindingType.EXACT_DUPLICATE,
                    risk=RiskLevel.HIGH,
                    title="图片文件完全重复",
                    description=f"{len(locations)} 个图片文件具有相同的 SHA-256 指纹。",
                    locations=locations,
                    details={"sha256": digest, "count": len(locations)},
                )
            )
            pairs.update(
                _feature_pair_key(str(first), 1, str(second), 1)
                for first, second in combinations(ordered, 2)
            )
        return findings, pairs

    def _pixel_findings(
        self,
        features: list[ImageFeature],
        file_hashes: dict[str, list[Path]],
    ) -> tuple[list[Finding], set[tuple[str, str]]]:
        groups: dict[str, list[ImageFeature]] = defaultdict(list)
        for feature in features:
            groups[feature.pixel_sha256].append(feature)
        file_digest_by_path = {
            str(path): digest for digest, paths in file_hashes.items() for path in paths
        }
        findings: list[Finding] = []
        pairs: set[tuple[str, str]] = set()
        for digest, duplicate_features in groups.items():
            if len(duplicate_features) < 2:
                continue
            ordered = sorted(duplicate_features, key=lambda item: (item.source_path, item.page))
            pairs.update(
                _feature_pair_key(first.source_path, first.page, second.source_path, second.page)
                for first, second in combinations(ordered, 2)
            )
            distinct_paths = {item.source_path for item in ordered}
            file_digests = {file_digest_by_path.get(item.source_path) for item in ordered}
            if len(distinct_paths) > 1 and len(file_digests) == 1 and None not in file_digests:
                continue
            locations = tuple(_location(item) for item in ordered)
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.pixel_rule_id, locations),
                    rule_id=self.pixel_rule_id,
                    finding_type=FindingType.EXACT_DUPLICATE,
                    risk=RiskLevel.HIGH,
                    title="图片解码像素完全相同",
                    description=(
                        f"{len(locations)} 个图片内容解码后具有相同像素，文件编码或元数据可以不同。"
                    ),
                    locations=locations,
                    details={"pixel_sha256": digest, "count": len(locations)},
                )
            )
        return findings, pairs

    def _perceptual_findings(
        self,
        features: list[ImageFeature],
        exact_pairs: set[tuple[str, str]],
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[list[Finding], set[tuple[str, str]]]:
        findings: list[Finding] = []
        matched_pairs: set[tuple[str, str]] = set()
        for candidate_index, (first_index, second_index) in enumerate(_candidate_pairs(features)):
            if checkpoint and candidate_index % 128 == 0:
                checkpoint()
            first = features[first_index]
            second = features[second_index]
            pair_key = _feature_pair_key(
                first.source_path,
                first.page,
                second.source_path,
                second.page,
            )
            if pair_key in exact_pairs:
                continue
            match = _best_match(first, second)
            if match is None:
                continue
            matched_pairs.add(pair_key)
            transform, phash_distance, dhash_distance, similarity = match
            locations = (_location(first), _location(second))
            confidence = max(
                0.0,
                min(
                    1.0,
                    0.45 * (1.0 - phash_distance / 64)
                    + 0.25 * (1.0 - dhash_distance / 64)
                    + 0.30 * similarity,
                ),
            )
            risk = (
                RiskLevel.MEDIUM
                if phash_distance <= 4 and dhash_distance <= 6 and similarity >= 0.98
                else RiskLevel.LOW
            )
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.perceptual_rule_id, locations),
                    rule_id=self.perceptual_rule_id,
                    finding_type=FindingType.HIGH_SIMILARITY,
                    risk=risk,
                    title="图片整体高度相似",
                    description=(
                        "感知指纹和标准化缩略图一致，可能存在压缩、缩放、亮度变化、旋转或翻转。"
                    ),
                    locations=locations,
                    confidence=confidence,
                    details={
                        "transform_second_to_first": transform,
                        "phash_distance": phash_distance,
                        "dhash_distance": dhash_distance,
                        "normalized_similarity": round(similarity, 6),
                        "first_size": f"{first.width}x{first.height}",
                        "second_size": f"{second.width}x{second.height}",
                    },
                )
            )
        return findings, matched_pairs

    def _local_findings(
        self,
        features: list[ImageFeature],
        excluded_pairs: set[tuple[str, str]],
        checkpoint: Callable[[], None] | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        candidates = sorted(_local_candidate_pairs(features, excluded_pairs))
        for candidate_index, (first_index, second_index) in enumerate(candidates):
            if checkpoint and candidate_index % 64 == 0:
                checkpoint()
            first = features[first_index]
            second = features[second_index]
            match = _geometric_match(first, second)
            if match is None:
                continue
            locations = (_location(first), _location(second))
            risk = RiskLevel.MEDIUM if match.confidence >= 0.72 else RiskLevel.LOW
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.local_rule_id, locations),
                    rule_id=self.local_rule_id,
                    finding_type=FindingType.SUSPECTED_REUSE,
                    risk=risk,
                    title="图片存在局部重叠",
                    description=(
                        "局部关键点经过双向匹配和几何一致性验证，可能存在裁剪、缩放、旋转或部分重叠。"
                    ),
                    locations=locations,
                    confidence=match.confidence,
                    details=match.details,
                )
            )
        return findings


LOCAL_INDEX_DESCRIPTOR_LIMIT = 96
LOCAL_SIGNATURE_OFFSETS = (0, 4, 8, 12, 16, 20, 24, 28)
LOCAL_SIGNATURE_BUCKET_LIMIT = 64
LOCAL_CANDIDATE_MAX_DISTANCE = 56
LOCAL_CANDIDATE_MIN_VOTES = 3
LOCAL_MATCH_MAX_DISTANCE = 64
LOCAL_MATCH_RATIO = 0.80
LOCAL_MIN_MATCHES = 8
LOCAL_MIN_INLIERS = 8
LOCAL_MIN_INLIER_RATIO = 0.50


@dataclass(frozen=True, slots=True)
class _ModelEstimate:
    model: str
    matrix: NDArray[np.float64]
    inliers: NDArray[np.bool_]
    median_error: float

    @property
    def inlier_count(self) -> int:
        return int(np.count_nonzero(self.inliers))


@dataclass(frozen=True, slots=True)
class _GeometricMatch:
    confidence: float
    details: dict[str, str | int | float]


def _local_candidate_pairs(
    features: list[ImageFeature],
    excluded_pairs: set[tuple[str, str]],
) -> set[tuple[int, int]]:
    index: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    votes: dict[tuple[int, int], int] = defaultdict(int)
    for feature_index, feature in enumerate(features):
        descriptors = feature.local_descriptors[:LOCAL_INDEX_DESCRIPTOR_LIMIT]
        for descriptor in descriptors:
            descriptor_value = int.from_bytes(descriptor.tobytes(), "little")
            matched_features: set[int] = set()
            for band, offset in enumerate(LOCAL_SIGNATURE_OFFSETS):
                signature = int.from_bytes(descriptor[offset : offset + 2].tobytes(), "little")
                key = (band, signature)
                bucket = index[key]
                for previous_index, previous_value in bucket:
                    if previous_index == feature_index:
                        continue
                    pair_key = _feature_pair_key(
                        features[previous_index].source_path,
                        features[previous_index].page,
                        feature.source_path,
                        feature.page,
                    )
                    if pair_key in excluded_pairs:
                        continue
                    if (descriptor_value ^ previous_value).bit_count() <= (
                        LOCAL_CANDIDATE_MAX_DISTANCE
                    ):
                        matched_features.add(previous_index)
                if len(bucket) < LOCAL_SIGNATURE_BUCKET_LIMIT:
                    bucket.append((feature_index, descriptor_value))
            for previous_index in matched_features:
                votes[(previous_index, feature_index)] += 1
    return {pair for pair, vote_count in votes.items() if vote_count >= LOCAL_CANDIDATE_MIN_VOTES}


def _geometric_match(first: ImageFeature, second: ImageFeature) -> _GeometricMatch | None:
    matches = _mutual_ratio_matches(first.local_descriptors, second.local_descriptors)
    if len(matches) < LOCAL_MIN_MATCHES:
        return None

    first_points = np.asarray(
        [first.local_keypoints[first_index] for first_index, _, _ in matches],
        dtype=np.float32,
    )
    second_points = np.asarray(
        [second.local_keypoints[second_index] for _, second_index, _ in matches],
        dtype=np.float32,
    )
    threshold = max(
        3.0,
        0.004 * min(math.hypot(first.width, first.height), math.hypot(second.width, second.height)),
    )
    estimates = _estimate_models(second_points, first_points, threshold)
    if not estimates:
        return None
    estimate = max(
        estimates,
        key=lambda item: (
            item.inlier_count - (2 if item.model == "homography" else 0),
            -item.median_error,
        ),
    )
    inlier_ratio = estimate.inlier_count / len(matches)
    if estimate.inlier_count < LOCAL_MIN_INLIERS or inlier_ratio < LOCAL_MIN_INLIER_RATIO:
        return None

    first_inliers = first_points[estimate.inliers]
    second_inliers = second_points[estimate.inliers]
    first_region = _bounding_region(first_inliers, first.width, first.height)
    second_region = _bounding_region(second_inliers, second.width, second.height)
    first_coverage = first_region[2] * first_region[3] / max(first.width * first.height, 1)
    second_coverage = second_region[2] * second_region[3] / max(second.width * second.height, 1)
    if max(first_coverage, second_coverage) < 0.08 or min(first_coverage, second_coverage) < 0.01:
        return None

    scale_x, scale_y, rotation = _transform_summary(
        estimate.matrix,
        estimate.model,
        second.width / 2,
        second.height / 2,
    )
    error_score = max(0.0, 1.0 - estimate.median_error / max(threshold, 1e-6))
    confidence = max(
        0.0,
        min(
            1.0,
            0.30 * min(estimate.inlier_count / 30, 1.0)
            + 0.25 * inlier_ratio
            + 0.25 * min(max(first_coverage, second_coverage) / 0.40, 1.0)
            + 0.20 * error_score,
        ),
    )
    details: dict[str, str | int | float] = {
        "transform_model": estimate.model,
        "matched_keypoints": len(matches),
        "inlier_count": estimate.inlier_count,
        "inlier_ratio": round(inlier_ratio, 6),
        "median_reprojection_error": round(estimate.median_error, 4),
        "ransac_threshold": round(threshold, 4),
        "first_region_x": first_region[0],
        "first_region_y": first_region[1],
        "first_region_width": first_region[2],
        "first_region_height": first_region[3],
        "second_region_x": second_region[0],
        "second_region_y": second_region[1],
        "second_region_width": second_region[2],
        "second_region_height": second_region[3],
        "first_coverage": round(first_coverage, 6),
        "second_coverage": round(second_coverage, 6),
        "scale_x_second_to_first": round(scale_x, 6),
        "scale_y_second_to_first": round(scale_y, 6),
        "rotation_degrees_second_to_first": round(rotation, 4),
        "first_size": f"{first.width}x{first.height}",
        "second_size": f"{second.width}x{second.height}",
    }
    return _GeometricMatch(confidence=round(confidence, 6), details=details)


def _mutual_ratio_matches(
    first: NDArray[np.uint8],
    second: NDArray[np.uint8],
) -> list[tuple[int, int, float]]:
    if len(first) < 2 or len(second) < 2:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = _ratio_matches(matcher, first, second)
    backward = _ratio_matches(matcher, second, first)
    backward_pairs = {(train_index, query_index) for query_index, train_index, _ in backward}
    return [match for match in forward if (match[0], match[1]) in backward_pairs]


def _ratio_matches(
    matcher: cv2.BFMatcher,
    query: NDArray[np.uint8],
    train: NDArray[np.uint8],
) -> list[tuple[int, int, float]]:
    accepted: list[tuple[int, int, float]] = []
    for neighbors in matcher.knnMatch(query, train, k=2):
        if len(neighbors) < 2:
            continue
        best, second_best = neighbors
        if (
            best.distance <= LOCAL_MATCH_MAX_DISTANCE
            and best.distance < LOCAL_MATCH_RATIO * second_best.distance
        ):
            accepted.append((best.queryIdx, best.trainIdx, float(best.distance)))
    return accepted


def _estimate_models(
    source: NDArray[np.float32],
    destination: NDArray[np.float32],
    threshold: float,
) -> list[_ModelEstimate]:
    estimates: list[_ModelEstimate] = []
    affine, affine_mask = cv2.estimateAffinePartial2D(
        source,
        destination,
        method=cv2.RANSAC,
        ransacReprojThreshold=threshold,
        maxIters=4000,
        confidence=0.995,
        refineIters=10,
    )
    if affine is not None and affine_mask is not None:
        estimates.append(_model_estimate("affine", affine, affine_mask, source, destination))

    if len(source) >= 10:
        homography, homography_mask = cv2.findHomography(
            source,
            destination,
            method=cv2.RANSAC,
            ransacReprojThreshold=threshold,
            maxIters=4000,
            confidence=0.995,
        )
        if homography is not None and homography_mask is not None:
            estimates.append(
                _model_estimate("homography", homography, homography_mask, source, destination)
            )
    return estimates


def _model_estimate(
    model: str,
    matrix: NDArray,
    mask: NDArray,
    source: NDArray[np.float32],
    destination: NDArray[np.float32],
) -> _ModelEstimate:
    inliers = mask.reshape(-1).astype(bool)
    if model == "homography":
        projected = cv2.perspectiveTransform(source.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    else:
        projected = cv2.transform(source.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    errors = np.linalg.norm(projected - destination, axis=1)
    median_error = float(np.median(errors[inliers])) if np.any(inliers) else math.inf
    return _ModelEstimate(
        model=model,
        matrix=np.asarray(matrix, dtype=np.float64),
        inliers=inliers,
        median_error=median_error,
    )


def _bounding_region(
    points: NDArray[np.float32], width: int, height: int
) -> tuple[int, int, int, int]:
    minimum = np.floor(np.min(points, axis=0)).astype(int)
    maximum = np.ceil(np.max(points, axis=0)).astype(int)
    x = max(0, min(int(minimum[0]), width - 1))
    y = max(0, min(int(minimum[1]), height - 1))
    right = max(x + 1, min(int(maximum[0]) + 1, width))
    bottom = max(y + 1, min(int(maximum[1]) + 1, height))
    return x, y, right - x, bottom - y


def _transform_summary(
    matrix: NDArray[np.float64],
    model: str,
    center_x: float,
    center_y: float,
) -> tuple[float, float, float]:
    linear = matrix[:2, :2]
    if model == "homography":
        denominator = float(matrix[2, 0] * center_x + matrix[2, 1] * center_y + matrix[2, 2])
        if abs(denominator) > 1e-12:
            numerator_x = float(matrix[0, 0] * center_x + matrix[0, 1] * center_y + matrix[0, 2])
            numerator_y = float(matrix[1, 0] * center_x + matrix[1, 1] * center_y + matrix[1, 2])
            denominator_squared = denominator**2
            linear = np.asarray(
                [
                    [
                        (matrix[0, 0] * denominator - matrix[2, 0] * numerator_x)
                        / denominator_squared,
                        (matrix[0, 1] * denominator - matrix[2, 1] * numerator_x)
                        / denominator_squared,
                    ],
                    [
                        (matrix[1, 0] * denominator - matrix[2, 0] * numerator_y)
                        / denominator_squared,
                        (matrix[1, 1] * denominator - matrix[2, 1] * numerator_y)
                        / denominator_squared,
                    ],
                ],
                dtype=np.float64,
            )
    scale_x = float(math.hypot(linear[0, 0], linear[1, 0]))
    scale_y = float(math.hypot(linear[0, 1], linear[1, 1]))
    rotation = math.degrees(math.atan2(linear[1, 0], linear[0, 0]))
    return scale_x, scale_y, rotation


def _candidate_pairs(features: list[ImageFeature]) -> set[tuple[int, int]]:
    index: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    candidates: set[tuple[int, int]] = set()
    for feature_index, feature in enumerate(features):
        if feature.standard_deviation < 3.0:
            continue
        seen_keys: set[tuple[str, int, int]] = set()
        for fingerprint in feature.fingerprints:
            for hash_name, value in (("p", fingerprint.phash), ("d", fingerprint.dhash)):
                for band in range(8):
                    key = (hash_name, band, (value >> (band * 8)) & 0xFF)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    for previous in index[key]:
                        if previous != feature_index:
                            candidates.add((previous, feature_index))
                    index[key].append(feature_index)
    return candidates


def _best_match(
    first: ImageFeature,
    second: ImageFeature,
) -> tuple[str, int, int, float] | None:
    if first.standard_deviation < 3.0 or second.standard_deviation < 3.0:
        return None
    identity = first.identity_fingerprint
    best: tuple[str, int, int, float] | None = None
    for fingerprint in second.fingerprints:
        if not _compatible_aspect_ratio(first, second, fingerprint.transform):
            continue
        phash_distance = hamming_distance(identity.phash, fingerprint.phash)
        dhash_distance = hamming_distance(identity.dhash, fingerprint.dhash)
        if phash_distance > 10 or dhash_distance > 12:
            continue
        transformed = apply_transform(second.thumbnail, fingerprint.transform)
        similarity = normalized_similarity(first.thumbnail, transformed)
        if similarity < 0.92:
            continue
        candidate = (fingerprint.transform, phash_distance, dhash_distance, similarity)
        if best is None or (similarity, -phash_distance, -dhash_distance) > (
            best[3],
            -best[1],
            -best[2],
        ):
            best = candidate
    return best


def _compatible_aspect_ratio(first: ImageFeature, second: ImageFeature, transform: str) -> bool:
    second_width, second_height = second.width, second.height
    if transform in {
        "rotate_90",
        "rotate_270",
        "flip_horizontal_rotate_90",
        "flip_horizontal_rotate_270",
    }:
        second_width, second_height = second_height, second_width
    first_ratio = first.width / max(first.height, 1)
    second_ratio = second_width / max(second_height, 1)
    return abs(math.log(max(first_ratio, 1e-12) / max(second_ratio, 1e-12))) <= 0.08


def _location(feature: ImageFeature) -> EvidenceLocation:
    coordinate = f"第 {feature.page} 页" if feature.page_count > 1 else None
    return EvidenceLocation(feature.source_path, coordinate=coordinate)


def _feature_pair_key(
    first_path: str,
    first_page: int,
    second_path: str,
    second_page: int,
) -> tuple[str, str]:
    first = f"{first_path}#{first_page}"
    second = f"{second_path}#{second_page}"
    return tuple(sorted((first, second)))
