from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.performance import RuntimeEnvironment
from medical_image_check.engines import dot_blot as dot_blot_module
from medical_image_check.engines.dot_blot import DOT_BLOT_RULE_ID, DotBlotDuplicateDetector
from medical_image_check.engines.image_similarity import (
    ImageDuplicateDetector,
    _western_dominant_pages,
)
from medical_image_check.engines.western_blot import WesternBlotDuplicateDetector
from medical_image_check.evaluation.dot_blot import evaluate_dot_blot_manifest
from medical_image_check.infrastructure.performance import PerformanceRecorder


def _patterned_dot_row(
    centers: tuple[int, ...],
    *,
    width: int,
    height: int = 180,
) -> np.ndarray:
    image = np.full((height, width, 3), 238, dtype=np.uint8)
    radii = (26, 18, 28, 21, 31, 20, 25, 17)
    values = (55, 142, 35, 105, 22, 170, 70, 125)
    for index, center_x in enumerate(centers):
        radius = radii[index % len(radii)]
        value = values[index % len(values)]
        center = (center_x, height // 2 + (index % 3 - 1) * 2)
        cv2.circle(image, center, radius, (value, value, value), -1)
        if index % 3 == 0:
            cv2.circle(
                image,
                (center[0] - radius // 4, center[1] - radius // 5),
                max(3, radius // 4),
                (min(230, value + 95),) * 3,
                -1,
            )
        elif index % 3 == 1:
            cv2.ellipse(
                image,
                center,
                (max(3, radius // 2), max(3, radius // 3)),
                25,
                0,
                360,
                (max(0, value - 35),) * 3,
                -1,
            )
    return image


def _write(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image)


def test_dot_blot_matches_three_spot_crop_inside_eight_spot_row(tmp_path: Path) -> None:
    centers = (70, 155, 250, 345, 455, 550, 660, 755)
    full_image = _patterned_dot_row(centers, width=830)
    subset = full_image[45:140, 215:495]
    subset = cv2.resize(subset, (390, 135), interpolation=cv2.INTER_CUBIC)
    rotation = cv2.getRotationMatrix2D((195, 67), 6.0, 1.0)
    subset = cv2.warpAffine(subset, rotation, (390, 135), borderValue=(245, 245, 245))
    subset = cv2.convertScaleAbs(subset, alpha=0.72, beta=58)
    full_path = tmp_path / "eight-spots.png"
    subset_path = tmp_path / "three-spot-crop.png"
    _write(full_path, full_image)
    _write(subset_path, subset)

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.DOT_BLOT).scan(
        [full_path, subset_path]
    )

    assert issues == []
    dot = next(item for item in findings if item.rule_id == DOT_BLOT_RULE_ID)
    assert dot.title == "Dot blot 斑点阵列疑似复用"
    assert dot.details["evidence_kind"] == "dot_blot"
    assert dot.details["matched_spot_count"] == 3
    assert 3 in {dot.details["first_spot_count"], dot.details["second_spot_count"]}
    assert 8 in {dot.details["first_spot_count"], dot.details["second_spot_count"]}
    assert dot.details["appearance_similarity"] >= 0.68
    assert abs(dot.details["rotation_degrees_second_to_first"]) >= 3.0
    assert dot.details["first_region_width"] < full_image.shape[1]


def test_auto_route_accepts_spot_arrays_and_rejects_scientific_schematic() -> None:
    detector = DotBlotDuplicateDetector()
    dot = _patterned_dot_row((70, 160, 270, 390), width=470)
    schematic = np.full((260, 520, 3), 255, dtype=np.uint8)
    for row, color in enumerate(((40, 40, 180), (30, 140, 50))):
        y = 75 + row * 110
        for column in range(4):
            center = (85 + column * 115, y)
            cv2.rectangle(
                schematic,
                (center[0] - 34, center[1] - 18),
                (center[0] + 34, center[1] + 18),
                color,
                2,
            )
            if column:
                cv2.arrowedLine(
                    schematic,
                    (center[0] - 80, center[1]),
                    (center[0] - 38, center[1]),
                    (30, 30, 30),
                    2,
                )
            cv2.putText(
                schematic,
                f"N{row}{column}",
                (center[0] - 19, center[1] + 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (20, 20, 20),
                1,
                cv2.LINE_AA,
            )

    assert detector.route_auto_pages((dot, schematic)) == (True, False)


def test_auto_route_rejects_bar_chart_with_common_baseline() -> None:
    chart = np.full((260, 320, 3), 255, dtype=np.uint8)
    baseline = 190
    cv2.line(chart, (35, 20), (35, baseline), (20, 20, 20), 2)
    cv2.line(chart, (35, baseline), (300, baseline), (20, 20, 20), 2)
    for left, height in zip((65, 125, 185, 245), (55, 105, 72, 135), strict=True):
        cv2.rectangle(
            chart,
            (left, baseline - height),
            (left + 30, baseline),
            (35, 35, 35),
            -1,
        )
        cv2.line(
            chart,
            (left + 15, baseline - height - 12),
            (left + 15, baseline - height + 4),
            (35, 35, 35),
            2,
        )

    assert DotBlotDuplicateDetector().route_auto_pages((chart,)) == (False,)


def test_auto_route_accepts_small_dot_array_embedded_in_large_page(tmp_path: Path) -> None:
    page = np.full((800, 800, 3), 240, dtype=np.uint8)
    for center_x, value in zip((200, 320, 456, 600), (50, 90, 130, 60), strict=True):
        cv2.circle(page, (center_x, 400), 25, (value,) * 3, -1)

    assert DotBlotDuplicateDetector().route_auto_pages((page,)) == (True,)
    path = tmp_path / "embedded-small-dot.png"
    _write(path, page)
    profiler = PerformanceRecorder()

    _, issues = ImageDuplicateDetector().scan([path], profiler=profiler)
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

    assert issues == []
    assert stages["image.dot_blot_routing"].items == 1
    assert stages["image.dot_blot_features"].items >= 1


def test_auto_scan_skips_expensive_dot_extractor_for_rejected_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schematic = np.full((220, 420, 3), 255, dtype=np.uint8)
    for column in range(5):
        left = 25 + column * 78
        cv2.rectangle(schematic, (left, 70), (left + 54, 108), (30, 100, 180), 2)
        cv2.putText(
            schematic,
            f"P{column}",
            (left + 13, 95),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (20, 20, 20),
            1,
            cv2.LINE_AA,
        )
        if column:
            cv2.arrowedLine(
                schematic,
                (left - 22, 89),
                (left - 2, 89),
                (20, 20, 20),
                2,
            )
    path = tmp_path / "schematic.png"
    _write(path, schematic)

    def fail_if_extracted(*args: object, **kwargs: object) -> tuple[object, ...]:
        del args, kwargs
        raise AssertionError("未通过 AUTO 准入的页面不应进入 Dot blot 完整提取")

    monkeypatch.setattr(dot_blot_module, "_extract_page", fail_if_extracted)

    profiler = PerformanceRecorder()
    findings, issues = ImageDuplicateDetector().scan([path], profiler=profiler)
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

    assert issues == []
    assert not any(item.rule_id == DOT_BLOT_RULE_ID for item in findings)
    assert stages["image.dot_blot_routing"].calls == 1
    assert stages["image.dot_blot_routing"].items == 0
    assert stages["image.dot_blot_features"].items == 0
    assert stages["image.dot_blot_verification"].items == 0


@pytest.mark.parametrize(
    ("analysis_mode", "expected_global_limit", "expected_page_pair_limit"),
    [
        (ImageAnalysisMode.AUTO, 256, 4),
        (ImageAnalysisMode.DOT_BLOT, None, None),
    ],
)
def test_dot_candidate_budget_wiring_and_performance_item_semantics(
    monkeypatch: pytest.MonkeyPatch,
    analysis_mode: ImageAnalysisMode,
    expected_global_limit: int | None,
    expected_page_pair_limit: int | None,
) -> None:
    detector = ImageDuplicateDetector(analysis_mode=analysis_mode)
    captured: dict[str, int | None] = {}

    def fake_findings(
        regions: object,
        excluded_pairs: object,
        checkpoint: object,
        on_candidate_count: object,
        *,
        candidate_pair_limit: int | None,
        per_page_pair_limit: int | None,
    ) -> list[object]:
        del regions, excluded_pairs, checkpoint
        captured["candidate_pair_limit"] = candidate_pair_limit
        captured["per_page_pair_limit"] = per_page_pair_limit
        assert callable(on_candidate_count)
        on_candidate_count(7)
        return []

    monkeypatch.setattr(detector._dot_blot_detector, "findings", fake_findings)
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
    assert captured == {
        "candidate_pair_limit": expected_global_limit,
        "per_page_pair_limit": expected_page_pair_limit,
    }
    assert stages["image.dot_blot_candidates"].items == 7
    assert stages["image.dot_blot_verification"].items == 0


def test_auto_scan_routes_western_dominant_page_away_from_dot_extractor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    western = np.full((150, 520, 3), 235, dtype=np.uint8)
    for row, center_y in enumerate((50, 105)):
        cv2.rectangle(western, (15, center_y - 20), (505, center_y + 20), (205,) * 3, -1)
        for index, center_x in enumerate((65, 160, 260, 365, 455)):
            axes = (20 + (index % 3) * 4, 7 + ((index + row) % 2) * 2)
            cv2.ellipse(
                western,
                (center_x, center_y),
                axes,
                0,
                0,
                360,
                (35 + index * 18,) * 3,
                -1,
            )
    path = tmp_path / "neutral-gray-panel.png"
    _write(path, western)
    assert DotBlotDuplicateDetector().route_auto_pages((western,)) == (False,)
    western_regions = WesternBlotDuplicateDetector().extract_from_pages(path, (western,))
    assert len(western_regions) >= 2
    small_regions = tuple(
        replace(region, region=(10 + index * 30, 10, 20, 10))
        for index, region in enumerate(western_regions[:2])
    )
    combined_regions = tuple(
        replace(region, region=(20, 10 + index * 70, 250, 55))
        for index, region in enumerate(western_regions[:2])
    )
    assert _western_dominant_pages(small_regions, (western,)) == set()
    assert _western_dominant_pages(combined_regions, (western,)) == {1}
    assert _western_dominant_pages((western_regions[0],), (western,)) == {1}

    def fail_if_extracted(*args: object, **kwargs: object) -> tuple[object, ...]:
        del args, kwargs
        raise AssertionError("Western 已准入的页面不应再进入 Dot blot 完整提取")

    monkeypatch.setattr(dot_blot_module, "_extract_page", fail_if_extracted)

    findings, issues = ImageDuplicateDetector().scan([path])

    assert issues == []
    assert not any(item.rule_id == DOT_BLOT_RULE_ID for item in findings)


def test_auto_scan_keeps_dot_route_for_mixed_western_and_dot_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    random = np.random.default_rng(11)
    height, width = 500, 800
    _, x = np.mgrid[:height, :width]
    gray = np.clip(
        225 + 2 * np.sin(x / 57) + random.normal(0, 2, (height, width)),
        0,
        255,
    ).astype(np.uint8)
    for center_x, value in zip((100, 230, 370, 520), (45, 90, 140, 60), strict=True):
        cv2.circle(gray, (center_x, 120), 30, int(value), -1)
    for center_x, band_width in zip((120, 300, 500, 680), (44, 36, 52, 40), strict=True):
        cv2.ellipse(gray, (center_x, 360), (band_width // 2, 7), 0, 0, 360, 55, -1)
    mixed = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    path = tmp_path / "mixed-panel.png"
    _write(path, mixed)

    western = WesternBlotDuplicateDetector().extract_from_pages(path, (mixed,))
    assert len(western) == 1
    assert _western_dominant_pages(western, (mixed,)) == set()
    assert DotBlotDuplicateDetector().route_auto_pages((mixed,)) == (True,)

    original = dot_blot_module._extract_page
    calls = 0

    def counted_extract(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dot_blot_module, "_extract_page", counted_extract)

    _, issues = ImageDuplicateDetector().scan([path])

    assert issues == []
    assert calls == 1


def test_auto_scan_keeps_independent_dot_array_with_two_small_western_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    random = np.random.default_rng(123)
    height = width = 800
    gray = np.clip(
        230 + random.normal(0, 1.5, (height, width)),
        0,
        255,
    ).astype(np.uint8)
    for center_x, value, radius in zip(
        (120, 290, 480, 670),
        (40, 85, 135, 55),
        (30, 22, 34, 28),
        strict=True,
    ):
        cv2.circle(gray, (center_x, 130), radius, int(value), -1)
    for center_y in (390, 650):
        for center_x, band_width, value in zip(
            (120, 300, 500, 680),
            (44, 36, 52, 40),
            (50, 80, 110, 65),
            strict=True,
        ):
            cv2.ellipse(
                gray,
                (center_x, center_y),
                (band_width // 2, 6),
                0,
                0,
                360,
                int(value),
                -1,
            )
    mixed = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    path = tmp_path / "independent-dot-and-two-western-rows.png"
    _write(path, mixed)

    western = WesternBlotDuplicateDetector().extract_from_pages(path, (mixed,))
    assert len(western) == 2
    assert all(region.region[2] * region.region[3] / (width * height) < 0.10 for region in western)
    assert _western_dominant_pages(western, (mixed,)) == set()
    assert DotBlotDuplicateDetector().route_auto_pages((mixed,)) == (True,)

    original = dot_blot_module._extract_page
    calls = 0

    def counted_extract(*args: object, **kwargs: object) -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dot_blot_module, "_extract_page", counted_extract)

    _, issues = ImageDuplicateDetector().scan([path])

    assert issues == []
    assert calls == 1


def test_explicit_dot_blot_mode_does_not_use_auto_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _patterned_dot_row((70, 160, 270, 390), width=470)
    transformed = cv2.resize(source[35:145, 35:440], (520, 140), interpolation=cv2.INTER_CUBIC)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _write(first, source)
    _write(second, transformed)

    def fail_if_routed(pages: tuple[np.ndarray, ...]) -> tuple[bool, ...]:
        del pages
        raise AssertionError("显式 Dot blot 模式不应调用 AUTO 路由")

    monkeypatch.setattr(DotBlotDuplicateDetector, "route_auto_pages", fail_if_routed)

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.DOT_BLOT).scan(
        [first, second]
    )

    assert issues == []
    dot = next(item for item in findings if item.rule_id == DOT_BLOT_RULE_ID)
    assert dot.title == "Dot blot 斑点阵列疑似复用"
    assert dot.details["evidence_kind"] == "dot_blot"


def test_dot_blot_appearance_work_is_bounded_by_region_spot_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_image = _patterned_dot_row((70, 155, 250, 345, 455, 550, 660, 755), width=830)
    second_image = cv2.convertScaleAbs(first_image, alpha=0.76, beta=48)
    detector = DotBlotDuplicateDetector()
    first = detector.extract_from_pages("first.png", (first_image,))[0]
    second = detector.extract_from_pages("second.png", (second_image,))[0]
    calls = 0
    requested_pairs: set[tuple[int, int, int]] = set()
    original = dot_blot_module._patch_similarity
    original_similarities = dot_blot_module._SequenceAppearanceCache.similarities

    def counted_patch_similarity(left: np.ndarray, right: np.ndarray) -> float:
        nonlocal calls
        calls += 1
        return original(left, right)

    def counted_similarities(
        cache: dot_blot_module._SequenceAppearanceCache,
        first_index: int,
        second_index: int,
    ) -> tuple[float, float]:
        requested_pairs.add((id(cache), first_index, second_index))
        return original_similarities(cache, first_index, second_index)

    monkeypatch.setattr(dot_blot_module, "_patch_similarity", counted_patch_similarity)
    monkeypatch.setattr(
        dot_blot_module._SequenceAppearanceCache,
        "similarities",
        counted_similarities,
    )

    match = dot_blot_module._best_match(first, second)

    assert match is not None
    assert calls == 2 * len(requested_pairs)
    assert 0 < len(requested_pairs) < 2 * len(first.spots) * len(second.spots)


def test_dot_blot_appearance_cache_preserves_pair_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_image = _patterned_dot_row((70, 155, 250, 345, 455, 550, 660, 755), width=830)
    second_image = cv2.convertScaleAbs(first_image, alpha=0.76, beta=48)
    detector = DotBlotDuplicateDetector()
    first = detector.extract_from_pages("first.png", (first_image,))[0]
    second = detector.extract_from_pages("second.png", (second_image,))[0]
    first_indexes = (0, 2, 4, 6)
    second_indexes = (1, 3, 5, 7)
    rotation = 7.0
    original = dot_blot_module._patch_similarity

    expected_pairs = []
    for first_index, second_index in zip(first_indexes, second_indexes, strict=True):
        rotated = dot_blot_module._rotate_patch(
            second.spots[second_index].appearance,
            rotation,
        )
        expected_pairs.append(
            (
                float(np.float32(original(first.spots[first_index].appearance, rotated))),
                float(
                    np.float32(original(first.spots[first_index].appearance, cv2.flip(rotated, 1)))
                ),
            )
        )
    direct = [item[0] for item in expected_pairs]
    mirrored = [item[1] for item in expected_pairs]
    use_mirror = float(np.mean(mirrored)) > float(np.mean(direct)) + 0.025
    selected = mirrored if use_mirror else direct
    expected = (float(np.mean(selected)), float(np.min(selected)), use_mirror)
    calls = 0

    def counted_patch_similarity(left: np.ndarray, right: np.ndarray) -> float:
        nonlocal calls
        calls += 1
        return original(left, right)

    monkeypatch.setattr(dot_blot_module, "_patch_similarity", counted_patch_similarity)
    cache = dot_blot_module._SequenceAppearanceCache(first, second, rotation)

    actual = dot_blot_module._sequence_appearance_similarity(
        first_indexes,
        second_indexes,
        cache,
    )
    cached = dot_blot_module._sequence_appearance_similarity(
        first_indexes,
        second_indexes,
        cache,
    )

    assert actual[:2] == pytest.approx(expected[:2])
    assert actual[2] is expected[2]
    assert cached == actual
    assert calls == 2 * len(first_indexes)


def test_dot_blot_subset_distinctiveness_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_image = _patterned_dot_row((70, 155, 250, 345, 455, 550, 660, 755), width=830)
    second_image = cv2.convertScaleAbs(first_image, alpha=0.76, beta=48)
    detector = DotBlotDuplicateDetector()
    first = detector.extract_from_pages("first.png", (first_image,))[0]
    second = detector.extract_from_pages("second.png", (second_image,))[0]
    original = dot_blot_module._spot_distinctiveness
    seen: set[tuple[int, ...]] = set()

    def unique_subset_distinctiveness(
        spots: tuple[dot_blot_module.DotBlotSpot, ...],
    ) -> float:
        key = tuple(id(spot) for spot in spots)
        assert key not in seen
        seen.add(key)
        return original(spots)

    monkeypatch.setattr(
        dot_blot_module,
        "_spot_distinctiveness",
        unique_subset_distinctiveness,
    )

    assert dot_blot_module._best_match(first, second) is not None
    assert seen


def test_dot_blot_rejects_incompatible_profiles_before_patch_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_image = _patterned_dot_row((70, 155, 250, 345, 455, 550), width=630)
    second_image = cv2.convertScaleAbs(first_image, alpha=0.76, beta=48)
    detector = DotBlotDuplicateDetector()
    first = detector.extract_from_pages("first.png", (first_image,))[0]
    second = detector.extract_from_pages("second.png", (second_image,))[0]
    calls = 0

    def counted_patch_similarity(left: np.ndarray, right: np.ndarray) -> float:
        nonlocal calls
        del left, right
        calls += 1
        return 1.0

    monkeypatch.setattr(dot_blot_module, "_patch_similarity", counted_patch_similarity)
    monkeypatch.setattr(dot_blot_module, "_profile_similarity", lambda *args: 0.0)

    assert dot_blot_module._best_match(first, second) is None
    assert calls == 0


def test_auto_dot_candidate_budget_is_global_and_per_page_pair() -> None:
    detector = DotBlotDuplicateDetector()
    regions = []
    for index in range(8):
        image = _patterned_dot_row((70, 155, 250, 345, 455, 550, 660, 755), width=830)
        image = cv2.convertScaleAbs(image, alpha=0.82 + index * 0.01, beta=20 - index)
        regions.extend(detector.extract_from_pages(f"page-{index}.png", (image,))[:2])
    candidates = dot_blot_module._candidate_pairs(regions)

    first = dot_blot_module._select_candidate_pairs(regions, candidates, 12, 2)
    second = dot_blot_module._select_candidate_pairs(regions, candidates, 12, 2)
    page_pair_counts = Counter(
        dot_blot_module._page_pair_key(regions[left], regions[right]) for left, right in first
    )

    assert len(candidates) > 12
    assert first == second
    assert len(first) <= 12
    assert max(page_pair_counts.values()) <= 2


def _candidate_budget_regions() -> list[dot_blot_module.DotBlotRegion]:
    image = _patterned_dot_row((70, 155, 250, 345, 455, 550, 660, 755), width=830)
    template = DotBlotDuplicateDetector().extract_from_pages("template.png", (image,))[0]
    return [
        replace(template, source_path="page-a.png", extraction_score=0.99),
        replace(template, source_path="page-a.png", extraction_score=0.98),
        replace(template, source_path="page-a.png", extraction_score=0.97),
        replace(template, source_path="page-b.png", extraction_score=0.99),
        replace(template, source_path="page-b.png", extraction_score=0.98),
        replace(template, source_path="page-b.png", extraction_score=0.97),
        replace(template, source_path="page-c.png", extraction_score=0.40),
        replace(template, source_path="page-d.png", extraction_score=0.30),
    ]


def test_auto_dot_candidate_budget_prioritizes_page_pair_coverage() -> None:
    regions = _candidate_budget_regions()
    candidates = {(0, 3), (1, 4), (2, 5), (0, 6), (0, 7)}

    selected = dot_blot_module._select_candidate_pairs(regions, candidates, 3, 3)
    page_pairs = {
        dot_blot_module._page_pair_key(regions[first], regions[second])
        for first, second in selected
    }

    assert selected == [(0, 3), (0, 6), (0, 7)]
    assert len(page_pairs) == 3


def test_auto_dot_candidate_budget_is_deterministic_when_smaller_than_page_pair_count() -> None:
    regions = _candidate_budget_regions()
    candidates = {(0, 3), (1, 4), (2, 5), (0, 6), (0, 7)}

    first = dot_blot_module._select_candidate_pairs(regions, candidates, 2, 3)
    second = dot_blot_module._select_candidate_pairs(
        regions, set(reversed(sorted(candidates))), 2, 3
    )

    assert first == second == [(0, 3), (0, 6)]
    assert (
        len(
            {dot_blot_module._page_pair_key(regions[left], regions[right]) for left, right in first}
        )
        == 2
    )


def test_dot_candidate_selection_without_budget_returns_every_pair_sorted() -> None:
    regions = _candidate_budget_regions()
    candidates = {(2, 5), (0, 7), (0, 3), (1, 4), (0, 6)}

    selected = dot_blot_module._select_candidate_pairs(regions, candidates, None, None)

    assert selected == sorted(candidates)


def test_dot_blot_rejects_dark_scratch_texture(tmp_path: Path) -> None:
    dot_path = tmp_path / "dot-row.png"
    scratch_path = tmp_path / "scratch.png"
    _write(dot_path, _patterned_dot_row((70, 160, 270, 390), width=470))
    scratch = np.zeros((220, 470, 3), dtype=np.uint8)
    random = np.random.default_rng(12)
    for _ in range(34):
        x = int(random.integers(0, scratch.shape[1]))
        cv2.line(
            scratch,
            (x, int(random.integers(0, scratch.shape[0]))),
            (int(random.integers(0, scratch.shape[1])), int(random.integers(0, scratch.shape[0]))),
            (int(random.integers(125, 255)),) * 3,
            1,
        )
    _write(scratch_path, scratch)

    detector = DotBlotDuplicateDetector()
    scratch_regions = detector.extract_from_pages(scratch_path, (scratch,))
    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.DOT_BLOT).scan(
        [dot_path, scratch_path]
    )

    assert scratch_regions == ()
    assert issues == []
    assert not any(item.rule_id == DOT_BLOT_RULE_ID for item in findings)


def test_dot_blot_rejects_same_layout_with_incompatible_spot_content(tmp_path: Path) -> None:
    centers = (80, 190, 315, 450)
    first = _patterned_dot_row(centers, width=540)
    second = np.full_like(first, 238)
    for index, center_x in enumerate(centers):
        center = (center_x, 90)
        cv2.rectangle(
            second,
            (center[0] - 24, center[1] - 19),
            (center[0] + 24, center[1] + 19),
            (35 + index * 24,) * 3,
            -1,
        )
        cv2.line(
            second,
            (center[0] - 18, center[1] - 13),
            (center[0] + 18, center[1] + 13),
            (225, 225, 225),
            8,
        )
    first_path = tmp_path / "source.png"
    second_path = tmp_path / "unrelated.png"
    _write(first_path, first)
    _write(second_path, second)

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.DOT_BLOT).scan(
        [first_path, second_path]
    )

    assert issues == []
    assert not any(item.rule_id == DOT_BLOT_RULE_ID for item in findings)


def test_dot_blot_rejects_generic_equal_spacing_without_distinctive_pattern(
    tmp_path: Path,
) -> None:
    random = np.random.default_rng(42)
    paths: list[Path] = []
    for image_index in range(2):
        image = np.full((180, 470, 3), 238, dtype=np.uint8)
        image = np.clip(
            image.astype(np.int16) + random.normal(0, 3, image.shape).astype(np.int16),
            0,
            255,
        ).astype(np.uint8)
        for center_x in (55, 160, 275, 400):
            value = int(random.integers(45, 175))
            radius = int(random.integers(14, 26))
            cv2.circle(image, (center_x, 90), radius, (value,) * 3, -1)
        path = tmp_path / f"generic-row-{image_index}.png"
        _write(path, image)
        paths.append(path)

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.DOT_BLOT).scan(paths)

    assert issues == []
    assert not any(item.rule_id == DOT_BLOT_RULE_ID for item in findings)


def test_dot_blot_local_manifest_evaluator_reports_pair_metrics(tmp_path: Path) -> None:
    source = _patterned_dot_row((70, 160, 270, 390), width=470)
    transformed = cv2.resize(source[35:145, 35:440], (520, 140), interpolation=cv2.INTER_CUBIC)
    unrelated = np.full_like(source, 238)
    cv2.putText(
        unrelated,
        "unrelated",
        (75, 100),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (40, 40, 40),
        2,
        cv2.LINE_AA,
    )
    source_path = tmp_path / "source.png"
    transformed_path = tmp_path / "transformed.png"
    unrelated_path = tmp_path / "unrelated.png"
    _write(source_path, source)
    _write(transformed_path, transformed)
    _write(unrelated_path, unrelated)
    manifest = {
        "schema_version": 1,
        "images": [
            {
                "id": "source",
                "path": source_path.name,
                "split": "test",
                "source_group": "synthetic-positive",
            },
            {
                "id": "transformed",
                "path": transformed_path.name,
                "split": "test",
                "source_group": "synthetic-positive",
            },
            {
                "id": "unrelated",
                "path": unrelated_path.name,
                "split": "test",
                "source_group": "synthetic-negative",
            },
        ],
        "pairs": [
            {"first": "source", "second": "transformed", "expected": "positive"},
            {"first": "source", "second": "unrelated", "expected": "negative"},
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = evaluate_dot_blot_manifest(manifest_path)

    assert result["metrics"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "tn": 1,
        "precision": 1.0,
        "recall": 1.0,
        "specificity": 1.0,
    }
    assert result["issues"] == []

    manifest["images"][2]["source_group"] = "synthetic-positive"
    manifest["images"][2]["split"] = "validation"
    manifest["pairs"] = manifest["pairs"][:1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="source_group"):
        evaluate_dot_blot_manifest(manifest_path)
