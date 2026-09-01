from collections import Counter
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np
import pytest

from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.performance import RuntimeEnvironment
from medical_image_check.engines.image_similarity import (
    ImageDuplicateDetector,
    _bounded_small_image_candidate_pairs,
    _feature_pair_key,
    _is_layout_dominant_pair,
    _local_candidate_pairs,
    _select_ranked_pairs,
    _small_candidate_pairs,
    _small_content_match,
    _small_structural_match,
)
from medical_image_check.infrastructure.images import (
    TransformFingerprint,
    extract_image_features_from_pages,
)
from medical_image_check.infrastructure.performance import PerformanceRecorder


def _synthetic_image() -> np.ndarray:
    random = np.random.default_rng(20260825)
    image = random.integers(20, 80, size=(128, 192, 3), dtype=np.uint8)
    cv2.rectangle(image, (18, 20), (82, 92), (220, 80, 30), -1)
    cv2.circle(image, (142, 70), 30, (40, 230, 180), -1)
    cv2.line(image, (5, 118), (185, 8), (245, 245, 245), 4)
    return image


def _synthetic_local_image(seed: int = 20260825) -> np.ndarray:
    random = np.random.default_rng(seed)
    image = random.integers(10, 90, size=(480, 640, 3), dtype=np.uint8)
    for _ in range(35):
        x = int(random.integers(10, 600))
        y = int(random.integers(10, 440))
        color = tuple(int(value) for value in random.integers(100, 255, size=3))
        cv2.circle(image, (x, y), int(random.integers(4, 20)), color, -1)
    cv2.putText(
        image,
        "MEDICAL 2026",
        (80, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        2,
        (245, 245, 245),
        5,
    )
    return image


def _synthetic_scientific_layout(seed: int) -> np.ndarray:
    random = np.random.default_rng(seed)
    image = np.full((420, 620, 3), 255, dtype=np.uint8)
    for panel, (left, top) in enumerate(((25, 35), (320, 35), (25, 225), (320, 225))):
        cv2.putText(
            image,
            chr(ord("A") + panel),
            (left, top - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (20, 20, 20),
            2,
        )
        cv2.line(image, (left + 30, top + 125), (left + 260, top + 125), (20, 20, 20), 2)
        cv2.line(image, (left + 30, top + 10), (left + 30, top + 125), (20, 20, 20), 2)
        cv2.putText(
            image,
            "Control  Treatment  Time",
            (left + 36, top + 150),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (30, 30, 30),
            1,
        )
        points = []
        for index in range(6):
            x = left + 40 + index * 40
            y = top + 105 - int(random.integers(5, 85))
            points.append((x, y))
            cv2.circle(image, (x, y), 4, (80, 70, 170), -1)
        cv2.polylines(image, [np.asarray(points, dtype=np.int32)], False, (80, 70, 170), 2)
    return image


def test_detector_finds_same_decoded_pixels_across_formats(tmp_path: Path) -> None:
    image = _synthetic_image()
    png = tmp_path / "source.png"
    bmp = tmp_path / "encoded-differently.bmp"
    assert cv2.imwrite(str(png), image)
    assert cv2.imwrite(str(bmp), image)

    findings, issues = ImageDuplicateDetector().scan([png, bmp])

    assert issues == []
    assert {finding.rule_id for finding in findings} == {"image.pixel.sha256"}
    assert png.read_bytes() != bmp.read_bytes()


def test_detector_finds_rotated_and_compressed_whole_image(tmp_path: Path) -> None:
    image = _synthetic_image()
    source = tmp_path / "source.png"
    transformed = tmp_path / "rotated.jpg"
    assert cv2.imwrite(str(source), image)
    assert cv2.imwrite(
        str(transformed),
        np.rot90(image, 1),
        [cv2.IMWRITE_JPEG_QUALITY, 94],
    )

    findings, issues = ImageDuplicateDetector().scan([source, transformed])

    assert issues == []
    perceptual = [item for item in findings if item.rule_id == "image.global.perceptual"]
    assert len(perceptual) == 1
    assert perceptual[0].details["transform_second_to_first"] in {
        "rotate_90",
        "rotate_270",
    }
    assert perceptual[0].details["normalized_similarity"] >= 0.92


def test_detector_scans_each_tiff_page_and_preserves_source(tmp_path: Path) -> None:
    image = _synthetic_image()
    tiff = tmp_path / "pages.tiff"
    original = tmp_path / "original.png"
    assert cv2.imwritemulti(str(tiff), [image, image])
    assert cv2.imwrite(str(original), image)
    before = sha256(tiff.read_bytes()).hexdigest()

    findings, issues = ImageDuplicateDetector().scan([tiff, original])

    assert issues == []
    pixel_findings = [item for item in findings if item.rule_id == "image.pixel.sha256"]
    assert len(pixel_findings) == 1
    assert any("第 2 页" in location.display_text for location in pixel_findings[0].locations)
    assert sha256(tiff.read_bytes()).hexdigest() == before


def test_detector_reports_corrupt_image_without_aborting(tmp_path: Path) -> None:
    corrupt = tmp_path / "broken.png"
    valid = tmp_path / "valid.png"
    corrupt.write_bytes(b"not-an-image")
    assert cv2.imwrite(str(valid), _synthetic_image())

    findings, issues = ImageDuplicateDetector().scan([corrupt, valid])

    assert findings == []
    assert len(issues) == 1
    assert issues[0].source_path == str(corrupt)


def test_detector_finds_rotated_rescaled_crop_with_geometric_evidence(tmp_path: Path) -> None:
    image = _synthetic_local_image()
    cropped = image[105:405, 170:550]
    transformed = cv2.resize(
        np.rot90(cropped, 1),
        (330, 420),
        interpolation=cv2.INTER_AREA,
    )
    source = tmp_path / "source.png"
    reused = tmp_path / "reused-crop.jpg"
    assert cv2.imwrite(str(source), image)
    assert cv2.imwrite(
        str(reused),
        transformed,
        [cv2.IMWRITE_JPEG_QUALITY, 92],
    )

    findings, issues = ImageDuplicateDetector().scan([source, reused])

    assert issues == []
    local = [item for item in findings if item.rule_id == "image.local.geometric"]
    assert len(local) == 1
    assert local[0].risk == "medium"
    assert local[0].details["inlier_count"] >= 8
    assert local[0].details["inlier_ratio"] >= 0.5
    assert local[0].details["first_region_width"] > 0
    assert local[0].details["second_region_height"] > 0
    assert abs(local[0].details["rotation_degrees_second_to_first"]) >= 80


def test_detector_does_not_report_unrelated_local_images(tmp_path: Path) -> None:
    paths: list[Path] = []
    for index, seed in enumerate((11, 22, 33), start=1):
        path = tmp_path / f"unrelated-{index}.png"
        assert cv2.imwrite(str(path), _synthetic_local_image(seed))
        paths.append(path)

    findings, issues = ImageDuplicateDetector().scan(paths)

    assert issues == []
    assert not any(item.rule_id == "image.local.geometric" for item in findings)


def test_bounded_small_image_fallback_recovers_transformed_tissue_crop(
    tmp_path: Path,
) -> None:
    random = np.random.default_rng(5)
    image = np.full((170, 230, 3), 225, dtype=np.uint8)
    noise = random.normal(0, 18, image.shape).astype(np.int16)
    image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    for _ in range(80):
        center = (int(random.integers(3, 227)), int(random.integers(3, 167)))
        radius = int(random.integers(1, 6))
        color = tuple(int(value) for value in random.integers(40, 210, size=3))
        cv2.circle(image, center, radius, color, -1)
    crop = cv2.resize(image[28:148, 35:195], (145, 105), interpolation=cv2.INTER_AREA)
    first = tmp_path / "tissue-a.png"
    second = tmp_path / "tissue-b.png"
    assert cv2.imwrite(str(first), image)
    assert cv2.imwrite(str(second), crop)
    features = [
        extract_image_features_from_pages(first, (image,))[0],
        extract_image_features_from_pages(second, (crop,))[0],
    ]

    assert _local_candidate_pairs(features, set()) == set()
    assert _small_candidate_pairs(features, set()) == set()
    assert _bounded_small_image_candidate_pairs(features, set()) == {(0, 1)}

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert issues == []
    local = [item for item in findings if item.rule_id == "image.local.geometric"]
    assert len(local) == 1
    assert local[0].details["inlier_count"] >= 8
    assert not any(item.rule_id.startswith("image.dot_blot.") for item in findings)


def test_small_image_fallback_does_not_recheck_an_already_matched_local_pair(
    tmp_path: Path,
) -> None:
    image = _synthetic_image()
    first = extract_image_features_from_pages(tmp_path / "first.png", (image,))[0]
    second = replace(first, source_path=str(tmp_path / "second.png"))
    pair_key = _feature_pair_key(first.source_path, first.page, second.source_path, second.page)
    candidate_counts: list[int] = []

    findings = ImageDuplicateDetector()._small_content_findings(
        [first, second],
        {pair_key},
        supplemental_pairs={(0, 1)},
        on_candidate_count=candidate_counts.append,
    )

    assert findings == []
    assert candidate_counts == [0]


def test_small_content_candidate_count_excludes_pairs_without_detail_images(
    tmp_path: Path,
) -> None:
    image = np.full((170, 230, 3), 180, dtype=np.uint8)
    cv2.circle(image, (80, 80), 25, (40, 70, 120), -1)
    first = extract_image_features_from_pages(tmp_path / "first.png", (image,))[0]
    second = replace(first, source_path=str(tmp_path / "second.png"))
    candidate_counts: list[int] = []

    findings = ImageDuplicateDetector()._small_content_findings(
        [first, second],
        set(),
        supplemental_pairs={(0, 1)},
        on_candidate_count=candidate_counts.append,
    )

    assert first.detail_image is None
    assert findings == []
    assert candidate_counts == [0]


def test_generic_performance_keeps_result_and_candidate_counts_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = ImageDuplicateDetector()

    def fake_local_findings(
        features: object,
        excluded_pairs: object,
        checkpoint: object,
        supplemental_pairs: object,
        on_candidate_count: object,
    ) -> list[object]:
        del features, excluded_pairs, checkpoint, supplemental_pairs
        assert callable(on_candidate_count)
        on_candidate_count(5)
        return []

    def fake_small_findings(
        features: object,
        excluded_pairs: object,
        checkpoint: object,
        supplemental_pairs: object,
        on_candidate_count: object,
    ) -> list[object]:
        del features, excluded_pairs, checkpoint, supplemental_pairs
        assert callable(on_candidate_count)
        on_candidate_count(3)
        return []

    monkeypatch.setattr(detector, "_local_findings", fake_local_findings)
    monkeypatch.setattr(detector, "_small_content_findings", fake_small_findings)
    profiler = PerformanceRecorder()

    findings, issues = detector.scan([], profiler=profiler)
    performance = profiler.finish(
        RuntimeEnvironment(
            operating_system="test",
            os_release="test",
            machine="test",
            processor="test",
            logical_cpu_count=1,
            python_version="3.12",
            opencv_version=cv2.__version__,
        )
    )
    stages = {stage.stage_id: stage for stage in performance.stages}

    assert findings == []
    assert issues == []
    assert stages["image.local_geometric_candidates"].items == 5
    assert stages["image.local_geometric_verification"].items == 0
    assert stages["image.small_content_candidates"].items == 3
    assert stages["image.small_content_verification"].items == 0


def test_small_image_fallback_candidate_budget_is_global_and_per_image() -> None:
    image = _synthetic_image()
    template = extract_image_features_from_pages("template.png", (image,))[0]
    features = [replace(template, source_path=f"small-{index:02d}.png") for index in range(40)]

    first = _bounded_small_image_candidate_pairs(features, set())
    second = _bounded_small_image_candidate_pairs(features, set())
    counts = Counter(index for pair in first for index in pair)

    assert first == second
    assert len(first) <= 64
    assert max(counts.values()) <= 8


def test_small_image_fallback_budget_covers_independent_low_vote_pair() -> None:
    image = _synthetic_image()
    template = extract_image_features_from_pages("template.png", (image,))[0]

    def repeated_hash(value: int) -> int:
        return sum(value << (band * 8) for band in range(8))

    dense_fingerprint = TransformFingerprint(
        "identity",
        repeated_hash(1),
        repeated_hash(2),
    )
    shared_low_bytes = sum(3 << (band * 8) for band in range(4))
    independent = (
        TransformFingerprint(
            "identity",
            shared_low_bytes + sum(4 << (band * 8) for band in range(4, 8)),
            repeated_hash(6),
        ),
        TransformFingerprint(
            "identity",
            shared_low_bytes + sum(5 << (band * 8) for band in range(4, 8)),
            repeated_hash(7),
        ),
    )
    features = [
        replace(
            template,
            source_path=f"dense-{index:02d}.png",
            fingerprints=(dense_fingerprint,),
        )
        for index in range(20)
    ]
    features.extend(
        replace(
            template,
            source_path=f"independent-{index}.png",
            fingerprints=(fingerprint,),
        )
        for index, fingerprint in enumerate(independent)
    )

    selected = _bounded_small_image_candidate_pairs(features, set())
    counts = Counter(index for pair in selected for index in pair)

    assert (20, 21) in selected
    assert len(selected) <= 64
    assert max(counts.values()) <= 8


def test_small_candidate_bucket_cap_still_compares_tail_features() -> None:
    image = _synthetic_image()
    template = extract_image_features_from_pages("template.png", (image,))[0]
    features = [replace(template, source_path=f"same-{index:02d}.png") for index in range(70)]

    primary = _small_candidate_pairs(features, set())
    fallback = _bounded_small_image_candidate_pairs(features, set())

    assert set(range(70)) <= {index for pair in primary for index in pair}
    assert set(range(70)) <= {index for pair in fallback for index in pair}
    assert (64, 65) in primary
    assert (64, 65) in fallback


def test_local_candidate_bucket_cap_still_compares_tail_features() -> None:
    image = _synthetic_image()
    template = extract_image_features_from_pages("template.png", (image,))[0]
    descriptors = np.zeros((3, 32), dtype=np.uint8)
    features = [
        replace(
            template,
            source_path=f"same-local-{index:02d}.png",
            local_descriptors=descriptors,
        )
        for index in range(66)
    ]

    selected = _local_candidate_pairs(features, set())

    assert (64, 65) in selected


def test_large_batch_primary_candidate_budgets_are_deterministic() -> None:
    image = _synthetic_image()
    template = extract_image_features_from_pages("template.png", (image,))[0]
    assert len(template.local_descriptors) >= 3
    template = replace(template, local_descriptors=template.local_descriptors[:3])
    features = [replace(template, source_path=f"panel-{index:03d}.png") for index in range(140)]

    local_first = _local_candidate_pairs(features, set())
    local_second = _local_candidate_pairs(features, set())
    small_first = _small_candidate_pairs(features, set())
    small_second = _small_candidate_pairs(features, set())
    local_counts = Counter(index for pair in local_first for index in pair)
    small_counts = Counter(index for pair in small_first for index in pair)

    assert local_first == local_second
    assert small_first == small_second
    assert len(local_first) <= 512
    assert len(small_first) <= 256
    assert max(local_counts.values()) <= 16
    assert max(small_counts.values()) <= 16


def test_ranked_pair_budget_covers_independent_features_before_score_fill() -> None:
    eligible = [
        (100, (0, 1)),
        (99, (0, 2)),
        (98, (1, 2)),
        (10, (3, 4)),
    ]

    first = _select_ranked_pairs(eligible, pair_limit=3, per_feature_limit=2)
    second = _select_ranked_pairs(eligible, pair_limit=3, per_feature_limit=2)
    counts = Counter(index for pair in first for index in pair)

    assert first == second == {(0, 1), (0, 2), (3, 4)}
    assert set(counts) == {0, 1, 2, 3, 4}
    assert max(counts.values()) <= 2


def test_ranked_pair_selection_without_global_limit_keeps_score_order_budgeting() -> None:
    eligible = [
        (100, (0, 1)),
        (99, (0, 2)),
        (10, (3, 4)),
    ]

    selected = _select_ranked_pairs(eligible, pair_limit=None, per_feature_limit=1)

    assert selected == {(0, 1), (3, 4)}


def test_detector_suppresses_white_background_scientific_layout_matches(
    tmp_path: Path,
) -> None:
    first_image = _synthetic_scientific_layout(11)
    second_image = _synthetic_scientific_layout(22)
    first = tmp_path / "layout-a.png"
    second = tmp_path / "layout-b.png"
    assert cv2.imwrite(str(first), first_image)
    assert cv2.imwrite(str(second), second_image)
    first_feature = extract_image_features_from_pages(first, (first_image,))[0]
    second_feature = extract_image_features_from_pages(second, (second_image,))[0]

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.GENERIC).scan(
        [first, second]
    )

    assert issues == []
    assert _is_layout_dominant_pair(first_feature, second_feature) is True
    assert not any(
        item.rule_id
        in {
            "image.global.perceptual",
            "image.local.geometric",
            "image.small_region.content_reuse",
        }
        for item in findings
    )


def test_small_structural_match_finds_shifted_low_texture_strip() -> None:
    x = np.linspace(-1.0, 1.0, 112, dtype=np.float32)
    y = np.linspace(-1.0, 1.0, 44, dtype=np.float32)[:, None]
    strip = 205 - 65 * np.exp(-((x / 0.42) ** 2 + (y / 0.19) ** 2))
    strip += 8 * np.sin(np.arange(112, dtype=np.float32) / 7)[None, :]
    first = np.clip(strip, 0, 255).astype(np.uint8)
    second = cv2.warpAffine(
        cv2.convertScaleAbs(first, alpha=0.78, beta=35),
        np.asarray([[1.0, 0.0, 4.0], [0.0, 1.0, 2.0]], dtype=np.float32),
        (112, 44),
        borderMode=cv2.BORDER_REFLECT,
    )

    match = _small_structural_match(first, second)

    assert match is not None
    assert match.details["verification_method"] == "dense_structure"
    assert match.details["highpass_correlation"] >= 0.65
    assert match.details["gradient_correlation"] >= 0.65


def test_small_content_match_finds_mirrored_microscopy_crop() -> None:
    image = _synthetic_local_image(913)[120:240, 170:330]
    first_image = image[10:105, 12:145]
    second_image = cv2.resize(
        cv2.flip(image, 1),
        (176, 126),
        interpolation=cv2.INTER_AREA,
    )[8:118, 8:168]
    first = extract_image_features_from_pages(Path("first.png"), (first_image,))[0]
    second = extract_image_features_from_pages(Path("second.png"), (second_image,))[0]

    match = _small_content_match(0, 1, first, second, {})

    assert match is not None
    assert match.details["verification_method"] == "sift_geometry"
    assert match.details["inlier_count"] >= 6


def test_small_content_match_rejects_unrelated_regions() -> None:
    first_random = np.random.default_rng(101)
    second_random = np.random.default_rng(202)
    first_image = first_random.integers(10, 180, size=(80, 120, 3), dtype=np.uint8)
    second_image = second_random.integers(10, 180, size=(80, 120, 3), dtype=np.uint8)
    cv2.circle(first_image, (30, 30), 14, (220, 80, 40), -1)
    cv2.rectangle(second_image, (75, 45), (108, 70), (40, 220, 180), -1)
    first = extract_image_features_from_pages(Path("first.png"), (first_image,))[0]
    second = extract_image_features_from_pages(Path("second.png"), (second_image,))[0]

    assert _small_content_match(0, 1, first, second, {}) is None


def test_small_content_fallback_rejects_many_unrelated_strips(tmp_path: Path) -> None:
    paths: list[Path] = []
    for index in range(24):
        random = np.random.default_rng(7000 + index)
        height = int(random.integers(24, 58))
        width = int(random.integers(65, 176))
        image = random.normal(178, 22, size=(height, width)).astype(np.float32)
        image = cv2.GaussianBlur(image, (0, 0), float(random.uniform(0.6, 1.8)))
        for _ in range(int(random.integers(1, 4))):
            x = int(random.integers(3, width - 20))
            y = int(random.integers(3, height - 5))
            band_width = int(random.integers(12, min(65, width - x)))
            band_height = int(random.integers(2, min(8, height - y)))
            cv2.rectangle(
                image,
                (x, y),
                (x + band_width, y + band_height),
                float(random.integers(35, 125)),
                -1,
            )
        path = tmp_path / f"negative-strip-{index:02d}.png"
        assert cv2.imwrite(str(path), np.clip(image, 0, 255).astype(np.uint8))
        paths.append(path)

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.GENERIC).scan(paths)

    assert issues == []
    assert not any(item.rule_id == "image.small_region.content_reuse" for item in findings)


