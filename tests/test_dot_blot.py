from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.engines.dot_blot import DOT_BLOT_RULE_ID, DotBlotDuplicateDetector
from medical_image_check.engines.image_similarity import ImageDuplicateDetector
from medical_image_check.evaluation.dot_blot import evaluate_dot_blot_manifest


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
