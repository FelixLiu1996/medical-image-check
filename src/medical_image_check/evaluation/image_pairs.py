from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import cv2

from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.models import Finding, FindingType
from medical_image_check.engines.image_similarity import ImageDuplicateDetector
from medical_image_check.infrastructure.images import canonical_pixels, decode_image_pages

POSITIVE_FINDING_TYPES = {
    FindingType.EXACT_DUPLICATE,
    FindingType.SUSPECTED_REUSE,
    FindingType.HIGH_SIMILARITY,
}
ALLOWED_SPLITS = {"train", "validation", "test"}
ALLOWED_MODALITIES = {
    "generic",
    "western_blot",
    "dot_blot",
    "fluorescence",
    "pathology",
}


def evaluate_image_pair_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path).resolve()
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    images = _load_images(payload, manifest_file.parent)
    pairs = _load_pairs(payload, images)
    _validate_source_group_splits(images)

    counts = defaultdict(int)
    counts_by_split: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    counts_by_modality: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    pair_results: list[dict[str, Any]] = []
    for pair in pairs:
        result = _evaluate_pair(pair, images)
        outcome = result["outcome"]
        counts[outcome] += 1
        counts_by_split[pair["split"]][outcome] += 1
        counts_by_modality[pair["modality"]][outcome] += 1
        pair_results.append(result)

    return {
        "schema_version": 1,
        "manifest": str(manifest_file),
        "image_count": len(images),
        "pair_count": len(pairs),
        "metrics": _metrics(counts),
        "metrics_by_split": {
            split: _metrics(split_counts) for split, split_counts in sorted(counts_by_split.items())
        },
        "metrics_by_modality": {
            modality: _metrics(modality_counts)
            for modality, modality_counts in sorted(counts_by_modality.items())
        },
        "pairs": pair_results,
    }


def _evaluate_pair(
    pair: dict[str, Any],
    images: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="medical-image-pair-") as temporary_directory:
        temp = Path(temporary_directory)
        first_path = _write_evaluation_image(
            images[pair["first"]], pair.get("first_region"), temp / "first.png"
        )
        second_path = _write_evaluation_image(
            images[pair["second"]], pair.get("second_region"), temp / "second.png"
        )
        detector = ImageDuplicateDetector(
            western_single_band_enabled=pair["western_single_band_enabled"],
            analysis_mode=ImageAnalysisMode(pair["analysis_mode"]),
        )
        findings, issues = detector.scan([first_path, second_path])

    positive_findings = [
        finding for finding in findings if finding.finding_type in POSITIVE_FINDING_TYPES
    ]
    normal_findings = [
        finding for finding in findings if finding.finding_type == FindingType.NORMAL_RELATION
    ]
    best = max(positive_findings, key=lambda item: item.confidence, default=None)
    predicted_positive = best is not None
    expected_positive = pair["expected"] == "positive"
    return {
        **pair,
        "predicted": "positive" if predicted_positive else "negative",
        "outcome": _outcome(expected_positive, predicted_positive),
        "evidence": _finding_payload(best),
        "normal_relations": [_finding_payload(finding) for finding in normal_findings],
        "issues": [{"message": issue.message, "severity": issue.severity} for issue in issues],
    }


def _write_evaluation_image(
    image: dict[str, Any],
    region: list[int] | None,
    destination: Path,
) -> Path:
    pages = decode_image_pages(image["path"])
    page_number = image["page"]
    if page_number > len(pages):
        raise ValueError(f"图片 {image['id']} 只有 {len(pages)} 页，不能读取第 {page_number} 页。")
    pixels = canonical_pixels(pages[page_number - 1])
    if region is not None:
        x, y, width, height = region
        image_height, image_width = pixels.shape[:2]
        if x + width > image_width or y + height > image_height:
            raise ValueError(
                f"图片 {image['id']} 的区域 {region} 超出图片尺寸 {image_width}x{image_height}。"
            )
        pixels = pixels[y : y + height, x : x + width]
    if not cv2.imwrite(str(destination), pixels):
        raise OSError(f"无法写入临时评测图片：{destination}")
    return destination


def _load_images(payload: dict[str, Any], base_directory: Path) -> dict[str, dict[str, Any]]:
    if payload.get("schema_version") != 1:
        raise ValueError("图片逐对评测清单 schema_version 必须为 1。")
    raw_images = payload.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        raise ValueError("图片逐对评测清单必须包含非空 images 列表。")

    images: dict[str, dict[str, Any]] = {}
    for raw in raw_images:
        if not isinstance(raw, dict):
            raise ValueError("images 中的每一项必须是对象。")
        identifier = _required_text(raw, "id")
        if identifier in images:
            raise ValueError(f"图片 id 重复：{identifier}")
        raw_path = Path(_required_text(raw, "path")).expanduser()
        path = (
            (base_directory / raw_path).resolve()
            if not raw_path.is_absolute()
            else raw_path.resolve()
        )
        if not path.is_file():
            raise ValueError(f"图片不存在：{path}")
        split = _required_text(raw, "split")
        if split not in ALLOWED_SPLITS:
            raise ValueError(f"图片 {identifier} 的 split 必须是 train、validation 或 test。")
        page = raw.get("page", 1)
        if not isinstance(page, int) or page < 1:
            raise ValueError(f"图片 {identifier} 的 page 必须是正整数。")
        images[identifier] = {
            "id": identifier,
            "path": path,
            "split": split,
            "source_group": _required_text(raw, "source_group"),
            "page": page,
        }
    return images


