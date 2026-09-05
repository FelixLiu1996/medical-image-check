# ruff: noqa: E501
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

POSITIVE_FINDING_TYPES = frozenset({"exact_duplicate", "suspected_reuse", "high_similarity"})
REVIEW_DECISIONS = (
    ("duplicate", "正确检出（确有重复）"),
    ("not_duplicate", "误报（并不重复）"),
    ("uncertain", "不确定"),
)
RULE_LABELS = {
    "image.local.geometric": "通用局部几何",
    "image.pathology.local_reuse": "病理局部复用",
    "image.western_blot.panel_reuse": "Western blot 面板复用",
    "image.fluorescence.same_channel_reuse": "荧光同通道复用",
    "image.global.perceptual": "整图感知相似",
    "image.small_region.content_reuse": "小区域内容复用",
    "image.dot_blot.spot_array_reuse": "Dot blot 点阵复用",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def build_negative_review_package(
    batch: str | Path,
    output_directory: str | Path,
    zip_path: str | Path | None = None,
    *,
    sample_size: int = 32,
    configuration: str = "panel-split-auto",
    seed: str = "negative-review-v1",
) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    archive = Path(zip_path).expanduser().resolve() if zip_path is not None else None
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    if archive is not None and archive.exists():
        raise FileExistsError(f"ZIP already exists: {archive}")
    if sample_size < 1:
        raise ValueError("sample_size must be positive")

    selection = select_negative_candidates(
        batch_path,
        sample_size=sample_size,
        configuration=configuration,
        seed=seed,
    )
    staging = output.with_name(f".{output.name}.building-{os.getpid()}")
    if staging.exists():
        raise FileExistsError(f"Staging directory already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        tasks = _prepare_tasks(staging, selection["selected_candidates"])
        payload = {
            "schema_version": 1,
            "artifact_kind": "negative_candidate_calibration_review_package",
            "dataset_id": selection["dataset_id"],
            "evaluation_role": "post_unseal_validation_regression",
            "configuration": configuration,
            "algorithm_version": selection["algorithm_version"],
            "created_at": datetime.now(UTC).isoformat(),
            "sampling": selection["sampling"],
            "review_scope": "classify_sampled_negative_algorithm_candidates_only",
            "metric_guardrail": (
                "This is a deterministic, case-balanced calibration sample from redistributable "
                "official figures. It can reveal false-positive patterns but cannot by itself "
                "produce exact full-batch precision or F1."
            ),
            "review_task_count": len(tasks),
            "review_case_count": len({task["case_id"] for task in tasks}),
            "review_tasks": tasks,
        }
        (staging / "review-data.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (staging / "index.html").write_text(_render_html(payload), encoding="utf-8")
        (staging / "README.txt").write_text(_readme(payload), encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "artifact_kind": "negative_candidate_review_package_manifest",
            "dataset_id": selection["dataset_id"],
            "entrypoint": "index.html",
            "configuration": configuration,
            "algorithm_version": selection["algorithm_version"],
            "formal_asset_policy": (
                "Only official figures explicitly marked redistributable=true are sampled and "
                "embedded. Restricted figures are excluded from this shareable calibration package."
            ),
            "files": [],
        }
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            manifest["files"].append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.replace(output)
        if archive is not None:
            archive.parent.mkdir(parents=True, exist_ok=True)
            temporary_archive = archive.with_name(f".{archive.name}.building-{os.getpid()}")
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as bundle:
                for path in sorted(item for item in output.rglob("*") if item.is_file()):
                    bundle.write(path, (Path(output.name) / path.relative_to(output)).as_posix())
            temporary_archive.replace(archive)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "output_directory": str(output),
        "zip_path": str(archive) if archive is not None else "",
        "zip_sha256": sha256_file(archive) if archive is not None else "",
        "review_task_count": len(tasks),
        "review_case_count": len({task["case_id"] for task in tasks}),
        "candidate_population_count": selection["sampling"]["all_negative_candidate_count"],
        "redistributable_candidate_count": selection["sampling"]["redistributable_candidate_count"],
        "restricted_candidate_count": selection["sampling"]["restricted_candidate_count"],
    }


def select_negative_candidates(
    batch: str | Path,
    *,
    sample_size: int,
    configuration: str,
    seed: str,
) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    truth = read_json(batch_path / "ground-truth-sealed.json")
    findings = read_json(batch_path / "blind-algorithm-findings-summary.json")
    expected_by_case = {
        str(case.get("case_id")): str(case.get("expected") or "")
        for case in truth.get("cases", [])
        if isinstance(case, dict)
    }
    assets_by_path = _asset_catalog(batch_path, expected_by_case)
    all_candidates: list[dict[str, Any]] = []
    redistributable: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    exact_duplicate_count = 0
    evaluable_negative_cases: set[str] = set()
    candidate_negative_cases: set[str] = set()
    for case in findings.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "")
        if expected_by_case.get(case_id) != "negative":
            continue
        run = next(
            (
                item
                for item in case.get("runs", [])
                if isinstance(item, dict) and item.get("configuration") == configuration
            ),
            None,
        )
        if (
            not isinstance(run, dict)
            or run.get("status") != "complete"
            or int(run.get("scan_input_count") or 0) < 2
        ):
            continue
        evaluable_negative_cases.add(case_id)
        for finding_index, finding in enumerate(run.get("findings", [])):
            if (
                not isinstance(finding, dict)
                or finding.get("finding_type") not in POSITIVE_FINDING_TYPES
            ):
                continue
            candidate_negative_cases.add(case_id)
            candidate = _candidate_record(
                batch_path,
                case_id,
                configuration,
                finding_index,
                finding,
                assets_by_path,
            )
            signature = str(candidate["exact_signature"])
            if signature in seen_signatures:
                exact_duplicate_count += 1
                continue
            seen_signatures.add(signature)
            all_candidates.append(candidate)
            if candidate["redistributable"]:
                redistributable.append(candidate)

    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in redistributable:
        strata[(candidate["rule_id"], candidate["confidence_band"])].append(candidate)
    if not strata:
        raise ValueError("No redistributable negative candidates are available for review")
    target = min(sample_size, len(redistributable))
    if target < len(strata):
        raise ValueError(
            f"sample_size {target} is smaller than the {len(strata)} non-empty rule/band strata"
        )
    allocation = _allocate_strata(strata, target)
    selected = _balanced_deterministic_sample(strata, allocation, seed)
    selected.sort(
        key=lambda item: (
            item["case_id"],
            item["rule_id"],
            -float(item["confidence"]),
            item["review_id"],
        )
    )

    population_by_rule = Counter(item["rule_id"] for item in all_candidates)
    redistributable_by_rule = Counter(item["rule_id"] for item in redistributable)
    selected_by_rule = Counter(item["rule_id"] for item in selected)
    stratum_records = []
    for key in sorted(strata):
        rule_id, band = key
        population = len(strata[key])
        selected_count = allocation[key]
        stratum_records.append(
            {
                "rule_id": rule_id,
                "rule_label": RULE_LABELS.get(rule_id, rule_id),
                "confidence_band": band,
                "redistributable_population_count": population,
                "selected_count": selected_count,
                "descriptive_expansion_weight": round(population / selected_count, 6),
            }
        )
    return {
        "dataset_id": str(findings.get("dataset_id") or truth.get("dataset_id") or ""),
        "algorithm_version": str(findings.get("algorithm_version") or ""),
        "selected_candidates": selected,
        "sampling": {
            "mode": "deterministic_case_balanced_stratified_calibration",
            "seed": seed,
            "configuration": configuration,
            "confidence_bands": {"high": ">=0.90", "medium": ">=0.75 and <0.90", "low": "<0.75"},
            "selection_scope": "redistributable_official_figures_only",
            "all_evaluable_negative_case_count": len(evaluable_negative_cases),
            "candidate_negative_case_count": len(candidate_negative_cases),
            "all_negative_candidate_count": len(all_candidates),
            "redistributable_candidate_count": len(redistributable),
            "restricted_candidate_count": len(all_candidates) - len(redistributable),
            "exact_duplicate_candidate_count_removed": exact_duplicate_count,
            "selected_candidate_count": len(selected),
            "selected_case_count": len({item["case_id"] for item in selected}),
            "case_cap": 2,
            "population_by_rule": dict(sorted(population_by_rule.items())),
            "redistributable_population_by_rule": dict(sorted(redistributable_by_rule.items())),
            "selected_by_rule": dict(sorted(selected_by_rule.items())),
            "strata": stratum_records,
            "inference_limit": (
                "Case balancing changes inclusion probabilities; expansion weights are descriptive "
                "only. Do not report formal precision/F1 from this package alone."
            ),
        },
    }


def _asset_catalog(
    batch: Path,
    expected_by_case: dict[str, str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for case_id, expected in expected_by_case.items():
        if expected != "negative":
            continue
        path = batch / case_id / "official-assets.json"
        if not path.is_file():
            continue
        payload = read_json(path)
        for paper in payload.get("paper_assets", []):
            if not isinstance(paper, dict):
                continue
            for figure in paper.get("figures", []):
                if not isinstance(figure, dict) or figure.get("status") != "downloaded":
                    continue
                raw_path = str(figure.get("relative_path") or "")
                local_path = _resolve_path(raw_path, batch)
                if local_path is None:
                    continue
                result[_normalized_path(local_path)] = {
                    "local_path": local_path,
                    "redistributable": figure.get("redistributable") is True,
                    "sha256": str(figure.get("sha256") or ""),
                    "source_url": str(figure.get("source_url") or ""),
                    "article_url": str(paper.get("article_url") or ""),
                    "paper_title": str(paper.get("title") or ""),
                    "doi": str(paper.get("doi") or ""),
                    "pmcid": str(paper.get("pmcid") or ""),
                    "figure_label": str(figure.get("label") or figure.get("figure_id") or ""),
                    "caption": str(figure.get("caption") or ""),
                }
    return result


def _candidate_record(
    batch: Path,
    case_id: str,
    configuration: str,
    finding_index: int,
    finding: dict[str, Any],
    assets_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    finding_id = str(finding.get("finding_id") or f"finding-{finding_index + 1}")
    locations: list[dict[str, Any]] = []
    for index, location in enumerate(finding.get("locations", [])[:2]):
        if not isinstance(location, dict):
            continue
        raw_path = str(location.get("source_path") or "")
        local_path = _resolve_path(raw_path, batch)
        asset = assets_by_path.get(_normalized_path(local_path)) if local_path else None
        locations.append(
            {
                "side": "AB"[index],
                "raw_path": raw_path,
                "local_path": local_path,
                "coordinate": str(location.get("coordinate") or ""),
                "region": _finding_region(finding, index),
                "asset": asset,
            }
        )
    confidence = _finite_number(finding.get("confidence")) or 0.0
    rule_id = str(finding.get("rule_id") or "unknown")
    signature_payload = {
        "case_id": case_id,
        "configuration": configuration,
        "rule_id": rule_id,
        "locations": [
            {
                "path": _normalized_path(item["local_path"]) if item["local_path"] else "",
                "region": item["region"],
            }
            for item in locations
        ],
    }
    return {
        "review_id": f"{case_id}-{configuration}-{finding_id}",
        "case_id": case_id,
        "configuration": configuration,
        "finding_id": finding_id,
        "rule_id": rule_id,
        "rule_label": RULE_LABELS.get(rule_id, rule_id),
        "finding_type": str(finding.get("finding_type") or ""),
        "risk": str(finding.get("risk") or ""),
        "confidence": confidence,
        "confidence_band": _confidence_band(confidence),
        "title": str(finding.get("title") or ""),
        "description": str(finding.get("description") or ""),
        "details": finding.get("details") if isinstance(finding.get("details"), dict) else {},
        "locations": locations,
        "redistributable": len(locations) == 2
        and all(item["asset"] and item["asset"]["redistributable"] for item in locations),
        "exact_signature": hashlib.sha256(
            json.dumps(signature_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }


def _allocate_strata(
    strata: dict[tuple[str, str], list[dict[str, Any]]],
    target: int,
) -> dict[tuple[str, str], int]:
    allocation = {key: 1 for key in strata}
    remaining = target - len(strata)
    capacity = sum(max(0, len(items) - 1) for items in strata.values())
    if remaining <= 0 or capacity <= 0:
        return allocation
    quotas = {key: remaining * max(0, len(items) - 1) / capacity for key, items in strata.items()}
    for key, quota in quotas.items():
        allocation[key] += min(len(strata[key]) - 1, math.floor(quota))
    left = target - sum(allocation.values())
    order = sorted(
        strata,
        key=lambda key: (
            -(quotas[key] - math.floor(quotas[key])),
            -len(strata[key]),
            key,
        ),
    )
    while left:
        progressed = False
        for key in order:
            if allocation[key] >= len(strata[key]):
                continue
            allocation[key] += 1
            left -= 1
            progressed = True
            if not left:
                break
        if not progressed:
            break
    return allocation


def _balanced_deterministic_sample(
    strata: dict[tuple[str, str], list[dict[str, Any]]],
    allocation: dict[tuple[str, str], int],
    seed: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    case_counts: Counter[str] = Counter()
    for key in sorted(strata, key=lambda item: (len(strata[item]), item)):
        candidates = sorted(
            strata[key],
            key=lambda item: _sample_hash(seed, item["review_id"]),
        )
        chosen: list[dict[str, Any]] = []
        remaining = list(candidates)
        while len(chosen) < allocation[key] and remaining:
            remaining.sort(
                key=lambda item: (
                    case_counts[item["case_id"]] >= 2,
                    case_counts[item["case_id"]],
                    _sample_hash(seed, item["review_id"]),
                )
            )
            candidate = remaining.pop(0)
            chosen.append(candidate)
            case_counts[candidate["case_id"]] += 1
        selected.extend(chosen)
    return selected


def _sample_hash(seed: str, review_id: str) -> str:
    return hashlib.sha256(f"{seed}|{review_id}".encode()).hexdigest()


def _confidence_band(value: float) -> str:
    if value >= 0.90:
        return "high"
    if value >= 0.75:
        return "medium"
    return "low"


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _finding_region(finding: dict[str, Any], index: int) -> list[int] | None:
    if index not in (0, 1):
        return None
    details = finding.get("details")
    if not isinstance(details, dict):
        return None
    prefix = "first" if index == 0 else "second"
    values = [details.get(f"{prefix}_region_{item}") for item in ("x", "y", "width", "height")]
    if any(isinstance(item, bool) or not isinstance(item, int | float) for item in values):
        return None
    x, y, width, height = (round(float(item)) for item in values)
    if x < 0 or y < 0 or width < 1 or height < 1:
        return None
    return [x, y, width, height]


def _resolve_path(value: str, batch: Path) -> Path | None:
    if not value:
        return None
    raw = Path(value).expanduser()
    candidates = [raw.resolve()] if raw.is_absolute() else []
    candidates.extend(((batch / raw).resolve(), (Path.cwd() / raw).resolve()))
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _normalized_path(value: Path | None) -> str:
    return os.path.normcase(os.path.normpath(str(value.resolve()))) if value is not None else ""


def _prepare_tasks(output: Path, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    originals: dict[str, str] = {}
    for candidate in candidates:
        prepared_locations: list[dict[str, Any]] = []
        for location in candidate["locations"]:
            asset = location["asset"]
            source = location["local_path"]
            if not isinstance(asset, dict) or not isinstance(source, Path):
                raise ValueError(
                    f"Selected candidate lacks a local redistributable asset: {candidate['review_id']}"
                )
            digest = str(asset.get("sha256") or sha256_file(source))
            original_href = originals.get(digest)
            if original_href is None:
                suffix = source.suffix.lower() or ".img"
                original_href = f"assets/originals/{digest[:16]}{suffix}"
                destination = output / original_href
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                originals[digest] = original_href
            region = location["region"]
            side = location["side"]
            base = f"assets/evidence/{_safe_name(candidate['review_id'])}-{side.lower()}"
            crop_href = f"{base}-crop.jpg"
            overview_href = f"{base}-overview.jpg"
            _write_crop(source, region, output / crop_href)
            _write_overview(source, region, side, output / overview_href)
            prepared_locations.append(
                {
                    "side": side,
                    "coordinate": location["coordinate"],
                    "region": region,
                    "crop_path": crop_href,
                    "overview_path": overview_href,
                    "original_path": original_href,
                    "paper_title": asset["paper_title"],
                    "identity": asset["doi"] or (f"PMC {asset['pmcid']}" if asset["pmcid"] else ""),
                    "figure_label": asset["figure_label"],
                    "caption": asset["caption"],
                    "source_url": asset["source_url"],
                    "article_url": asset["article_url"],
                    "sha256": digest,
                }
            )
        tasks.append(
            {
                key: candidate[key]
                for key in (
                    "review_id",
                    "case_id",
                    "configuration",
                    "finding_id",
                    "rule_id",
                    "rule_label",
                    "finding_type",
                    "risk",
                    "confidence",
                    "confidence_band",
                    "title",
                    "description",
                    "details",
                )
            }
            | {"locations": prepared_locations}
        )
    return tasks


def _write_crop(source: Path, region: list[int] | None, destination: Path) -> None:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    if region is None:
        crop = image
    else:
        x, y, width, height = region
        padding = max(12, round(max(width, height) * 0.15))
        crop = image.crop(
            (
                max(0, x - padding),
                max(0, y - padding),
                min(image.width, x + width + padding),
                min(image.height, y + height + padding),
            )
        )
    crop.thumbnail((1000, 700), Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    crop.save(destination, format="JPEG", quality=92, optimize=True)


def _write_overview(
    source: Path,
    region: list[int] | None,
    label: str,
    destination: Path,
) -> None:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    scale = min(1600 / max(image.width, 1), 1200 / max(image.height, 1), 1.0)
    if scale < 1.0:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if region is not None:
        draw = ImageDraw.Draw(image)
        x, y, width, height = region
        box = tuple(round(value * scale) for value in (x, y, x + width, y + height))
        line_width = max(4, round(min(image.width, image.height) * 0.006))
        draw.rectangle(box, outline=(220, 38, 38), width=line_width)
        draw.text((box[0] + line_width, max(0, box[1] - 18)), label, fill=(220, 38, 38))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="JPEG", quality=88, optimize=True)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")[:160] or "item"


def _render_html(payload: dict[str, Any]) -> str:
    cards = "".join(
        _render_task(task, index, payload["review_task_count"])
        for index, task in enumerate(payload["review_tasks"], start=1)
    )
    seed = json.dumps(
        {
            "schema_version": 1,
            "artifact_kind": "negative_candidate_doctor_feedback",
            "dataset_id": payload["dataset_id"],
            "review_scope": payload["review_scope"],
            "configuration": payload["configuration"],
            "algorithm_version": payload["algorithm_version"],
            "sampling": payload["sampling"],
            "review_ids": [task["review_id"] for task in payload["review_tasks"]],
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>阴性算法候选复核</title>
<style>:root{{--ink:#17324d;--muted:#687b8d;--line:#d7e1e9;--blue:#176fc1;--green:#147d55;--red:#b42318;--amber:#9a5d00}}*{{box-sizing:border-box}}body{{margin:0;background:#eef3f7;color:var(--ink);font:16px/1.58 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}header{{position:sticky;top:0;z-index:4;background:rgba(255,255,255,.98);border-bottom:1px solid var(--line);padding:15px 24px}}h1{{margin:0;font-size:24px}}.sub,.meta{{color:var(--muted)}}.toolbar{{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-top:10px}}button,input,select,textarea{{font:inherit}}button{{border:1px solid #a9bfd1;background:white;border-radius:9px;padding:9px 13px;cursor:pointer}}button.primary{{background:var(--blue);border-color:var(--blue);color:white;font-weight:700}}input[type=search]{{min-width:250px;border:1px solid #a9bfd1;border-radius:9px;padding:9px 11px}}.progress{{margin-left:auto;color:var(--green);font-weight:800}}main{{max-width:1260px;margin:auto;padding:20px}}.instruction{{background:white;border:2px solid #8dbde5;border-radius:14px;padding:17px 19px}}.instruction h2{{margin:0 0 7px}}.instruction p{{margin:6px 0}}.stats{{display:flex;gap:9px;flex-wrap:wrap;margin-top:11px}}.stat{{background:#f2f6f9;border-radius:8px;padding:7px 11px}}.task{{background:white;border:1px solid var(--line);border-radius:14px;margin:18px 0;overflow:hidden}}.task.done{{border-color:#82c4aa}}.head{{padding:14px 17px;border-bottom:1px solid var(--line);display:flex;gap:15px}}.counter{{font-size:19px;font-weight:800;min-width:94px}}.title{{font-weight:800}}.body{{padding:17px}}.question{{background:#edf7ff;border-left:5px solid var(--blue);border-radius:8px;padding:11px 13px;font-size:18px;font-weight:800}}.ab{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:13px}}figure{{margin:0;border:1px solid var(--line);border-radius:10px;padding:10px;background:white}}figure>img{{display:block;width:100%;height:260px;object-fit:contain}}figcaption{{font-weight:800;text-align:center;margin-top:6px}}details{{margin-top:9px}}summary{{cursor:pointer;color:#37617f;font-weight:650}}.overview{{display:block;max-width:100%;max-height:760px;object-fit:contain;margin:9px auto}}.answer{{border-top:1px solid var(--line);margin-top:16px;padding-top:15px}}.choices{{display:flex;gap:9px;flex-wrap:wrap}}.choice input{{position:absolute;opacity:0;pointer-events:none}}.choice span{{display:block;border:2px solid #aec0cf;border-radius:9px;padding:10px 15px;font-weight:800;cursor:pointer}}.choice input:checked+span{{border-color:var(--blue);background:#eaf4ff}}.choice.duplicate input:checked+span{{border-color:var(--green);background:#eaf8f2;color:var(--green)}}.choice.not_duplicate input:checked+span{{border-color:var(--red);background:#fff0ef;color:var(--red)}}.choice.uncertain input:checked+span{{border-color:var(--amber);background:#fff7e8;color:var(--amber)}}textarea{{width:100%;min-height:58px;margin-top:10px;border:1px solid #b8c9d7;border-radius:9px;padding:9px}}.hidden{{display:none}}@media(max-width:760px){{header{{position:static}}main{{padding:11px}}.ab{{grid-template-columns:1fr}}figure>img{{height:auto;max-height:420px}}.progress{{margin-left:0}}}}</style></head><body>
<header><h1>阴性算法候选复核</h1><div class="sub">每组只判断一对图片；无需检查算法参数，也无需重新查找论文。</div><div class="toolbar"><input id="search" type="search" placeholder="搜索案例或规则"><select id="filter"><option value="all">全部</option><option value="pending">仅未完成</option><option value="completed">仅已完成</option></select><button class="primary" id="export">导出反馈 JSON</button><span class="progress" id="progress"></span></div></header>
<main><section class="instruction"><h2>你只需要判断：A 和 B 是否真的重复</h2><p>红框和上方局部图是算法认为相似的位置。确实是同一内容或复用关系，选“正确检出”；只是同类组织、相似排版、正常内参或其他巧合，选“误报”；看不清选“不确定”。</p><p><strong>本包是第一轮误报校准样本，不是全部阴性候选。</strong>它从可合法分享图片的候选中按规则和置信度分层选取，用来先找主要误报模式；不能单独作为最终 Precision/F1。</p><div class="stats"><span class="stat">本次 <b>{payload["review_task_count"]}</b> 组</span><span class="stat">覆盖 <b>{payload["review_case_count"]}</b> 篇论文</span><span class="stat">可分享候选池 <b>{payload["sampling"]["redistributable_candidate_count"]}</b> 条</span></div></section>{cards}</main>
<script id="seed" type="application/json">{seed}</script><script>const seed=JSON.parse(document.getElementById('seed').textContent),key='negative-candidate-review-v1:'+seed.dataset_id+':'+seed.configuration;let state={{reviews:{{}}}};try{{const saved=JSON.parse(localStorage.getItem(key)||'{{}}');if(saved&&saved.reviews)state=saved}}catch(e){{}}function done(id){{return ['duplicate','not_duplicate','uncertain'].includes((state.reviews[id]||{{}}).decision)}}function apply(){{const q=document.getElementById('search').value.trim().toLowerCase(),f=document.getElementById('filter').value;let n=0;document.querySelectorAll('[data-task]').forEach(card=>{{const d=done(card.dataset.task);if(d)n++;card.classList.toggle('done',d);card.classList.toggle('hidden',!((f==='all'||(f==='pending'&&!d)||(f==='completed'&&d))&&card.textContent.toLowerCase().includes(q)))}});document.getElementById('progress').textContent=`已完成 ${{n}}/${{seed.review_ids.length}}`}}document.querySelectorAll('[data-task]').forEach(card=>{{const id=card.dataset.task,s=state.reviews[id]||{{}};card.querySelectorAll('[data-decision]').forEach(el=>{{el.checked=s.decision===el.value;el.addEventListener('change',()=>{{state.reviews[id]=state.reviews[id]||{{}};state.reviews[id].decision=el.value;localStorage.setItem(key,JSON.stringify(state));apply()}})}});const note=card.querySelector('[data-note]');note.value=s.note||'';note.addEventListener('input',()=>{{state.reviews[id]=state.reviews[id]||{{}};state.reviews[id].note=note.value;localStorage.setItem(key,JSON.stringify(state))}})}});document.getElementById('search').addEventListener('input',apply);document.getElementById('filter').addEventListener('change',apply);document.getElementById('export').addEventListener('click',()=>{{const payload={{...seed,exported_at:new Date().toISOString(),task_reviews:seed.review_ids.map(id=>({{review_id:id,decision:(state.reviews[id]||{{}}).decision||'pending',note:(state.reviews[id]||{{}}).note||''}}))}};const blob=new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${{seed.dataset_id}}-negative-feedback.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}});apply();</script></body></html>"""


def _render_task(task: dict[str, Any], index: int, total: int) -> str:
    figures = "".join(_render_location(location) for location in task["locations"])
    decisions = "".join(
        f'<label class="choice {_e(value)}"><input type="radio" name="decision-{_e(task["review_id"])}" value="{_e(value)}" data-decision><span>{_e(label)}</span></label>'
        for value, label in REVIEW_DECISIONS
    )
    return f"""<article class="task" data-task="{_e(task["review_id"])}"><div class="head"><div class="counter">第 {index}/{total} 组<div class="meta">{_e(task["case_id"])}</div></div><div><div class="title">{_e(task["rule_label"])} · 置信度 {float(task["confidence"]):.3f}</div><div class="meta">{_e(task["title"])}</div></div></div><div class="body"><div class="question">A 和 B 是否存在重复或复用关系？</div><div class="ab">{figures}</div><details><summary>查看算法说明</summary><p>{_e(task["description"])}</p><div class="meta">规则：{_e(task["rule_id"])}；Finding ID：{_e(task["finding_id"])}</div></details><section class="answer"><div class="choices">{decisions}</div><textarea data-note placeholder="可选备注，例如：同类组织但不是同一区域、只是排版相似、正常内参"></textarea></section></div></article>"""


def _render_location(location: dict[str, Any]) -> str:
    links = []
    if location.get("source_url"):
        links.append(f'<a href="{_e(location["source_url"])}">打开正式图片来源</a>')
    if location.get("article_url"):
        links.append(f'<a href="{_e(location["article_url"])}">打开论文页面</a>')
    label = " · ".join(
        item for item in (location.get("figure_label"), location.get("identity")) if item
    )
    return f"""<figure><img src="{_e(location["crop_path"])}" alt="候选区域 {_e(location["side"])}"><figcaption>候选 {_e(location["side"])}</figcaption><div class="meta">{_e(label)}<br>{_e(location["paper_title"])}</div><details><summary>查看完整 Figure（红框为算法位置）</summary><img class="overview" src="{_e(location["overview_path"])}" alt="带红框完整 Figure"><p class="meta">{_e(location["coordinate"])}<br>{" · ".join(links)}</p></details></figure>"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _readme(payload: dict[str, Any]) -> str:
    return f"""阴性算法候选复核包（第一轮误报校准）

打开方式：解压后双击 index.html。必须保留整个目录，不能只发送 index.html。

每张卡片只需要判断一件事：算法红框所示的 A 和 B 是否真的重复或复用。
- 确实是同一内容或复用关系：选择“正确检出（确有重复）”；
- 只是同类组织、相似排版、正常内参或其他巧合：选择“误报（并不重复）”；
- 看不清：选择“不确定”。

完成后点击页面顶部“导出反馈 JSON”，将 JSON 发回。

本次共有 {payload["review_task_count"]} 组，涉及 {payload["review_case_count"]} 个案例。候选来自 {payload["configuration"]}，在 {payload["sampling"]["redistributable_candidate_count"]} 条可分享图片候选中按算法规则和置信度分层、并限制单篇论文任务数后选取。

本包用途是先定位主要误报模式，不是全部阴性候选。由于采用了案例均衡且排除了受限制正式图，本轮结果不能单独作为最终 Precision/F1；后续需根据反馈决定扩大复核、固定阴性真值或建立来源隔离的新 test。
"""
