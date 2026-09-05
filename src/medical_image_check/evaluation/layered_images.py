from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import cv2
import numpy as np

from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.models import FindingType
from medical_image_check.engines.dot_blot import DotBlotDuplicateDetector
from medical_image_check.engines.fluorescence import FluorescenceDuplicateDetector
from medical_image_check.engines.image_similarity import (
    ImageDuplicateDetector,
    _looks_like_western_input,
    _western_dominant_pages,
)
from medical_image_check.engines.pathology import PathologyDuplicateDetector
from medical_image_check.engines.western_blot import WesternBlotDuplicateDetector
from medical_image_check.infrastructure.images import canonical_pixels, decode_image_pages
from medical_image_check.services.panel_splitting import detect_panel_regions

ALLOWED_SPLITS = {"train", "validation", "test"}
ALLOWED_MODALITIES = {
    "generic",
    "western_blot",
    "dot_blot",
    "fluorescence",
    "pathology",
}
ALLOWED_SCOPES = {"complete", "targeted"}
IOU_THRESHOLDS = (0.5, 0.75)
POSITIVE_FINDING_TYPES = {
    FindingType.EXACT_DUPLICATE,
    FindingType.SUSPECTED_REUSE,
    FindingType.HIGH_SIMILARITY,
}


def evaluate_layered_image_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    images = _load_images(payload, manifest_file.parent)
    _validate_source_group_splits(images)
    relations = _load_relations(payload, images)

    image_results: list[dict[str, Any]] = []
    complete_counts = {threshold: defaultdict(int) for threshold in IOU_THRESHOLDS}
    all_counts = {threshold: defaultdict(int) for threshold in IOU_THRESHOLDS}
    best_ious: list[float] = []
    type_counts: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    type_confusion: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    exact_type_matches = 0
    panel_count = 0
    runtime_by_id: dict[str, dict[str, Any]] = {}

    for image in images:
        pixels = _load_page(image)
        predicted_boxes = list(detect_panel_regions(pixels))
        expected_boxes = [tuple(panel["region"]) for panel in image["panels"]]
        matches_by_threshold = {
            threshold: _maximum_box_matching(expected_boxes, predicted_boxes, threshold)
            for threshold in IOU_THRESHOLDS
        }
        for expected in expected_boxes:
            best_ious.append(max((_box_iou(expected, box) for box in predicted_boxes), default=0.0))
        for threshold, matches in matches_by_threshold.items():
            all_counts[threshold]["tp"] += len(matches)
            all_counts[threshold]["fn"] += len(expected_boxes) - len(matches)
            if image["annotation_scope"] == "complete":
                complete_counts[threshold]["tp"] += len(matches)
                complete_counts[threshold]["fn"] += len(expected_boxes) - len(matches)
                complete_counts[threshold]["fp"] += len(predicted_boxes) - len(matches)

        panel_results: list[dict[str, Any]] = []
        for panel in image["panels"]:
            panel_count += 1
            x, y, width, height = panel["region"]
            crop = pixels[y : y + height, x : x + width]
            predicted_modality, accepted_modalities = _predict_panel_modality(image["path"], crop)
            expected_modality = panel["modality"]
            exact_type_matches += int(predicted_modality == expected_modality)
            type_confusion[expected_modality][predicted_modality] += 1
            for modality in ALLOWED_MODALITIES:
                expected_positive = expected_modality == modality
                predicted_positive = predicted_modality == modality
                type_counts[modality][_outcome(expected_positive, predicted_positive)] += 1
            panel_results.append(
                {
                    **panel,
                    "predicted_modality": predicted_modality,
                    "accepted_specialist_modalities": list(accepted_modalities),
                    "best_split_iou": max(
                        (_box_iou(tuple(panel["region"]), box) for box in predicted_boxes),
                        default=0.0,
                    ),
                }
            )

        image_results.append(
            {
                "id": image["id"],
                "split": image["split"],
                "source_group": image["source_group"],
                "annotation_scope": image["annotation_scope"],
                "expected_panel_count": len(expected_boxes),
                "predicted_panel_count": len(predicted_boxes),
                "predicted_panels": [list(box) for box in predicted_boxes],
                "matches": {
                    _threshold_key(threshold): [
                        {
                            "expected_panel_id": image["panels"][expected_index]["id"],
                            "predicted_index": predicted_index + 1,
                            "iou": _box_iou(
                                expected_boxes[expected_index], predicted_boxes[predicted_index]
                            ),
                        }
                        for expected_index, predicted_index in matches_by_threshold[threshold]
                    ]
                    for threshold in IOU_THRESHOLDS
                },
                "panels": panel_results,
            }
        )
        runtime_by_id[image["id"]] = {
            "image": image,
            "pixels": pixels,
            "predicted_boxes": predicted_boxes,
            "panels_by_id": {panel["id"]: panel for panel in image["panels"]},
        }

    per_modality = {
        modality: _binary_metrics(counts) for modality, counts in sorted(type_counts.items())
    }
    macro_values = [metrics["f1"] for metrics in per_modality.values() if metrics["f1"] is not None]
    micro_counts: defaultdict[str, int] = defaultdict(int)
    for counts in type_counts.values():
        for key, value in counts.items():
            micro_counts[key] += value

    relation_results: list[dict[str, Any]] = []
    ground_truth_crop_counts: defaultdict[str, int] = defaultdict(int)
    splitter_crop_counts: defaultdict[str, int] = defaultdict(int)
    for relation in relations:
        first = _relation_endpoint(runtime_by_id, relation["endpoints"][0])
        second = _relation_endpoint(runtime_by_id, relation["endpoints"][1])
        expected_positive = relation["expected"] == "positive"
        gt_positive = _evaluate_cropped_pair(
            first["pixels"],
            first["panel"]["region"],
            second["pixels"],
            second["panel"]["region"],
            relation["western_single_band_enabled"],
        )
        ground_truth_crop_counts[_outcome(expected_positive, gt_positive)] += 1

        first_predicted, first_iou = _best_box_for_panel(
            first["panel"]["region"], first["predicted_boxes"]
        )
        second_predicted, second_iou = _best_box_for_panel(
            second["panel"]["region"], second["predicted_boxes"]
        )
        splitter_positive = False
        if first_predicted is not None and second_predicted is not None:
            splitter_positive = _evaluate_cropped_pair(
                first["pixels"],
                first_predicted,
                second["pixels"],
                second_predicted,
                relation["western_single_band_enabled"],
            )
        splitter_crop_counts[_outcome(expected_positive, splitter_positive)] += 1
        relation_results.append(
            {
                **relation,
                "ground_truth_crop_prediction": "positive" if gt_positive else "negative",
                "splitter_crop_prediction": ("positive" if splitter_positive else "negative"),
                "first_split_iou": first_iou,
                "second_split_iou": second_iou,
                "splitter_endpoints_available": (
                    first_predicted is not None and second_predicted is not None
                ),
            }
        )

    return {
        "schema_version": 1,
        "artifact_kind": "layered_image_evaluation",
        "manifest": str(manifest_file),
        "dataset_id": payload["dataset_id"],
        "image_count": len(images),
        "complete_image_count": sum(image["annotation_scope"] == "complete" for image in images),
        "targeted_image_count": sum(image["annotation_scope"] == "targeted" for image in images),
        "annotated_panel_count": panel_count,
        "panel_splitting": {
            "complete_annotations": {
                _threshold_key(threshold): _binary_metrics(counts)
                for threshold, counts in complete_counts.items()
            },
            "all_annotated_panel_recall": {
                _threshold_key(threshold): _recall_metrics(counts)
                for threshold, counts in all_counts.items()
            },
            "mean_best_iou": sum(best_ious) / len(best_ious) if best_ious else None,
            "precision_scope_note": (
                "Precision is computed only for images marked complete; unmatched predictions "
                "on targeted-only images are not false positives."
            ),
        },
        "type_routing": {
            "panel_count": panel_count,
            "exact_accuracy": exact_type_matches / panel_count if panel_count else None,
            "micro": _binary_metrics(micro_counts),
            "macro_f1": sum(macro_values) / len(macro_values) if macro_values else None,
            "per_modality": per_modality,
            "confusion": {
                expected: dict(sorted(predicted.items()))
                for expected, predicted in sorted(type_confusion.items())
            },
            "metric_note": (
                "Routing is evaluated on human Panel crops so it is isolated from splitter errors."
            ),
        },
        "relation_matching": {
            "ground_truth_panel_crops": _binary_metrics(ground_truth_crop_counts),
            "automatic_splitter_crops_at_iou_0_50": _binary_metrics(splitter_crop_counts),
            "metric_note": (
                "The first metric isolates duplicate matching on human Panel crops; the second "
                "requires both automatic Panel endpoints to reach IoU 0.50 before matching."
            ),
        },
        "images": image_results,
        "relations": relation_results,
    }


