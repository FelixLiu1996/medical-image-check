from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from hashlib import sha256
from itertools import combinations
from pathlib import Path

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    deterministic_finding_id,
)
from medical_image_check.infrastructure.images import (
    ImageFeature,
    apply_transform,
    extract_image_features,
    hamming_distance,
    normalized_similarity,
)


class ImageDuplicateDetector:
    file_rule_id = "image.file.sha256"
    pixel_rule_id = "image.pixel.sha256"
    perceptual_rule_id = "image.global.perceptual"

    def scan(
        self,
        paths: Iterable[Path],
        on_file: Callable[[Path], None] | None = None,
    ) -> tuple[list[Finding], list[ScanIssue]]:
        file_hashes: dict[str, list[Path]] = defaultdict(list)
        features: list[ImageFeature] = []
        issues: list[ScanIssue] = []

        for path in paths:
            try:
                data = path.read_bytes()
                extracted = extract_image_features(path, data)
                file_hashes[sha256(data).hexdigest()].append(path)
                features.extend(extracted)
            except (OSError, ValueError) as exc:
                issues.append(ScanIssue(str(path), f"无法处理图片：{exc}", "error"))
            finally:
                if on_file:
                    on_file(path)

        findings, exact_pairs = self._file_findings(file_hashes)
        pixel_findings, pixel_pairs = self._pixel_findings(features, file_hashes)
        findings.extend(pixel_findings)
        exact_pairs.update(pixel_pairs)
        findings.extend(self._perceptual_findings(features, exact_pairs))
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
    ) -> list[Finding]:
        findings: list[Finding] = []
        for first_index, second_index in _candidate_pairs(features):
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
        return findings


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