def test_detector_finds_partial_overlap_between_two_crops(tmp_path: Path) -> None:
    random = np.random.default_rng(77)
    image = random.integers(0, 100, size=(600, 800, 3), dtype=np.uint8)
    for _ in range(60):
        x = int(random.integers(10, 790))
        y = int(random.integers(10, 590))
        color = tuple(int(value) for value in random.integers(120, 255, size=3))
        cv2.circle(image, (x, y), int(random.integers(4, 18)), color, -1)
    first = tmp_path / "left-crop.png"
    second = tmp_path / "right-crop.jpg"
    assert cv2.imwrite(str(first), image[40:440, 30:530])
    assert cv2.imwrite(
        str(second),
        image[160:560, 280:780],
        [cv2.IMWRITE_JPEG_QUALITY, 90],
    )

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert issues == []
    local = [item for item in findings if item.rule_id == "image.local.geometric"]
    assert len(local) == 1
    assert local[0].details["inlier_count"] >= 8
    assert 0.05 <= local[0].details["first_coverage"] < 0.8
    assert 0.05 <= local[0].details["second_coverage"] < 0.8


def _dot_blot_image(width: int = 720, height: int = 160) -> np.ndarray:
    image = np.full((height, width, 3), 238, dtype=np.uint8)
    for center, radius, value in zip(
        ((115, 78), (305, 66), (470, 76), (620, 76)),
        (46, 15, 44, 49),
        (60, 195, 25, 10),
        strict=True,
    ):
        cv2.circle(image, center, radius, (value, value, value), -1)
    return image