def _load_images(payload: dict[str, Any], base_directory: Path) -> list[dict[str, Any]]:
    if payload.get("schema_version") != 1:
        raise ValueError("分层图片评测清单 schema_version 必须为 1。")
    if payload.get("artifact_kind") != "layered_image_ground_truth":
        raise ValueError("分层图片评测清单 artifact_kind 不正确。")
    dataset_id = payload.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("分层图片评测清单必须包含 dataset_id。")
    raw_images = payload.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        raise ValueError("分层图片评测清单必须包含非空 images。")

    images: list[dict[str, Any]] = []
    seen_image_ids: set[str] = set()
    seen_panel_ids: set[str] = set()
    for raw in raw_images:
        if not isinstance(raw, dict):
            raise ValueError("images 中的每一项必须是对象。")
        identifier = _required_text(raw, "id")
        if identifier in seen_image_ids:
            raise ValueError(f"图片 id 重复：{identifier}")
        seen_image_ids.add(identifier)
        raw_path = Path(_required_text(raw, "path")).expanduser()
        path = (
            raw_path.resolve() if raw_path.is_absolute() else (base_directory / raw_path).resolve()
        )
        if not path.is_file():
            raise ValueError(f"图片不存在：{path}")
        split = _required_text(raw, "split")
        if split not in ALLOWED_SPLITS:
            raise ValueError(f"图片 {identifier} 的 split 必须是 train、validation 或 test。")
        scope = _required_text(raw, "annotation_scope")
        if scope not in ALLOWED_SCOPES:
            raise ValueError(f"图片 {identifier} 的 annotation_scope 必须是 complete 或 targeted。")
        page = raw.get("page", 1)
        if not isinstance(page, int) or page < 1:
            raise ValueError(f"图片 {identifier} 的 page 必须是正整数。")
        raw_panels = raw.get("panels")
        if not isinstance(raw_panels, list) or not raw_panels:
            raise ValueError(f"图片 {identifier} 必须至少标注一个 Panel。")
        panels: list[dict[str, Any]] = []
        for raw_panel in raw_panels:
            if not isinstance(raw_panel, dict):
                raise ValueError(f"图片 {identifier} 的 panels 项必须是对象。")
            panel_id = _required_text(raw_panel, "id")
            if panel_id in seen_panel_ids:
                raise ValueError(f"Panel id 重复：{panel_id}")
            seen_panel_ids.add(panel_id)
            modality = _required_text(raw_panel, "modality")
            if modality not in ALLOWED_MODALITIES:
                raise ValueError(f"Panel {panel_id} 的 modality 不支持：{modality}")
            panels.append(
                {
                    "id": panel_id,
                    "region": _region(raw_panel.get("region"), f"Panel {panel_id}"),
                    "modality": modality,
                    "target_ids": _text_list(raw_panel.get("target_ids", []), "target_ids"),
                }
            )
        images.append(
            {
                "id": identifier,
                "path": path,
                "page": page,
                "split": split,
                "source_group": _required_text(raw, "source_group"),
                "annotation_scope": scope,
                "panels": panels,
            }
        )
    return images


