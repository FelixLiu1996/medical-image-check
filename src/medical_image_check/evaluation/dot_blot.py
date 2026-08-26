from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.engines.dot_blot import DOT_BLOT_RULE_ID
from medical_image_check.engines.image_similarity import ImageDuplicateDetector


def evaluate_dot_blot_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path).resolve()
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    images, paths_by_id = _load_images(payload, manifest_file.parent)
    pairs = _load_pairs(payload, images)
    _validate_source_group_splits(images)

    findings, issues = ImageDuplicateDetector(analysis_mode=ImageAnalysisMode.DOT_BLOT).scan(
        list(paths_by_id.values())
    )
    identifiers_by_path = {str(path): identifier for identifier, path in paths_by_id.items()}
    detected: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in findings:
        if finding.rule_id != DOT_BLOT_RULE_ID or len(finding.locations) != 2:
            continue
        identifiers = tuple(
            identifiers_by_path.get(str(Path(location.source_path).resolve()), "")
            for location in finding.locations
        )
        if not all(identifiers):
            continue
        key = tuple(sorted(identifiers))
        previous = detected.get(key)
        if previous is None or finding.confidence > previous["confidence"]:
            detected[key] = {
                "confidence": finding.confidence,
                "matched_spot_count": finding.details.get("matched_spot_count"),
                "appearance_similarity": finding.details.get("appearance_similarity"),
                "layout_error": finding.details.get("layout_error"),
            }

    pair_results: list[dict[str, Any]] = []
    counts = defaultdict(int)
    counts_by_split: dict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for pair in pairs:
        key = tuple(sorted((pair["first"], pair["second"])))
        evidence = detected.get(key)
        minimum_spots = pair.get("minimum_matched_spots", 3)
        predicted_positive = evidence is not None and (
            evidence["matched_spot_count"] is None
            or evidence["matched_spot_count"] >= minimum_spots
        )
        expected_positive = pair["expected"] == "positive"
        outcome = _outcome(expected_positive, predicted_positive)
        split = images[pair["first"]]["split"]
        counts[outcome] += 1
        counts_by_split[split][outcome] += 1
        pair_results.append(
            {
                **pair,
                "outcome": outcome,
                "predicted": "positive" if predicted_positive else "negative",
                "evidence": evidence,
            }
        )

    return {
        "schema_version": 1,
        "manifest": str(manifest_file),
        "image_count": len(images),
        "pair_count": len(pairs),
        "metrics": _metrics(counts),
        "metrics_by_split": {
            split: _metrics(split_counts) for split, split_counts in sorted(counts_by_split.items())
        },
        "issues": [
            {
                "source_path": issue.source_path,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in issues
        ],
        "pairs": pair_results,
    }


def _load_images(
    payload: dict[str, Any],
    base_directory: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, Path]]:
    if payload.get("schema_version") != 1:
        raise ValueError("Dot blot 评测清单 schema_version 必须为 1。")
    raw_images = payload.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        raise ValueError("Dot blot 评测清单必须包含非空 images 列表。")

    images: dict[str, dict[str, str]] = {}
    paths_by_id: dict[str, Path] = {}
    seen_paths: set[Path] = set()
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
        if path in seen_paths:
            raise ValueError(f"同一路径不能注册为多个图片：{path}")
        seen_paths.add(path)
        images[identifier] = {
            "split": _required_text(raw, "split"),
            "source_group": _required_text(raw, "source_group"),
        }
        paths_by_id[identifier] = path
    return images, paths_by_id


def _load_pairs(payload: dict[str, Any], images: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ValueError("Dot blot 评测清单必须包含非空 pairs 列表。")
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_pairs:
        if not isinstance(raw, dict):
            raise ValueError("pairs 中的每一项必须是对象。")
        first = _required_text(raw, "first")
        second = _required_text(raw, "second")
        if first == second or first not in images or second not in images:
            raise ValueError(f"无效的图片对：{first} / {second}")
        key = tuple(sorted((first, second)))
        if key in seen:
            raise ValueError(f"图片对重复：{first} / {second}")
        seen.add(key)
        expected = _required_text(raw, "expected")
        if expected not in {"positive", "negative"}:
            raise ValueError("expected 只能是 positive 或 negative。")
        if images[first]["split"] != images[second]["split"]:
            raise ValueError(f"同一评测图片对必须属于同一 split：{first} / {second}")
        minimum_spots = raw.get("minimum_matched_spots", 3)
        if not isinstance(minimum_spots, int) or minimum_spots < 3:
            raise ValueError("minimum_matched_spots 必须是大于等于 3 的整数。")
        pairs.append(
            {
                "first": first,
                "second": second,
                "expected": expected,
                "split": images[first]["split"],
                "minimum_matched_spots": minimum_spots,
                "note": str(raw.get("note", "")),
            }
        )
    return pairs


def _validate_source_group_splits(images: dict[str, dict[str, str]]) -> None:
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
    parser = argparse.ArgumentParser(description="在本地评估 Dot blot 专项检测效果。")
    parser.add_argument("manifest", type=Path, help="本地评测清单 JSON 路径")
    parser.add_argument("--output", type=Path, help="可选：写出完整 JSON 结果")
    arguments = parser.parse_args()
    result = evaluate_dot_blot_manifest(arguments.manifest)
    if arguments.output:
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    metrics = result["metrics"]
    print(
        "Dot blot 评测完成："
        f"TP={metrics['tp']}，FP={metrics['fp']}，FN={metrics['fn']}，TN={metrics['tn']}，"
        f"precision={_format_metric(metrics['precision'])}，"
        f"recall={_format_metric(metrics['recall'])}。"
    )


def _format_metric(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
