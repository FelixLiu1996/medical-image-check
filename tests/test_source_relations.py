from __future__ import annotations

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

from medical_image_check.evaluation.source_relation_review import build_review_package
from medical_image_check.evaluation.source_relations import (
    annotation_boxes,
    build_source_relation_draft,
    classify_statement,
    extract_figure_references,
    freeze_confirmed_source_relations,
    parse_article_image_contexts,
    select_claim_text,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_synthetic_batch(root: Path) -> Path:
    batch = root / "batch"
    case = batch / "eval-001"
    images = case / "wechat-images"
    images.mkdir(parents=True)
    image = np.full((150, 320, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 25), (130, 115), (0, 0, 255), thickness=4)
    cv2.rectangle(image, (180, 25), (295, 115), (0, 0, 255), thickness=4)
    assert cv2.imwrite(str(images / "001.png"), image)
    official_directory = batch / "official-papers" / "PMC0000001"
    official_directory.mkdir(parents=True)
    official_image = np.full((180, 360, 3), 245, dtype=np.uint8)
    cv2.rectangle(official_image, (25, 35), (145, 135), (80, 80, 80), thickness=-1)
    cv2.rectangle(official_image, (205, 35), (330, 135), (110, 110, 110), thickness=-1)
    assert cv2.imwrite(str(official_directory / "04-Figure-4.png"), official_image)

    write_json(
        batch / "ground-truth-sealed.json",
        {
            "dataset_id": "synthetic-relations",
            "cases": [
                {
                    "case_id": "eval-001",
                    "source_order": 1,
                    "expected": "positive",
                    "paper_keys": ["10.1000/source"],
                    "wechat_url": "https://mp.weixin.qq.com/s/example",
                }
            ],
        },
    )
    write_json(
        batch / "answer-acquisition-summary.json",
        {"cases": [{"case_id": "eval-001", "status": "complete", "title": "示例"}]},
    )
    write_json(
        batch / "source-reported-pair-candidates.json",
        {
            "cases": [
                {
                    "case_id": "eval-001",
                    "pairs": [
                        {
                            "pair_id": "eval-001-source-pair-001",
                            "source_image_relative": "eval-001/wechat-images/001.png",
                            "annotation_color": "red",
                            "candidate_valid_for_review": True,
                            "candidate_validation_issues": [],
                            "endpoints": [
                                {
                                    "paper_title": "论文 A",
                                    "doi": "10.1000/a",
                                    "figure": "Figure 4",
                                    "panel": "H",
                                    "official_region": [25, 35, 120, 100],
                                    "official_path": "official-papers/PMC0000001/04-Figure-4.png",
                                },
                                {
                                    "paper_title": "&lt;p&gt;论文 &lt;em&gt;B&lt;/em&gt;&lt;/p&gt;",
                                    "pmid": "0000001",
                                    "figure": "Figure 4",
                                    "panel": "I",
                                    "official_region": [205, 35, 125, 100],
                                    "official_path": "official-papers/PMC0000001/04-Figure-4.png",
                                },
                            ],
                        }
                    ],
                }
            ]
        },
    )
    (batch / "source-image-ocr.jsonl").write_text(
        json.dumps(
            {
                "source_image_relative": "eval-001/wechat-images/001.png",
                "width": 320,
                "height": 150,
                "lines": [{"text": "Figure 4H and 5E"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        case / "article.json",
        {
            "title": "示例",
            "image_downloads": [
                {
                    "order": 1,
                    "status": "downloaded",
                    "filename": "001.png",
                    "url": "https://mmbiz.qpic.cn/example?a=1#fragment",
                    "sha256": "synthetic",
                }
            ],
        },
    )
    (case / "article-body.html").write_text(
        "<p>图4H、5E中的两个区域存在重复，DOI: 10.1000/EXAMPLE。</p>"
        '<p>📷 相关图片：</p><img data-src="https://mmbiz.qpic.cn/example?a=1">',
        encoding="utf-8",
    )
    return batch


def test_article_image_context_uses_preceding_relation_claim() -> None:
    document = (
        "<p>图3A和图7B存在重叠。</p><p>📷 相关图片：</p>"
        '<img data-src="https://example.test/a.png?x=1#fragment">'
    )
    contexts = parse_article_image_contexts(document)
    assert len(contexts) == 1
    assert contexts[0].url == "https://example.test/a.png?x=1"
    assert select_claim_text(contexts[0].preceding_blocks) == "图3A和图7B存在重叠。"


def test_figure_reference_shorthand_ignores_measurement_ratio() -> None:
    references = extract_figure_references("图4H、5E和5J重复；图2B展示 P-S6 235/236 条带。")
    assert [(item.figure, item.panel) for item in references] == [
        ("4", "H"),
        ("5", "E"),
        ("5", "J"),
        ("2", "B"),
    ]


def test_rebuttal_is_not_silently_promoted() -> None:
    assert classify_statement("两个面板相似，但它们并非完全相同。") == (
        "rebuttal",
        "disputed_relation",
    )


def test_annotation_boxes_forms_one_same_color_pair() -> None:
    image = np.full((150, 320, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 25), (130, 115), (0, 0, 255), thickness=4)
    cv2.rectangle(image, (180, 25), (295, 115), (0, 0, 255), thickness=4)
    boxes = annotation_boxes(image)
    assert len(boxes["red"]) == 2


def test_build_draft_and_review_package_exclude_algorithm_findings(tmp_path: Path) -> None:
    batch = make_synthetic_batch(tmp_path)
    draft = build_source_relation_draft(batch)
    assert draft["case_count"] == 1
    assert draft["statement_count"] == 1
    assert draft["source_box_pair_count"] == 1
    assert draft["algorithm_findings_included"] is False
    assert draft["cases"][0]["statements"][0]["dois"] == ["10.1000/example"]
    assert draft["cases"][0]["statements"][0]["figure_references"] == [
        {"figure": "4", "panel": "H", "subpanel": ""},
        {"figure": "5", "panel": "E", "subpanel": ""},
    ]

    output = tmp_path / "review"
    archive = tmp_path / "review.zip"
    result = build_review_package(batch, output, archive)
    assert result["source_box_pair_count"] == 1
    assert result["review_task_count"] == 1
    assert result["system_backlog_pair_count"] == 0
    assert result["asset_failure_count"] == 0
    assert (output / "index.html").is_file()
    assert len(list((output / "assets" / "source-crops").glob("*.png"))) == 2
    assert len(list((output / "assets" / "official-crops").glob("*.jpg"))) == 2
    assert len(list((output / "assets" / "official-overviews").glob("*.jpg"))) == 2
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "不包含算法查重结果" in html
    assert "你只需要做一件事" in html
    assert "不需要重新判断图片是否重复" in html
    assert "公众号原文关系判断" not in html
    payload = json.loads((output / "source-relation-draft.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["review_scope"] == "verify_prepared_source_to_official_ab_mappings_only"
    assert payload["review_tasks"][0]["official_endpoints"][1]["paper_title"] == "论文 B"
    with zipfile.ZipFile(archive) as bundle:
        assert f"{output.name}/index.html" in bundle.namelist()


def test_freeze_confirmed_source_relations_promotes_only_correct_mappings(
    tmp_path: Path,
) -> None:
    batch = make_synthetic_batch(tmp_path)
    feedback_path = tmp_path / "feedback.json"
    output_path = tmp_path / "confirmed.json"
    write_json(
        feedback_path,
        {
            "schema_version": 2,
            "artifact_kind": "source_relation_doctor_feedback",
            "dataset_id": "synthetic-relations",
            "review_scope": "verify_prepared_source_to_official_ab_mappings_only",
            "task_ids": ["eval-001-source-001-red"],
            "task_reviews": [
                {
                    "task_id": "eval-001-source-001-red",
                    "decision": "correct",
                    "note": "",
                }
            ],
        },
    )

    manifest = freeze_confirmed_source_relations(batch, feedback_path, output_path)

    assert manifest["confirmed_subset_frozen"] is True
    assert manifest["ground_truth_frozen"] is False
    assert manifest["confirmed_relation_count"] == 1
    assert manifest["confirmed_case_count"] == 1
    assert manifest["decision_counts"] == {"correct": 1}
    assert manifest["cases"][0]["pairs"][0]["pair_id"] == "eval-001-source-001-red"
    assert manifest["cases"][0]["pairs"][0]["source_candidate_pair_id"] == (
        "eval-001-source-pair-001"
    )
    assert manifest["cases"][0]["pairs"][0]["ground_truth_eligible"] is True
    assert output_path.is_file()


def test_freeze_confirmed_source_relations_rejects_incomplete_feedback(tmp_path: Path) -> None:
    batch = make_synthetic_batch(tmp_path)
    feedback_path = tmp_path / "feedback.json"
    write_json(
        feedback_path,
        {
            "schema_version": 2,
            "artifact_kind": "source_relation_doctor_feedback",
            "dataset_id": "synthetic-relations",
            "review_scope": "verify_prepared_source_to_official_ab_mappings_only",
            "task_ids": [],
            "task_reviews": [],
        },
    )

    try:
        freeze_confirmed_source_relations(batch, feedback_path)
    except ValueError as exc:
        assert "任务不完整" in str(exc)
    else:
        raise AssertionError("incomplete feedback must be rejected")