def _load_page(image: dict[str, Any]) -> np.ndarray:
    pages = decode_image_pages(image["path"])
    page_number = image["page"]
    if page_number > len(pages):
        raise ValueError(f"图片 {image['id']} 只有 {len(pages)} 页，不能读取第 {page_number} 页。")
    pixels = canonical_pixels(pages[page_number - 1])
    height, width = pixels.shape[:2]
    for panel in image["panels"]:
        x, y, panel_width, panel_height = panel["region"]
        if x + panel_width > width or y + panel_height > height:
            raise ValueError(
                f"Panel {panel['id']} 的区域 {panel['region']} 超出图片尺寸 {width}x{height}。"
            )
    return pixels


def _load_relations(payload: dict[str, Any], images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_relations = payload.get("relations", [])
    if not isinstance(raw_relations, list):
        raise ValueError("relations 必须是列表。")
    image_by_id = {image["id"]: image for image in images}
    panel_to_image = {panel["id"]: image["id"] for image in images for panel in image["panels"]}
    relations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_relations:
        if not isinstance(raw, dict):
            raise ValueError("relations 中的每一项必须是对象。")
        identifier = _required_text(raw, "id")
        if identifier in seen_ids:
            raise ValueError(f"关系 id 重复：{identifier}")
        seen_ids.add(identifier)
        expected = _required_text(raw, "expected")
        if expected not in {"positive", "negative"}:
            raise ValueError(f"关系 {identifier} 的 expected 必须是 positive 或 negative。")
        raw_endpoints = raw.get("endpoints")
        if not isinstance(raw_endpoints, list) or len(raw_endpoints) != 2:
            raise ValueError(f"关系 {identifier} 必须包含两个 endpoints。")
        endpoints: list[dict[str, str]] = []
        for raw_endpoint in raw_endpoints:
            if not isinstance(raw_endpoint, dict):
                raise ValueError(f"关系 {identifier} 的 endpoint 必须是对象。")
            image_id = _required_text(raw_endpoint, "image_id")
            panel_id = _required_text(raw_endpoint, "panel_id")
            if image_id not in image_by_id or panel_to_image.get(panel_id) != image_id:
                raise ValueError(f"关系 {identifier} 引用了无效 endpoint：{image_id}/{panel_id}")
            endpoints.append({"image_id": image_id, "panel_id": panel_id})
        first_split = image_by_id[endpoints[0]["image_id"]]["split"]
        second_split = image_by_id[endpoints[1]["image_id"]]["split"]
        if first_split != second_split:
            raise ValueError(f"关系 {identifier} 的两个端点不能跨 split。")
        single_band = raw.get("western_single_band_enabled", False)
        if not isinstance(single_band, bool):
            raise ValueError("western_single_band_enabled 必须是布尔值。")
        relations.append(
            {
                "id": identifier,
                "expected": expected,
                "split": first_split,
                "endpoints": endpoints,
                "western_single_band_enabled": single_band,
                "case_id": str(raw.get("case_id") or ""),
            }
        )
    return relations


def _relation_endpoint(
    runtime_by_id: dict[str, dict[str, Any]], endpoint: dict[str, str]
) -> dict[str, Any]:
    runtime = runtime_by_id[endpoint["image_id"]]
    return {
        "pixels": runtime["pixels"],
        "predicted_boxes": runtime["predicted_boxes"],
        "panel": runtime["panels_by_id"][endpoint["panel_id"]],
    }


def _best_box_for_panel(
    panel: list[int], predicted_boxes: list[tuple[int, int, int, int]]
) -> tuple[tuple[int, int, int, int] | None, float]:
    candidates = sorted(
        ((_box_iou(tuple(panel), box), box) for box in predicted_boxes),
        key=lambda item: (-item[0], item[1]),
    )
    if not candidates:
        return None, 0.0
    best_iou, best_box = candidates[0]
    return (best_box if best_iou >= 0.5 else None), best_iou


def _evaluate_cropped_pair(
    first_pixels: np.ndarray,
    first_region: list[int] | tuple[int, int, int, int],
    second_pixels: np.ndarray,
    second_region: list[int] | tuple[int, int, int, int],
    western_single_band_enabled: bool,
) -> bool:
    with TemporaryDirectory(prefix="medical-image-layered-") as temporary_directory:
        temp = Path(temporary_directory)
        paths: list[Path] = []
        for name, pixels, region in (
            ("first.png", first_pixels, first_region),
            ("second.png", second_pixels, second_region),
        ):
            x, y, width, height = region
            crop = pixels[y : y + height, x : x + width]
            path = temp / name
            if crop.size == 0 or not cv2.imwrite(str(path), crop):
                raise OSError(f"无法写入分层评测临时图片：{path}")
            paths.append(path)
        findings, _ = ImageDuplicateDetector(
            western_single_band_enabled=western_single_band_enabled,
            analysis_mode=ImageAnalysisMode.AUTO,
        ).scan(
            paths,
            candidate_source_groups={
                str(paths[0].resolve()): "layered-endpoint-first",
                str(paths[1].resolve()): "layered-endpoint-second",
            },
        )
    return any(finding.finding_type in POSITIVE_FINDING_TYPES for finding in findings)


def _predict_panel_modality(source: Path, crop: np.ndarray) -> tuple[str, tuple[str, ...]]:
    pages = (np.ascontiguousarray(crop),)
    accepted: list[str] = []
    western_detector = WesternBlotDuplicateDetector()
    western_regions = ()
    if _looks_like_western_input(source, pages):
        western_regions = western_detector.extract_from_pages(source, pages)
        if western_regions:
            accepted.append("western_blot")

    dot_detector = DotBlotDuplicateDetector()
    dot_eligible = dot_detector.route_auto_pages(pages)[0]
    if western_regions and 1 in _western_dominant_pages(western_regions, pages):
        dot_eligible = False
    if dot_eligible and dot_detector.extract_from_pages(source, pages, eligible_pages=(True,)):
        accepted.append("dot_blot")

    if FluorescenceDuplicateDetector().extract_from_pages(source, pages, force=False):
        accepted.append("fluorescence")
    if PathologyDuplicateDetector().extract_from_pages(source, pages, force=False):
        accepted.append("pathology")

    if not accepted:
        return "generic", ()
    if len(accepted) == 1:
        return accepted[0], tuple(accepted)
    return "ambiguous", tuple(accepted)


def _maximum_box_matching(
    expected: list[tuple[int, int, int, int]],
    predicted: list[tuple[int, int, int, int]],
    threshold: float,
) -> list[tuple[int, int]]:
    edges = [
        sorted(
            (
                (predicted_index, _box_iou(expected_box, predicted_box))
                for predicted_index, predicted_box in enumerate(predicted)
                if _box_iou(expected_box, predicted_box) >= threshold
            ),
            key=lambda item: (-item[1], item[0]),
        )
        for expected_box in expected
    ]
    matched_expected_by_prediction: dict[int, int] = {}

    def assign(expected_index: int, visited: set[int]) -> bool:
        for predicted_index, _ in edges[expected_index]:
            if predicted_index in visited:
                continue
            visited.add(predicted_index)
            previous = matched_expected_by_prediction.get(predicted_index)
            if previous is None or assign(previous, visited):
                matched_expected_by_prediction[predicted_index] = expected_index
                return True
        return False

    order = sorted(
        range(len(expected)),
        key=lambda index: (-max((score for _, score in edges[index]), default=-1.0), index),
    )
    for expected_index in order:
        assign(expected_index, set())
    return sorted(
        (
            (expected_index, predicted_index)
            for predicted_index, expected_index in matched_expected_by_prediction.items()
        ),
        key=lambda item: item[0],
    )


def _box_iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    left = max(first_x, second_x)
    top = max(first_y, second_y)
    right = min(first_x + first_width, second_x + second_width)
    bottom = min(first_y + first_height, second_y + second_height)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = first_width * first_height + second_width * second_height - intersection
    return intersection / union if union else 0.0


def _binary_metrics(counts: dict[str, int]) -> dict[str, int | float | None]:
    tp = counts.get("tp", 0)
    fp = counts.get("fp", 0)
    fn = counts.get("fn", 0)
    tn = counts.get("tn", 0)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        ),
    }


