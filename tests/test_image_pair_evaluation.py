from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from medical_image_check.evaluation.image_pairs import evaluate_image_pair_manifest


def _write_image(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image)


def test_pair_evaluator_supports_whole_images_and_same_image_regions(tmp_path: Path) -> None:
    source = np.full((180, 360, 3), 245, dtype=np.uint8)
    cv2.circle(source, (70, 80), 28, (20, 20, 20), -1)
    cv2.line(source, (25, 130), (120, 105), (60, 60, 60), 5)
    source[:, 180:] = source[:, :180]
    different = np.full((180, 180, 3), 245, dtype=np.uint8)
    cv2.rectangle(different, (35, 45), (145, 135), (80, 80, 80), -1)

    source_path = tmp_path / "source.png"
    copy_path = tmp_path / "copy.png"
    different_path = tmp_path / "different.png"
    _write_image(source_path, source)
    _write_image(copy_path, source.copy())
    _write_image(different_path, different)
    manifest = {
        "schema_version": 1,
        "images": [
            {
                "id": "source",
                "path": source_path.name,
                "split": "validation",
                "source_group": "synthetic-source",
            },
            {
                "id": "copy",
                "path": copy_path.name,
                "split": "validation",
                "source_group": "synthetic-source",
            },
            {
                "id": "different",
                "path": different_path.name,
                "split": "validation",
                "source_group": "synthetic-negative",
            },
        ],
        "pairs": [
            {
                "id": "whole-positive",
                "first": "source",
                "second": "copy",
                "expected": "positive",
                "modality": "generic",
            },
            {
                "id": "region-positive",
                "first": "source",
                "second": "source",
                "first_region": [0, 0, 180, 180],
                "second_region": [180, 0, 180, 180],
                "expected": "positive",
                "modality": "generic",
                "reuse_scope": "partial_region",
            },
            {
                "id": "negative",
                "first": "source",
                "second": "different",
                "first_region": [0, 0, 180, 180],
                "expected": "negative",
                "modality": "generic",
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = evaluate_image_pair_manifest(manifest_path)

    assert result["metrics"] == {
        "tp": 2,
        "fp": 0,
        "fn": 0,
        "tn": 1,
        "precision": 1.0,
        "recall": 1.0,
        "specificity": 1.0,
    }
    assert [pair["outcome"] for pair in result["pairs"]] == ["tp", "tp", "tn"]


def test_pair_evaluator_rejects_uncertain_labels_and_split_leakage(tmp_path: Path) -> None:
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    _write_image(first_path, image)
    _write_image(second_path, image)
    manifest = {
        "schema_version": 1,
        "images": [
            {
                "id": "first",
                "path": first_path.name,
                "split": "train",
                "source_group": "same-paper",
            },
            {
                "id": "second",
                "path": second_path.name,
                "split": "test",
                "source_group": "same-paper",
            },
        ],
        "pairs": [
            {
                "id": "uncertain",
                "first": "first",
                "second": "second",
                "expected": "uncertain",
                "modality": "generic",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        evaluate_image_pair_manifest(manifest_path)
