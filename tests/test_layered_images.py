from __future__ import annotations

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from medical_image_check.evaluation import layered_images
from medical_image_check.evaluation.layered_images import (
    _predict_panel_modality,
    evaluate_layered_image_manifest,
)
from medical_image_check.evaluation.panel_type_review import (
    build_panel_type_review_package,
    freeze_panel_type_feedback,
    normalize_panel_type_feedback,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_annotation_batch(root: Path) -> tuple[Path, Path, Path]:
    batch = root / "batch"
    figure_directory = batch / "official-papers" / "PMC-LAYERED"
    figure_directory.mkdir(parents=True)
    figure = np.full((180, 360, 3), 255, dtype=np.uint8)
    cv2.rectangle(figure, (20, 30), (150, 145), (60, 60, 60), -1)
    cv2.rectangle(figure, (205, 30), (340, 145), (90, 90, 90), -1)
    figure_path = figure_directory / "01-Figure-1.png"
    assert cv2.imwrite(str(figure_path), figure)

    _write_json(
        batch / "eval-001" / "official-assets.json",
        {
            "paper_assets": [
                {
                    "figures": [
                        {
                            "relative_path": str(figure_path),
                            "redistributable": False,
                            "source_url": "https://example.test/official-figure.png",
                            "article_url": "https://example.test/article",
                            "reuse_scope": "local-evaluation-only",
                            "provider": "Example publisher",
                        }
                    ]
                }
            ]
        },
    )
    relations_path = root / "confirmed.json"
    _write_json(
        relations_path,
        {
            "schema_version": 1,
            "dataset_id": "layered-test",
            "confirmed_subset_frozen": True,
            "cases": [
                {
                    "case_id": "eval-001",
                    "pairs": [
                        {
                            "pair_id": "pair-001",
                            "ground_truth_eligible": True,
                            "endpoints": [
                                {
                                    "official_path": str(figure_path),
                                    "official_region": [50, 60, 40, 30],
                                    "pmcid": "PMC-LAYERED",
                                    "figure": "Figure 1",
                                    "panel": "A",
                                },
                                {
                                    "official_path": str(figure_path),
                                    "official_region": [250, 60, 40, 30],
                                    "pmcid": "PMC-LAYERED",
                                    "figure": "Figure 1",
                                    "panel": "B",
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    )
    return batch, relations_path, figure_path


def test_panel_type_routing_treats_blank_panel_as_generic() -> None:
    modality, accepted = _predict_panel_modality(
        Path("plain-panel.png"), np.full((120, 180, 3), 255, dtype=np.uint8)
    )

    assert modality == "generic"
    assert accepted == ()


def test_panel_type_review_freezes_target_panels_without_copying_assets(tmp_path: Path) -> None:
    batch, relations_path, _ = _make_annotation_batch(tmp_path)
    output = batch / "panel-type-review"

    result = build_panel_type_review_package(batch, relations_path, output)

    assert result["task_count"] == 1
    assert result["target_count"] == 2
    assert result["relation_count"] == 1
    assert result["restricted_task_count"] == 1
    assert list(output.glob("*.png")) == []
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "红框只是已确认重复内容的位置，不是 Panel 标准边界" in html
    assert "蓝色算法框默认隐藏" in html
    assert "不能把本目录单独压缩或对外发送" in html

    review_data = json.loads((output / "review-data.json").read_text(encoding="utf-8"))
    task_id = review_data["task_ids"][0]
    feedback_path = tmp_path / "feedback.json"
    _write_json(
        feedback_path,
        {
            "schema_version": 1,
            "artifact_kind": "panel_type_annotation_feedback",
            "dataset_id": "layered-test",
            "task_ids": [task_id],
            "task_reviews": [
                {
                    "task_id": task_id,
                    "status": "complete",
                    "annotation_scope": "complete",
                    "panels": [
                        {"region": [20, 30, 131, 116], "modality": "generic"},
                        {"region": [205, 30, 136, 116], "modality": "generic"},
                    ],
                    "note": "",
                }
            ],
        },
    )
    ground_truth_path = tmp_path / "layered-ground-truth.json"

    frozen = freeze_panel_type_feedback(
        batch,
        output / "review-data.json",
        feedback_path,
        ground_truth_path,
    )

    assert frozen["image_count"] == 1
    assert frozen["panel_count"] == 2
    assert frozen["resolved_relation_count"] == 1
    assert frozen["unresolved_relation_count"] == 0
    assert frozen["relations"][0]["endpoints"][0]["panel_id"].endswith("panel-001")
    assert frozen["relations"][0]["endpoints"][1]["panel_id"].endswith("panel-002")


def test_panel_type_review_can_preload_a_non_frozen_draft(tmp_path: Path) -> None:
    batch, relations_path, _ = _make_annotation_batch(tmp_path)
    first_output = batch / "panel-type-review-seed"
    build_panel_type_review_package(batch, relations_path, first_output)
    review_data = json.loads((first_output / "review-data.json").read_text(encoding="utf-8"))
    task_id = review_data["task_ids"][0]
    draft_path = tmp_path / "draft.json"
    _write_json(
        draft_path,
        {
            "schema_version": 1,
            "artifact_kind": "panel_type_annotation_feedback",
            "dataset_id": "layered-test",
            "task_ids": [task_id],
            "task_reviews": [
                {
                    "task_id": task_id,
                    "status": "complete",
                    "annotation_scope": "targeted",
                    "panels": [
                        {"region": [20, 30, 131, 116], "modality": "generic"},
                        {"region": [205, 30, 136, 116], "modality": "generic"},
                    ],
                    "note": "机器辅助预标，待人工确认",
                }
            ],
        },
    )
    output = batch / "panel-type-review-prefilled"

    result = build_panel_type_review_package(
        batch, relations_path, output, initial_feedback=draft_path
    )

    assert result["initial_feedback_sha256"]
    package = json.loads((output / "review-data.json").read_text(encoding="utf-8"))
    assert package["initial_feedback_sha256"] == result["initial_feedback_sha256"]
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "机器辅助预标，待人工确认" in html
    assert "绿色框若由预标草稿带入，只是待人工确认的起点" in html


def test_remote_panel_type_review_links_restricted_assets_without_copying_them(
    tmp_path: Path,
) -> None:
    batch, relations_path, figure_path = _make_annotation_batch(tmp_path)
    output = tmp_path / "remote-review"
    archive = tmp_path / "remote-review.zip"

    result = build_panel_type_review_package(
        batch,
        relations_path,
        output,
        distribution_mode="remote",
        zip_path=archive,
    )

    assert result["restricted_task_count"] == 1
    assert result["redistributable_task_count"] == 0
    assert result["zip_sha256"]
    assert not (output / "assets").exists()
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "受限图片，需联网读取官方来源" in html
    assert "https://example.test/official-figure.png" in html
    assert str(figure_path) not in html
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
    assert names == ["ASSET_SOURCES.txt", "README.txt", "index.html", "review-data.json"]


def test_remote_panel_type_review_copies_only_redistributable_assets(tmp_path: Path) -> None:
    batch, relations_path, figure_path = _make_annotation_batch(tmp_path)
    catalog_path = batch / "eval-001" / "official-assets.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    figure = catalog["paper_assets"][0]["figures"][0]
    figure["redistributable"] = True
    figure["reuse_scope"] = "CC BY 4.0"
    figure["license_url"] = "https://creativecommons.org/licenses/by/4.0/"
    _write_json(catalog_path, catalog)
    output = tmp_path / "remote-review"
    archive = tmp_path / "remote-review.zip"

    result = build_panel_type_review_package(
        batch,
        relations_path,
        output,
        distribution_mode="remote",
        zip_path=archive,
    )

    assert result["redistributable_task_count"] == 1
    packaged_images = list((output / "assets").iterdir())
    assert len(packaged_images) == 1
    assert packaged_images[0].read_bytes() == figure_path.read_bytes()
    review_data = json.loads((output / "review-data.json").read_text(encoding="utf-8"))
    assert review_data["tasks"][0]["asset_delivery"] == "included_in_package"
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
    assert any(name.startswith("assets/panel-type-001-") for name in names)


def test_panel_type_feedback_normalization_clamps_and_audits_changes(tmp_path: Path) -> None:
    batch, relations_path, _ = _make_annotation_batch(tmp_path)
    review_directory = batch / "panel-type-review"
    build_panel_type_review_package(batch, relations_path, review_directory)
    package = json.loads((review_directory / "review-data.json").read_text(encoding="utf-8"))
    task_id = package["task_ids"][0]
    feedback_path = tmp_path / "raw-feedback.json"
    _write_json(
        feedback_path,
        {
            "schema_version": 1,
            "artifact_kind": "panel_type_annotation_feedback",
            "dataset_id": "layered-test",
            "task_ids": [task_id],
            "task_reviews": [
                {
                    "task_id": task_id,
                    "status": "complete",
                    "annotation_scope": "targeted",
                    "panels": [
                        {"region": [-2, 30, 153, 116], "modality": "generic"},
                        {"region": [205, 30, 157, 152], "modality": "generic"},
                    ],
                    "note": "人工补充确认完整",
                }
            ],
        },
    )
    output = tmp_path / "normalized-feedback.json"

    normalized = normalize_panel_type_feedback(
        batch,
        review_directory / "review-data.json",
        feedback_path,
        output,
        complete_task_ids=(task_id,),
        drop_panels=((task_id, 2),),
    )

    review = normalized["task_reviews"][0]
    assert review["annotation_scope"] == "complete"
    assert review["panels"] == [{"region": [0, 30, 151, 116], "modality": "generic"}]
    audit = normalized["normalization"]
    assert audit["clamped_panel_count"] == 1
    assert audit["removed_panel_count"] == 1
    assert audit["scope_override_count"] == 1
    assert output.is_file()


def test_layered_evaluator_separates_complete_precision_and_type_routing(
    tmp_path: Path, monkeypatch
) -> None:
    image = np.full((180, 360, 3), 255, dtype=np.uint8)
    image_path = tmp_path / "figure.png"
    assert cv2.imwrite(str(image_path), image)
    manifest_path = tmp_path / "manifest.json"
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "artifact_kind": "layered_image_ground_truth",
            "dataset_id": "layered-evaluation",
            "images": [
                {
                    "id": "image-001",
                    "path": "figure.png",
                    "page": 1,
                    "split": "validation",
                    "source_group": "paper-001",
                    "annotation_scope": "complete",
                    "panels": [
                        {
                            "id": "panel-a",
                            "region": [20, 30, 130, 115],
                            "modality": "generic",
                            "target_ids": [],
                        },
                        {
                            "id": "panel-b",
                            "region": [205, 30, 135, 115],
                            "modality": "generic",
                            "target_ids": [],
                        },
                    ],
                }
            ],
            "relations": [
                {
                    "id": "relation-001",
                    "expected": "positive",
                    "endpoints": [
                        {"image_id": "image-001", "panel_id": "panel-a"},
                        {"image_id": "image-001", "panel_id": "panel-b"},
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(
        layered_images,
        "detect_panel_regions",
        lambda _: ((20, 30, 130, 115), (205, 30, 135, 115)),
    )
    monkeypatch.setattr(
        layered_images,
        "_predict_panel_modality",
        lambda _source, _crop: ("generic", ()),
    )
    monkeypatch.setattr(layered_images, "_evaluate_cropped_pair", lambda *_args: True)

    result = evaluate_layered_image_manifest(manifest_path)

    split = result["panel_splitting"]["complete_annotations"]
    assert split["iou_0_50"]["precision"] == 1.0
    assert split["iou_0_50"]["recall"] == 1.0
    assert split["iou_0_75"]["f1"] == 1.0
    assert result["type_routing"]["exact_accuracy"] == 1.0
    assert result["type_routing"]["confusion"] == {"generic": {"generic": 2}}
    assert result["relation_matching"]["ground_truth_panel_crops"]["recall"] == 1.0
    assert result["relation_matching"]["automatic_splitter_crops_at_iou_0_50"]["recall"] == 1.0


def test_layered_evaluator_does_not_count_targeted_unmatched_predictions_as_fp(
    tmp_path: Path, monkeypatch
) -> None:
    image_path = tmp_path / "targeted.png"
    assert cv2.imwrite(str(image_path), np.full((120, 240, 3), 255, dtype=np.uint8))
    manifest_path = tmp_path / "targeted-manifest.json"
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "artifact_kind": "layered_image_ground_truth",
            "dataset_id": "targeted-only",
            "images": [
                {
                    "id": "targeted-image",
                    "path": str(image_path),
                    "page": 1,
                    "split": "validation",
                    "source_group": "paper-targeted",
                    "annotation_scope": "targeted",
                    "panels": [
                        {
                            "id": "target-panel",
                            "region": [10, 10, 80, 80],
                            "modality": "generic",
                        }
                    ],
                }
            ],
            "relations": [],
        },
    )
    monkeypatch.setattr(
        layered_images,
        "detect_panel_regions",
        lambda _: ((10, 10, 80, 80), (130, 10, 80, 80)),
    )
    monkeypatch.setattr(
        layered_images,
        "_predict_panel_modality",
        lambda _source, _crop: ("generic", ()),
    )

    result = evaluate_layered_image_manifest(manifest_path)

    complete = result["panel_splitting"]["complete_annotations"]["iou_0_50"]
    targeted = result["panel_splitting"]["all_annotated_panel_recall"]["iou_0_50"]
    assert complete["precision"] is None
    assert complete["fp"] == 0
    assert targeted == {"tp": 1, "fn": 0, "recall": 1.0}


def test_layered_evaluator_rejects_source_group_split_leakage(tmp_path: Path) -> None:
    image_path = tmp_path / "same-source.png"
    assert cv2.imwrite(str(image_path), np.full((80, 100, 3), 255, dtype=np.uint8))
    manifest_path = tmp_path / "leaked-manifest.json"
    images = []
    for index, split in enumerate(("validation", "test"), start=1):
        images.append(
            {
                "id": f"image-{index}",
                "path": str(image_path),
                "page": 1,
                "split": split,
                "source_group": "same-paper",
                "annotation_scope": "targeted",
                "panels": [
                    {
                        "id": f"panel-{index}",
                        "region": [5, 5, 50, 50],
                        "modality": "generic",
                    }
                ],
            }
        )
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "artifact_kind": "layered_image_ground_truth",
            "dataset_id": "leakage-test",
            "images": images,
            "relations": [],
        },
    )

    with pytest.raises(ValueError, match="source_group"):
        evaluate_layered_image_manifest(manifest_path)
