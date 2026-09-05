from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PIL import Image

from medical_image_check.evaluation.negative_review import (
    build_negative_review_package,
    select_negative_candidates,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_batch(root: Path) -> Path:
    batch = root / "batch"
    case = batch / "eval-002"
    figures = batch / "official-papers" / "PMC2"
    figures.mkdir(parents=True)
    for index in (1, 2):
        Image.new("RGB", (240, 160), (230 - index * 10, 220, 210)).save(
            figures / f"0{index}-Figure-{index}.png"
        )
    first = figures / "01-Figure-1.png"
    second = figures / "02-Figure-2.png"
    write_json(
        batch / "ground-truth-sealed.json",
        {
            "dataset_id": "negative-test",
            "cases": [
                {"case_id": "eval-001", "expected": "positive"},
                {"case_id": "eval-002", "expected": "negative"},
            ],
        },
    )
    finding = {
        "finding_id": "finding-1",
        "rule_id": "image.local.geometric",
        "finding_type": "suspected_reuse",
        "risk": "medium",
        "confidence": 0.82,
        "title": "局部相似",
        "description": "测试候选",
        "locations": [
            {"source_path": str(first), "coordinate": "A"},
            {"source_path": str(second), "coordinate": "B"},
        ],
        "details": {
            "first_region_x": 20,
            "first_region_y": 30,
            "first_region_width": 80,
            "first_region_height": 50,
            "second_region_x": 40,
            "second_region_y": 45,
            "second_region_width": 70,
            "second_region_height": 45,
        },
    }
    write_json(
        batch / "blind-algorithm-findings-summary.json",
        {
            "schema_version": 1,
            "dataset_id": "negative-test",
            "algorithm_version": "algorithm-test",
            "cases": [
                {
                    "case_id": "eval-002",
                    "runs": [
                        {
                            "configuration": "panel-split-auto",
                            "status": "complete",
                            "scan_input_count": 2,
                            "findings": [finding],
                        }
                    ],
                }
            ],
        },
    )
    write_json(
        case / "official-assets.json",
        {
            "schema_version": 3,
            "case_id": "eval-002",
            "paper_assets": [
                {
                    "title": "Test paper",
                    "doi": "10.1000/test",
                    "pmcid": "PMC2",
                    "article_url": "https://example.test/paper",
                    "figures": [
                        {
                            "status": "downloaded",
                            "relative_path": str(first),
                            "redistributable": True,
                            "sha256": "a" * 64,
                            "label": "Figure 1",
                            "caption": "First",
                            "source_url": "https://example.test/figure-1",
                        },
                        {
                            "status": "downloaded",
                            "relative_path": str(second),
                            "redistributable": True,
                            "sha256": "b" * 64,
                            "label": "Figure 2",
                            "caption": "Second",
                            "source_url": "https://example.test/figure-2",
                        },
                    ],
                }
            ],
        },
    )
    return batch


def test_select_negative_candidates_uses_only_negative_redistributable_findings(
    tmp_path: Path,
) -> None:
    batch = make_batch(tmp_path)

    result = select_negative_candidates(
        batch,
        sample_size=1,
        configuration="panel-split-auto",
        seed="test-seed",
    )

    assert result["sampling"]["all_evaluable_negative_case_count"] == 1
    assert result["sampling"]["all_negative_candidate_count"] == 1
    assert result["sampling"]["redistributable_candidate_count"] == 1
    assert result["sampling"]["selected_candidate_count"] == 1
    assert result["selected_candidates"][0]["case_id"] == "eval-002"


def test_build_negative_review_package_is_simple_and_self_contained(tmp_path: Path) -> None:
    batch = make_batch(tmp_path)
    output = tmp_path / "review"
    archive = tmp_path / "review.zip"

    result = build_negative_review_package(
        batch,
        output,
        archive,
        sample_size=1,
        seed="test-seed",
    )

    assert result["review_task_count"] == 1
    assert result["review_case_count"] == 1
    assert result["zip_sha256"]
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "你只需要判断：A 和 B 是否真的重复" in html
    assert "正确检出（确有重复）" in html
    assert "误报（并不重复）" in html
    assert "不能单独作为最终 Precision/F1" in html
    payload = json.loads((output / "review-data.json").read_text(encoding="utf-8"))
    assert payload["review_scope"] == "classify_sampled_negative_algorithm_candidates_only"
    assert len(payload["review_tasks"][0]["locations"]) == 2
    assert len(list((output / "assets" / "evidence").glob("*.jpg"))) == 4
    with zipfile.ZipFile(archive) as bundle:
        assert f"{output.name}/index.html" in bundle.namelist()
        assert f"{output.name}/manifest.json" in bundle.namelist()