def _load_pairs(payload: dict[str, Any], images: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ValueError("图片逐对评测清单必须包含非空 pairs 列表。")
    pairs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_pairs:
        if not isinstance(raw, dict):
            raise ValueError("pairs 中的每一项必须是对象。")
        identifier = _required_text(raw, "id")
        if identifier in seen_ids:
            raise ValueError(f"图片对 id 重复：{identifier}")
        seen_ids.add(identifier)
        first = _required_text(raw, "first")
        second = _required_text(raw, "second")
        if first not in images or second not in images:
            raise ValueError(f"无效的图片对：{first} / {second}")
        first_region = _optional_region(raw, "first_region")
        second_region = _optional_region(raw, "second_region")
        if first == second and (first_region is None or second_region is None):
            raise ValueError(f"同一图片内比较必须同时指定两个区域：{identifier}")
        if first == second and first_region == second_region:
            raise ValueError(f"同一图片内比较的两个区域不能相同：{identifier}")
        if images[first]["split"] != images[second]["split"]:
            raise ValueError(f"同一评测图片对必须属于同一 split：{first} / {second}")
        expected = _required_text(raw, "expected")
        if expected not in {"positive", "negative"}:
            raise ValueError("expected 只能是 positive 或 negative；不确定案例不得计分。")
        modality = _required_text(raw, "modality")
        if modality not in ALLOWED_MODALITIES:
            raise ValueError(f"不支持的 modality：{modality}")
        analysis_mode = str(raw.get("analysis_mode", modality)).strip()
        try:
            ImageAnalysisMode(analysis_mode)
        except ValueError as exc:
            raise ValueError(f"不支持的 analysis_mode：{analysis_mode}") from exc
        single_band = raw.get("western_single_band_enabled", False)
        if not isinstance(single_band, bool):
            raise ValueError("western_single_band_enabled 必须是布尔值。")
        pairs.append(
            {
                "id": identifier,
                "first": first,
                "second": second,
                "first_region": first_region,
                "second_region": second_region,
                "expected": expected,
                "split": images[first]["split"],
                "modality": modality,
                "analysis_mode": analysis_mode,
                "western_single_band_enabled": single_band,
                "reuse_scope": str(raw.get("reuse_scope", "")),
                "label_source": str(raw.get("label_source", "")),
                "note": str(raw.get("note", "")),
            }
        )
    return pairs


def _optional_region(payload: dict[str, Any], key: str) -> list[int] | None:
    value = payload.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{key} 必须是 [x, y, width, height] 四个整数。")
    x, y, width, height = value
    if x < 0 or y < 0 or width < 1 or height < 1:
        raise ValueError(f"{key} 必须位于图片内且宽高为正数。")
    return value


def _validate_source_group_splits(images: dict[str, dict[str, Any]]) -> None:
    split_by_group: dict[str, str] = {}
    for identifier, image in images.items():
        group = image["source_group"]
        previous = split_by_group.setdefault(group, image["split"])
        if previous != image["split"]:
            raise ValueError(f"同一 source_group 不得跨 split：{group}（发现于图片 {identifier}）")


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"字段 {key} 必须是非空字符串。")
    return value.strip()


def _finding_payload(finding: Finding | None) -> dict[str, Any] | None:
    if finding is None:
        return None
    return {
        "rule_id": finding.rule_id,
        "finding_type": finding.finding_type.value,
        "risk": finding.risk.value,
        "confidence": finding.confidence,
        "title": finding.title,
        "details": finding.details,
    }


def _outcome(expected_positive: bool, predicted_positive: bool) -> str:
    if expected_positive:
        return "tp" if predicted_positive else "fn"
    return "fp" if predicted_positive else "tn"


def _metrics(counts: dict[str, int]) -> dict[str, int | float | None]:
    tp = counts.get("tp", 0)
    fp = counts.get("fp", 0)
    fn = counts.get("fn", 0)
    tn = counts.get("tn", 0)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="在本地逐对评估医学图片查重效果。")
    parser.add_argument("manifest", type=Path, help="本地评测清单 JSON 路径")
    parser.add_argument("--output", type=Path, help="可选：写出完整 JSON 结果")
    arguments = parser.parse_args()
    result = evaluate_image_pair_manifest(arguments.manifest)
    if arguments.output:
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    metrics = result["metrics"]
    print(
        "图片逐对评测完成："
        f"TP={metrics['tp']}，FP={metrics['fp']}，FN={metrics['fn']}，TN={metrics['tn']}，"
        f"precision={_format_metric(metrics['precision'])}，"
        f"recall={_format_metric(metrics['recall'])}，"
        f"specificity={_format_metric(metrics['specificity'])}。"
    )


def _format_metric(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