def test_detector_finds_dot_blot_layout_after_crop_scale_and_contrast(tmp_path: Path) -> None:
    source_image = _dot_blot_image()
    transformed = cv2.resize(source_image[:, 45:690], (360, 110), interpolation=cv2.INTER_AREA)
    transformed = cv2.convertScaleAbs(transformed, alpha=0.55, beta=105)
    first = tmp_path / "source-dot.png"
    second = tmp_path / "adjusted-dot.png"
    assert cv2.imwrite(str(first), source_image)
    assert cv2.imwrite(str(second), transformed)

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert issues == []
    dot = next(item for item in findings if item.rule_id == "image.dot_blot.spot_array_reuse")
    assert dot.details["matched_spot_count"] >= 3
    assert dot.details["layout_similarity"] >= 0.8
    assert dot.details["profile_similarity"] >= 0.65
    assert dot.title == "局部重复结构疑似复用"
    assert dot.details["evidence_kind"] == "local_pattern"
    assert dot.details["technical_detector"] == "dot_blot_layout"
    assert dot.details["medical_modality_claimed"] is False
    assert not any(item.rule_id.startswith("image.pathology.") for item in findings)


def test_generic_image_mode_skips_medical_specialists(tmp_path: Path) -> None:
    first = tmp_path / "first-dot.png"
    second = tmp_path / "second-dot.png"
    image = _dot_blot_image()
    assert cv2.imwrite(str(first), image)
    assert cv2.imwrite(str(second), cv2.convertScaleAbs(image, alpha=0.8, beta=25))

    findings, _ = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.GENERIC).scan(
        [first, second]
    )

    assert not any(item.rule_id.startswith("image.dot_blot.") for item in findings)