def _recall_metrics(counts: dict[str, int]) -> dict[str, int | float | None]:
    tp = counts.get("tp", 0)
    fn = counts.get("fn", 0)
    return {"tp": tp, "fn": fn, "recall": tp / (tp + fn) if tp + fn else None}


def _outcome(expected_positive: bool, predicted_positive: bool) -> str:
    if expected_positive:
        return "tp" if predicted_positive else "fn"
    return "fp" if predicted_positive else "tn"


def _threshold_key(threshold: float) -> str:
    return f"iou_{threshold:.2f}".replace(".", "_")


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"字段 {key} 必须是非空字符串。")
    return value.strip()


def _region(value: object, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{label} 的 region 必须是 [x, y, width, height] 四个整数。")
    x, y, width, height = value
    if x < 0 or y < 0 or width < 1 or height < 1:
        raise ValueError(f"{label} 的 region 必须位于图片内且宽高为正数。")
    return value


def _text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} 必须是字符串列表。")
    return [item.strip() for item in value if item.strip()]


def _validate_source_group_splits(images: list[dict[str, Any]]) -> None:
    split_by_group: dict[str, str] = {}
    for image in images:
        previous = split_by_group.setdefault(image["source_group"], image["split"])
        if previous != image["split"]:
            raise ValueError(
                f"同一 source_group 不得跨 split：{image['source_group']}（图片 {image['id']}）"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="分别评测 Panel 拆分和 AUTO 类型路由。")
    parser.add_argument("manifest", type=Path, help="分层图片标准答案 JSON")
    parser.add_argument("--output", type=Path, help="可选：写出完整 JSON 结果")
    arguments = parser.parse_args()
    result = evaluate_layered_image_manifest(arguments.manifest)
    if arguments.output:
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    split = result["panel_splitting"]["all_annotated_panel_recall"]
    routing = result["type_routing"]
    print(
        "分层图片评测完成："
        f"Panel recall@0.50={_format_metric(split['iou_0_50']['recall'])}，"
        f"recall@0.75={_format_metric(split['iou_0_75']['recall'])}，"
        f"类型路由 exact accuracy={_format_metric(routing['exact_accuracy'])}。"
    )


def _format_metric(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
