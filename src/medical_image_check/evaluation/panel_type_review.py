# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from medical_image_check.infrastructure.images import canonical_pixels, decode_image_pages
from medical_image_check.services.panel_splitting import detect_panel_regions

ALLOWED_MODALITIES = {
    "generic",
    "western_blot",
    "dot_blot",
    "fluorescence",
    "pathology",
}


def build_panel_type_review_package(
    batch: str | Path,
    confirmed_relations: str | Path,
    output_directory: str | Path,
    initial_feedback: str | Path | None = None,
    *,
    distribution_mode: str = "local",
    zip_path: str | Path | None = None,
) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    relations_path = Path(confirmed_relations).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    if distribution_mode not in {"local", "remote"}:
        raise ValueError("distribution_mode 只支持 local 或 remote。")
    archive: Path | None = None
    if zip_path is not None:
        if distribution_mode != "remote":
            raise ValueError("只有 remote 模式可以生成可发送 ZIP。")
        archive = Path(zip_path).expanduser().resolve()
        if archive.exists():
            raise FileExistsError(f"ZIP already exists: {archive}")
    if distribution_mode == "local":
        try:
            output.relative_to(batch_path)
        except ValueError as exc:
            raise ValueError(
                "Panel/type 标注目录必须位于评测批次目录内，以免复制受限正式图片。"
            ) from exc

    relations = json.loads(relations_path.read_text(encoding="utf-8"))
    if relations.get("confirmed_subset_frozen") is not True:
        raise ValueError("关系清单必须是已冻结的 confirmed subset。")
    asset_by_path = _asset_catalog(batch_path)
    tasks_by_path: dict[Path, dict[str, Any]] = {}
    relation_records: list[dict[str, Any]] = []
    for case in relations.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "")
        for pair in case.get("pairs", []):
            if not isinstance(pair, dict) or pair.get("ground_truth_eligible") is not True:
                continue
            pair_id = str(pair.get("pair_id") or "")
            endpoints = pair.get("endpoints") or []
            if not pair_id or len(endpoints) != 2:
                continue
            target_ids: list[str] = []
            for side, endpoint in zip(("A", "B"), endpoints, strict=True):
                if not isinstance(endpoint, dict):
                    continue
                source = _resolve_path(batch_path, str(endpoint.get("official_path") or ""))
                region = _region(endpoint.get("official_region"), f"关系 {pair_id} 端点 {side}")
                expected_sha256 = str(endpoint.get("official_sha256") or "")
                actual_sha256 = _sha256_file(source)
                if expected_sha256 and expected_sha256 != actual_sha256:
                    raise ValueError(f"正式图片哈希不一致：{source}")
                task = tasks_by_path.setdefault(
                    source,
                    {
                        "source": source,
                        "source_group": _source_group(endpoint, source),
                        "targets": [],
                        "case_ids": set(),
                        "asset": asset_by_path.get(source, {}),
                    },
                )
                task["case_ids"].add(case_id)
                target_id = f"{pair_id}-{side.lower()}"
                target_ids.append(target_id)
                task["targets"].append(
                    {
                        "target_id": target_id,
                        "pair_id": pair_id,
                        "side": side,
                        "region": region,
                        "figure": str(endpoint.get("figure") or ""),
                        "panel_hint": str(endpoint.get("panel") or ""),
                    }
                )
            if len(target_ids) == 2:
                relation_records.append(
                    {
                        "pair_id": pair_id,
                        "case_id": case_id,
                        "target_ids": target_ids,
                        "expected": "positive",
                    }
                )

    output.mkdir(parents=True)
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(
        sorted(tasks_by_path.values(), key=lambda value: str(value["source"])), start=1
    ):
        source = item["source"]
        asset = item["asset"]
        pages = decode_image_pages(source)
        pixels = canonical_pixels(pages[0])
        height, width = pixels.shape[:2]
        source_sha256 = _sha256_file(source)
        task_id = f"panel-type-{index:03d}-{source_sha256[:10]}"
        redistributable = bool(asset.get("redistributable"))
        if distribution_mode == "remote" and redistributable:
            assets_directory = output / "assets"
            assets_directory.mkdir(exist_ok=True)
            packaged_name = f"{task_id}{source.suffix.lower()}"
            packaged_path = assets_directory / packaged_name
            shutil.copy2(source, packaged_path)
            browser_image_path = f"assets/{packaged_name}"
            asset_delivery = "included_in_package"
        elif distribution_mode == "remote":
            browser_image_path = str(asset.get("source_url") or "")
            if not _is_https_url(browser_image_path):
                raise ValueError(f"受限正式图片缺少可用的 HTTPS 官方图片地址：{source}")
            asset_delivery = "official_remote_url"
        else:
            browser_image_path = Path(os.path.relpath(source, output)).as_posix()
            asset_delivery = "local_reference"
        tasks.append(
            {
                "task_id": task_id,
                "batch_relative_path": source.relative_to(batch_path).as_posix(),
                "browser_image_path": browser_image_path,
                "source_sha256": source_sha256,
                "source_group": item["source_group"],
                "split": "validation",
                "page": 1,
                "width": width,
                "height": height,
                "case_ids": sorted(item["case_ids"]),
                "redistributable": redistributable,
                "asset_delivery": asset_delivery,
                "official_image_url": str(asset.get("source_url") or ""),
                "article_url": str(asset.get("article_url") or ""),
                "license_url": str(asset.get("license_url") or ""),
                "reuse_scope": str(asset.get("reuse_scope") or "unspecified"),
                "provider": str(asset.get("provider") or "unspecified"),
                "targets": sorted(item["targets"], key=lambda target: target["target_id"]),
                "algorithm_panels": [list(box) for box in detect_panel_regions(pixels)],
            }
        )

    payload = {
        "schema_version": 1,
        "artifact_kind": "panel_type_annotation_review_package",
        "dataset_id": str(relations.get("dataset_id") or batch_path.name),
        "review_scope": "annotate_target_panels_and_primary_modalities",
        "distribution_mode": distribution_mode,
        "algorithm_predictions_hidden_by_default": True,
        "restricted_assets_copied": False,
        "ground_truth_frozen": False,
        "task_ids": [task["task_id"] for task in tasks],
        "tasks": tasks,
        "relations": relation_records,
    }
    initial_state: dict[str, Any] = {}
    initial_feedback_sha256: str | None = None
    if initial_feedback is not None:
        initial_feedback_path = Path(initial_feedback).expanduser().resolve()
        initial_state = _load_initial_feedback(payload, initial_feedback_path)
        initial_feedback_sha256 = _sha256_file(initial_feedback_path)
        payload["initial_feedback_sha256"] = initial_feedback_sha256
    (output / "review-data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "index.html").write_text(
        _render_html(payload, initial_state=initial_state), encoding="utf-8"
    )
    (output / "README.txt").write_text(_readme(payload), encoding="utf-8")
    if distribution_mode == "remote":
        (output / "ASSET_SOURCES.txt").write_text(_asset_sources(payload), encoding="utf-8")
    result = {
        "output_directory": str(output),
        "task_count": len(tasks),
        "target_count": sum(len(task["targets"]) for task in tasks),
        "relation_count": len(relation_records),
        "restricted_task_count": sum(not task["redistributable"] for task in tasks),
        "redistributable_task_count": sum(task["redistributable"] for task in tasks),
        "entrypoint": str(output / "index.html"),
        "self_contained": False,
        "initial_feedback_sha256": initial_feedback_sha256,
    }
    if archive is not None:
        archive.parent.mkdir(parents=True, exist_ok=True)
        _write_zip(output, archive)
        result["zip_path"] = str(archive)
        result["zip_sha256"] = _sha256_file(archive)
    return result


def freeze_panel_type_feedback(
    batch: str | Path,
    review_data: str | Path,
    feedback: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    review_path = Path(review_data).expanduser().resolve()
    feedback_path = Path(feedback).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    package = json.loads(review_path.read_text(encoding="utf-8"))
    submitted = json.loads(feedback_path.read_text(encoding="utf-8"))
    if package.get("artifact_kind") != "panel_type_annotation_review_package":
        raise ValueError("review-data.json 类型不正确。")
    if submitted.get("artifact_kind") != "panel_type_annotation_feedback":
        raise ValueError("反馈 JSON 类型不正确。")
    if submitted.get("dataset_id") != package.get("dataset_id"):
        raise ValueError("反馈 dataset_id 与标注包不一致。")
    if submitted.get("task_ids") != package.get("task_ids"):
        raise ValueError("反馈 task_ids 与标注包不一致。")

    task_by_id = {task["task_id"]: task for task in package["tasks"]}
    review_by_id: dict[str, dict[str, Any]] = {}
    for review in submitted.get("task_reviews", []):
        if not isinstance(review, dict):
            raise ValueError("task_reviews 中的每一项必须是对象。")
        task_id = str(review.get("task_id") or "")
        if task_id not in task_by_id or task_id in review_by_id:
            raise ValueError(f"反馈包含无效或重复 task_id：{task_id}")
        review_by_id[task_id] = review

    images: list[dict[str, Any]] = []
    target_to_panel: dict[str, tuple[str, str]] = {}
    pending_task_ids: list[str] = []
    not_evaluable_task_ids: list[str] = []
    for task_id in package["task_ids"]:
        task = task_by_id[task_id]
        review = review_by_id.get(task_id, {})
        status = str(review.get("status") or "pending")
        if status == "pending":
            pending_task_ids.append(task_id)
            continue
        if status == "not_evaluable":
            not_evaluable_task_ids.append(task_id)
            continue
        if status != "complete":
            raise ValueError(f"任务 {task_id} 的 status 不支持：{status}")
        scope = str(review.get("annotation_scope") or "targeted")
        if scope not in {"complete", "targeted"}:
            raise ValueError(f"任务 {task_id} 的 annotation_scope 不正确。")
        source = (batch_path / task["batch_relative_path"]).resolve()
        if not source.is_file() or _sha256_file(source) != task["source_sha256"]:
            raise ValueError(f"任务 {task_id} 的正式图片缺失或哈希变化。")
        raw_panels = review.get("panels")
        if not isinstance(raw_panels, list) or not raw_panels:
            raise ValueError(f"已完成任务 {task_id} 必须至少包含一个 Panel。")
        panels: list[dict[str, Any]] = []
        for panel_index, raw_panel in enumerate(raw_panels, start=1):
            if not isinstance(raw_panel, dict):
                raise ValueError(f"任务 {task_id} 的 Panel 必须是对象。")
            region = _region(raw_panel.get("region"), f"任务 {task_id} Panel {panel_index}")
            _validate_region_in_image(region, task["width"], task["height"], task_id)
            modality = str(raw_panel.get("modality") or "")
            if modality not in ALLOWED_MODALITIES:
                raise ValueError(f"任务 {task_id} Panel {panel_index} 的类型不正确。")
            panel_id = f"{task_id}-panel-{panel_index:03d}"
            assigned_targets = [
                target["target_id"]
                for target in task["targets"]
                if _target_belongs_to_panel(target["region"], region)
            ]
            for target_id in assigned_targets:
                previous = target_to_panel.get(target_id)
                if previous is not None:
                    raise ValueError(f"目标 {target_id} 同时落入多个人工 Panel，请调整重叠边界。")
                target_to_panel[target_id] = (task_id, panel_id)
            panels.append(
                {
                    "id": panel_id,
                    "region": region,
                    "modality": modality,
                    "target_ids": assigned_targets,
                }
            )
        images.append(
            {
                "id": task_id,
                "path": Path(os.path.relpath(source, destination.parent)).as_posix(),
                "page": task["page"],
                "split": task["split"],
                "source_group": task["source_group"],
                "annotation_scope": scope,
                "panels": panels,
                "note": str(review.get("note") or ""),
            }
        )

    resolved_relations: list[dict[str, Any]] = []
    unresolved_relations: list[dict[str, Any]] = []
    for relation in package["relations"]:
        endpoints = [target_to_panel.get(target_id) for target_id in relation["target_ids"]]
        record = {
            "id": relation["pair_id"],
            "case_id": relation["case_id"],
            "expected": relation["expected"],
            "target_ids": relation["target_ids"],
        }
        if all(endpoint is not None for endpoint in endpoints):
            record["endpoints"] = [
                {"image_id": endpoint[0], "panel_id": endpoint[1]} for endpoint in endpoints
            ]
            resolved_relations.append(record)
        else:
            record["missing_target_ids"] = [
                target_id
                for target_id, endpoint in zip(relation["target_ids"], endpoints, strict=True)
                if endpoint is None
            ]
            unresolved_relations.append(record)

    result = {
        "schema_version": 1,
        "artifact_kind": "layered_image_ground_truth",
        "dataset_id": package["dataset_id"],
        "ground_truth_frozen": True,
        "review_data_sha256": _sha256_file(review_path),
        "feedback_sha256": _sha256_file(feedback_path),
        "image_count": len(images),
        "panel_count": sum(len(image["panels"]) for image in images),
        "resolved_relation_count": len(resolved_relations),
        "unresolved_relation_count": len(unresolved_relations),
        "pending_task_ids": pending_task_ids,
        "not_evaluable_task_ids": not_evaluable_task_ids,
        "images": images,
        "relations": resolved_relations,
        "unresolved_relations": unresolved_relations,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def normalize_panel_type_feedback(
    batch: str | Path,
    review_data: str | Path,
    feedback: str | Path,
    output_path: str | Path,
    *,
    complete_task_ids: tuple[str, ...] = (),
    drop_panels: tuple[tuple[str, int], ...] = (),
) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    review_path = Path(review_data).expanduser().resolve()
    feedback_path = Path(feedback).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Output file already exists: {destination}")
    package = json.loads(review_path.read_text(encoding="utf-8"))
    submitted = json.loads(feedback_path.read_text(encoding="utf-8"))
    if package.get("artifact_kind") != "panel_type_annotation_review_package":
        raise ValueError("review-data.json 类型不正确。")
    if submitted.get("artifact_kind") != "panel_type_annotation_feedback":
        raise ValueError("反馈 JSON 类型不正确。")
    if submitted.get("dataset_id") != package.get("dataset_id"):
        raise ValueError("反馈 dataset_id 与标注包不一致。")
    if submitted.get("task_ids") != package.get("task_ids"):
        raise ValueError("反馈 task_ids 与标注包不一致。")

    task_by_id = {task["task_id"]: task for task in package["tasks"]}
    reviews = submitted.get("task_reviews")
    if not isinstance(reviews, list):
        raise ValueError("反馈 task_reviews 必须是列表。")
    review_by_id = {
        str(review.get("task_id") or ""): review for review in reviews if isinstance(review, dict)
    }
    if set(review_by_id) != set(package["task_ids"]) or len(review_by_id) != len(reviews):
        raise ValueError("反馈必须为每个任务提供且仅提供一条 task_review。")
    unknown_complete = set(complete_task_ids) - set(task_by_id)
    if unknown_complete:
        raise ValueError(f"complete scope 覆盖包含未知任务：{sorted(unknown_complete)}")

    drop_by_task: dict[str, set[int]] = {}
    for task_id, panel_index in drop_panels:
        if task_id not in task_by_id:
            raise ValueError(f"删除 Panel 包含未知任务：{task_id}")
        if panel_index < 1:
            raise ValueError("删除 Panel 的序号必须从 1 开始。")
        drop_by_task.setdefault(task_id, set()).add(panel_index)

    normalized_reviews: list[dict[str, Any]] = []
    clamped_panels: list[dict[str, Any]] = []
    removed_panels: list[dict[str, Any]] = []
    scope_overrides: list[dict[str, str]] = []
    for task_id in package["task_ids"]:
        task = task_by_id[task_id]
        source = (batch_path / task["batch_relative_path"]).resolve()
        if not source.is_file() or _sha256_file(source) != task["source_sha256"]:
            raise ValueError(f"任务 {task_id} 的正式图片缺失或哈希变化。")
        review = review_by_id[task_id]
        status = str(review.get("status") or "pending")
        scope = str(review.get("annotation_scope") or "targeted")
        if task_id in complete_task_ids and scope != "complete":
            scope_overrides.append({"task_id": task_id, "before": scope, "after": "complete"})
            scope = "complete"
        panels = review.get("panels")
        if not isinstance(panels, list):
            raise ValueError(f"任务 {task_id} 的 panels 必须是列表。")
        requested_drops = drop_by_task.get(task_id, set())
        if any(panel_index > len(panels) for panel_index in requested_drops):
            raise ValueError(f"任务 {task_id} 的删除 Panel 序号超出范围。")
        normalized_panels: list[dict[str, Any]] = []
        for panel_index, panel in enumerate(panels, start=1):
            if not isinstance(panel, dict):
                raise ValueError(f"任务 {task_id} 的 Panel 必须是对象。")
            if panel_index in requested_drops:
                removed_panels.append(
                    {"task_id": task_id, "panel_index": panel_index, "panel": panel}
                )
                continue
            raw_region = panel.get("region")
            if (
                not isinstance(raw_region, list)
                or len(raw_region) != 4
                or any(not isinstance(value, int) for value in raw_region)
            ):
                raise ValueError(f"任务 {task_id} Panel {panel_index} 的 region 不正确。")
            x, y, width, height = raw_region
            if width < 1 or height < 1:
                raise ValueError(f"任务 {task_id} Panel {panel_index} 的宽高必须为正数。")
            left = max(0, min(x, task["width"]))
            top = max(0, min(y, task["height"]))
            right = max(0, min(x + width, task["width"]))
            bottom = max(0, min(y + height, task["height"]))
            if right <= left or bottom <= top:
                raise ValueError(f"任务 {task_id} Panel {panel_index} 裁剪后为空。")
            region = [left, top, right - left, bottom - top]
            if region != raw_region:
                clamped_panels.append(
                    {
                        "task_id": task_id,
                        "panel_index": panel_index,
                        "before": raw_region,
                        "after": region,
                    }
                )
            modality = str(panel.get("modality") or "")
            if modality not in ALLOWED_MODALITIES:
                raise ValueError(f"任务 {task_id} Panel {panel_index} 的类型不正确。")
            normalized_panels.append({"region": region, "modality": modality})
        normalized_reviews.append(
            {
                "task_id": task_id,
                "status": status,
                "annotation_scope": scope,
                "panels": normalized_panels,
                "note": str(review.get("note") or ""),
            }
        )

    normalized = {
        **submitted,
        "task_reviews": normalized_reviews,
        "normalization": {
            "source_feedback_sha256": _sha256_file(feedback_path),
            "review_data_sha256": _sha256_file(review_path),
            "clamped_panel_count": len(clamped_panels),
            "clamped_panels": clamped_panels,
            "removed_panel_count": len(removed_panels),
            "removed_panels": removed_panels,
            "scope_override_count": len(scope_overrides),
            "scope_overrides": scope_overrides,
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def _render_html(payload: dict[str, Any], *, initial_state: dict[str, Any] | None = None) -> str:
    cards = "".join(
        _render_task(task, index) for index, task in enumerate(payload["tasks"], start=1)
    )
    seed = json.dumps(
        {
            "schema_version": 1,
            "artifact_kind": "panel_type_annotation_feedback",
            "dataset_id": payload["dataset_id"],
            "task_ids": payload["task_ids"],
            "initial_state": initial_state or {},
            "initial_feedback_sha256": payload.get("initial_feedback_sha256"),
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Panel 边界与类型标注</title><style>
:root{{--ink:#17324d;--muted:#64788b;--line:#d6e1ea;--blue:#1976d2;--red:#d73535;--green:#138a5b;--soft:#f3f7fa}}*{{box-sizing:border-box}}body{{margin:0;background:#edf2f6;color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}header{{position:sticky;top:0;z-index:9;background:rgba(255,255,255,.97);border-bottom:1px solid var(--line);padding:14px 22px}}h1{{font-size:22px;margin:0}}button,select,textarea,input{{font:inherit}}button{{border:1px solid #a9bfd1;background:#fff;border-radius:8px;padding:8px 12px;cursor:pointer}}button.primary{{background:var(--blue);color:#fff;border-color:var(--blue);font-weight:700}}.toolbar{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:9px}}.progress{{margin-left:auto;font-weight:700;color:var(--green)}}main{{max-width:1380px;margin:auto;padding:20px}}.intro,.task{{background:#fff;border:1px solid var(--line);border-radius:13px;padding:17px;margin-bottom:17px}}.intro strong{{color:#0f5f9e}}.task-head{{display:flex;justify-content:space-between;gap:15px}}.meta{{color:var(--muted);font-size:13px}}.asset-links{{display:flex;gap:12px;flex-wrap:wrap;margin-top:5px;font-size:13px}}.workspace{{position:relative;margin:13px auto;max-width:100%;width:max-content;border:1px solid var(--line);background:#fff;line-height:0}}.workspace img{{display:block;max-width:100%;max-height:800px;width:auto;height:auto}}.workspace svg{{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}}.image-error{{line-height:1.5;padding:30px;max-width:720px;color:#9a5800}}.target{{fill:rgba(215,53,53,.08);stroke:var(--red);stroke-width:3;vector-effect:non-scaling-stroke}}.prediction{{fill:rgba(25,118,210,.05);stroke:var(--blue);stroke-width:2;stroke-dasharray:7 4;vector-effect:non-scaling-stroke}}.human{{fill:rgba(19,138,91,.08);stroke:var(--green);stroke-width:3;vector-effect:non-scaling-stroke}}.draft{{fill:rgba(19,138,91,.05);stroke:var(--green);stroke-width:2;stroke-dasharray:5 3;vector-effect:non-scaling-stroke}}.controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;background:var(--soft);padding:10px;border-radius:9px}}.panels{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:8px;margin-top:10px}}.panel-row{{display:flex;gap:8px;align-items:center;border:1px solid var(--line);border-radius:8px;padding:8px}}.panel-row code{{font-size:12px;flex:1}}textarea{{width:100%;min-height:48px;border:1px solid #b5c7d5;border-radius:8px;padding:8px;margin-top:10px}}.warning{{color:#9a5800}}.hidden{{display:none}}@media(max-width:700px){{header{{position:static}}main{{padding:10px}}.progress{{margin-left:0}}}}
</style></head><body><header><h1>Panel 边界与图片类型标注</h1><div class="toolbar"><button class="primary" id="export">导出标注 JSON</button><button id="clearStorage">清除本地草稿</button><span class="progress" id="progress"></span></div></header><main>
{_review_intro(payload)}{cards}</main>
<script id="seed" type="application/json">{seed}</script><script>
const seed=JSON.parse(document.getElementById('seed').textContent),key='mic-panel-type-'+seed.dataset_id+'-'+(seed.initial_feedback_sha256||'manual');
let state=JSON.parse(localStorage.getItem(key)||JSON.stringify(seed.initial_state||{{}}));
const save=()=>{{localStorage.setItem(key,JSON.stringify(state));updateProgress();}};
function taskState(id){{return state[id]||(state[id]={{panels:[],scope:'targeted',note:'',notEvaluable:false,hasDraft:false,draftReviewed:true}})}}
function point(svg,event){{const r=svg.getBoundingClientRect(),v=svg.viewBox.baseVal,x=Math.round((event.clientX-r.left)*v.width/r.width),y=Math.round((event.clientY-r.top)*v.height/r.height);return [Math.max(0,Math.min(v.width,x)),Math.max(0,Math.min(v.height,y))]}}
function render(id){{const root=document.getElementById(id),svg=root.querySelector('svg'),s=taskState(id),show=root.querySelector('.show-predictions').checked;svg.querySelectorAll('.dynamic').forEach(n=>n.remove());
root.querySelectorAll('.target-data').forEach(n=>{{const q=JSON.parse(n.dataset.region),r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('class','target dynamic');['x','y','width','height'].forEach((k,i)=>r.setAttribute(k,q[i]));svg.appendChild(r)}});
if(show)root.querySelectorAll('.prediction-data').forEach(n=>{{const q=JSON.parse(n.dataset.region),r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('class','prediction dynamic');['x','y','width','height'].forEach((k,i)=>r.setAttribute(k,q[i]));svg.appendChild(r)}});
s.panels.forEach((p,i)=>{{const r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('class','human dynamic');['x','y','width','height'].forEach((k,j)=>r.setAttribute(k,p.region[j]));svg.appendChild(r)}});
const list=root.querySelector('.panels');list.innerHTML='';s.panels.forEach((p,i)=>{{const row=document.createElement('div');row.className='panel-row';row.innerHTML=`<b>#${{i+1}}</b><code>${{p.region.join(', ')}}</code><select><option value="generic">通用/统计图/文字</option><option value="western_blot">Western blot</option><option value="dot_blot">Dot blot</option><option value="fluorescence">荧光图</option><option value="pathology">病理图</option></select><button>删除</button>`;row.querySelector('select').value=p.modality;row.querySelector('select').onchange=e=>{{p.modality=e.target.value;save()}};row.querySelector('button').onclick=()=>{{s.panels.splice(i,1);save();render(id)}};list.appendChild(row)}});
root.querySelector('.scope').value=s.scope;root.querySelector('textarea').value=s.note;root.querySelector('.not-evaluable').checked=s.notEvaluable;root.querySelector('.draft-confirmation').classList.toggle('hidden',!s.hasDraft);root.querySelector('.draft-reviewed').checked=s.draftReviewed;root.classList.toggle('disabled',s.notEvaluable);save();}}
document.querySelectorAll('.task').forEach(root=>{{const id=root.id,svg=root.querySelector('svg');let start=null,draft=null;taskState(id);svg.onpointerdown=e=>{{if(taskState(id).notEvaluable)return;start=point(svg,e);draft=document.createElementNS('http://www.w3.org/2000/svg','rect');draft.setAttribute('class','draft dynamic');svg.appendChild(draft);svg.setPointerCapture(e.pointerId)}};svg.onpointermove=e=>{{if(!start||!draft)return;const p=point(svg,e),x=Math.min(start[0],p[0]),y=Math.min(start[1],p[1]);draft.setAttribute('x',x);draft.setAttribute('y',y);draft.setAttribute('width',Math.abs(p[0]-start[0]));draft.setAttribute('height',Math.abs(p[1]-start[1]))}};svg.onpointerup=e=>{{if(!start)return;const p=point(svg,e),q=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.abs(p[0]-start[0]),Math.abs(p[1]-start[1])];start=null;draft=null;if(q[2]>=8&&q[3]>=8)taskState(id).panels.push({{region:q,modality:root.querySelector('.new-modality').value}});save();render(id)}};
const image=root.querySelector('img'),error=root.querySelector('.image-error');image.onerror=()=>{{image.classList.add('hidden');error.classList.remove('hidden')}};image.onload=()=>{{image.classList.remove('hidden');error.classList.add('hidden')}};root.querySelector('.show-predictions').onchange=()=>render(id);root.querySelector('.scope').onchange=e=>{{taskState(id).scope=e.target.value;save()}};root.querySelector('textarea').oninput=e=>{{taskState(id).note=e.target.value;save()}};root.querySelector('.undo').onclick=()=>{{taskState(id).panels.pop();save();render(id)}};root.querySelector('.not-evaluable').onchange=e=>{{taskState(id).notEvaluable=e.target.checked;save();render(id)}};root.querySelector('.draft-reviewed').onchange=e=>{{taskState(id).draftReviewed=e.target.checked;save()}};render(id)}});
function isDone(s){{return s.notEvaluable||(s.panels.length>0&&(!s.hasDraft||s.draftReviewed))}}
function updateProgress(){{const done=seed.task_ids.filter(id=>isDone(taskState(id))).length;document.getElementById('progress').textContent=`${{done}} / ${{seed.task_ids.length}} 已人工确认`}};
document.getElementById('export').onclick=()=>{{const out={{...seed,exported_at:new Date().toISOString(),task_reviews:seed.task_ids.map(id=>{{const s=taskState(id);return {{task_id:id,status:s.notEvaluable?'not_evaluable':isDone(s)?'complete':'pending',annotation_scope:s.scope,panels:s.panels,note:s.note}}}})}};delete out.initial_state;const blob=new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=seed.dataset_id+'-panel-type-feedback.json';a.click();URL.revokeObjectURL(a.href)}};
document.getElementById('clearStorage').onclick=()=>{{if(confirm('确认清除本页全部本地草稿？')){{localStorage.removeItem(key);location.reload()}}}};updateProgress();
</script></body></html>"""


def _review_intro(payload: dict[str, Any]) -> str:
    common = (
        '<section class="intro"><strong>红框只是已确认重复内容的位置，不是 Panel 标准边界。'
        "</strong>请拖拽画出包含红框的完整独立 Panel，并选择该 Panel 的主要类型。"
        "默认按“只标关系相关 Panel”计召回；只有确实标完本图所有 Panel 时才切换"
        "“完整标注”，用于计算拆分 Precision。蓝色算法框默认隐藏，避免影响人工判断。"
    )
    draft = '<p class="warning">绿色框若由预标草稿带入，只是待人工确认的起点，不是标准答案。'
    if payload.get("distribution_mode") == "remote":
        included = sum(task["asset_delivery"] == "included_in_package" for task in payload["tasks"])
        linked = sum(task["asset_delivery"] == "official_remote_url" for task in payload["tasks"])
        return (
            f"{common}{draft}这是可远程发送的复核包：{included} 张图片已按许可装入 ZIP；"
            f"{linked} 张受限图片没有复制，需联网从官方来源加载。若图片加载失败，请点击卡片中的"
            "“打开官方图片”；仍无法查看时勾选“本图不可评”，不要凭预标框判断。</p></section>"
        )
    return (
        f"{common}{draft}此页面直接引用本机评测目录中的正式图片，其中可能含受限资产；"
        "不能把本目录单独压缩或对外发送。</p></section>"
    )


def _render_task(task: dict[str, Any], index: int) -> str:
    targets = "".join(
        f'<span class="target-data" data-region="{html.escape(json.dumps(target["region"]))}"></span>'
        for target in task["targets"]
    )
    predictions = "".join(
        f'<span class="prediction-data" data-region="{html.escape(json.dumps(region))}"></span>'
        for region in task["algorithm_panels"]
    )
    target_summary = "；".join(
        f"{target['side']}：{html.escape(target['figure'] or '未知 Figure')} {html.escape(target['panel_hint'])}"
        for target in task["targets"]
    )
    delivery = task.get("asset_delivery", "local_reference")
    if delivery == "included_in_package":
        restriction = "图片已装入复核包"
    elif delivery == "official_remote_url":
        restriction = "受限图片，需联网读取官方来源"
    else:
        restriction = "可再分发" if task["redistributable"] else "受限，仅限本机查看"
    links = []
    if task.get("official_image_url"):
        links.append(_external_link(task["official_image_url"], "打开官方图片"))
    if task.get("article_url"):
        links.append(_external_link(task["article_url"], "打开原始文章"))
    asset_links = f'<div class="asset-links">{"".join(links)}</div>' if links else ""
    return f"""<section class="task" id="{task["task_id"]}"><div class="task-head"><div><b>#{index} · {html.escape(task["task_id"])}</b><div class="meta">案例 {html.escape(", ".join(task["case_ids"]))} · {html.escape(target_summary)} · {restriction}</div>{asset_links}</div><label><input class="not-evaluable" type="checkbox"> 本图不可评</label></div>
<div class="workspace" style="aspect-ratio:{task["width"]}/{task["height"]}"><img src="{html.escape(task["browser_image_path"], quote=True)}" alt="正式 Figure"><div class="image-error hidden">图片未能加载。请确认网络可用并点击上方“打开官方图片”；如果官方来源也无法访问，请将本图标为“不可评”。</div><svg viewBox="0 0 {task["width"]} {task["height"]}" preserveAspectRatio="none"></svg>{targets}{predictions}</div>
<div class="controls"><label>新框类型 <select class="new-modality"><option value="generic">通用/统计图/文字</option><option value="western_blot">Western blot</option><option value="dot_blot">Dot blot</option><option value="fluorescence">荧光图</option><option value="pathology">病理图</option></select></label><label>标注范围 <select class="scope"><option value="targeted">只标关系相关 Panel</option><option value="complete">已标完所有 Panel</option></select></label><label><input class="show-predictions" type="checkbox"> 显示蓝色算法框</label><label class="draft-confirmation hidden"><input class="draft-reviewed" type="checkbox"> 已核对预标草稿</label><button class="undo">撤销最后一框</button></div><div class="panels"></div><textarea placeholder="可选备注：难判断、嵌套结构或边界约定"></textarea></section>"""


def _readme(payload: dict[str, Any]) -> str:
    draft_note = (
        "\n本页已载入机器辅助预标。每张卡必须人工核对并勾选“已核对预标草稿”；"
        "未勾选的卡导出时保持 pending，不会进入正式标准答案。\n"
        if payload.get("initial_feedback_sha256")
        else ""
    )
    remote = payload.get("distribution_mode") == "remote"
    title = "可远程发送的医生复核包" if remote else "本机工作目录"
    delivery_note = (
        "本 ZIP 只内置许可允许再分发的正式图片；受限图片不在包内，由页面从官方来源联网加载。\n"
        "请先完整解压 ZIP，再打开 index.html。若受限图片无法加载，可点击“打开官方图片/原始文章”；\n"
        "仍不可查看时标记“本图不可评”，不要仅根据绿色预标框作答。\n"
        "复核完成后，只需把页面下载的 *-panel-type-feedback.json 发回。\n"
        if remote
        else "本目录不复制正式图片，而是直接引用批次中的本机资产；其中包含受限图片，禁止把本目录或上级批次打包外发。\n"
    )
    return f"""Panel 边界与类型标注（{title}）

1. {"完整解压 ZIP 后，" if remote else ""}打开 index.html。
2. 红框是已确认重复内容的位置，不是 Panel 标准边界。
3. 拖拽画出包含红框的完整独立 Panel，并选择主要类型。
4. 默认“只标关系相关 Panel”；确实标完全部 Panel 后才能选择“已标完所有 Panel”。
5. 逐张核对绿色预标框并勾选“已核对预标草稿”，完成后点击“导出标注 JSON”。
{draft_note}

任务：{len(payload["tasks"])} 张正式 Figure；关系：{len(payload["relations"])} 组。
算法蓝框默认隐藏，避免影响人工标准答案。
{delivery_note}
"""


def _external_link(url: str, label: str) -> str:
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _asset_sources(payload: dict[str, Any]) -> str:
    lines = [
        "复核包图片来源与许可说明",
        "",
        "included_in_package 表示图片文件按目录中的许可元数据随包提供；",
        "official_remote_url 表示 ZIP 中没有该图片文件，页面只引用官方网络地址。",
        "本包只用于当前算法评测与医生复核，不改变各来源原有许可条件。",
        "",
    ]
    for index, task in enumerate(payload["tasks"], start=1):
        lines.extend(
            [
                f"[{index}] {task['task_id']}",
                f"交付方式: {task['asset_delivery']}",
                f"来源机构: {task['provider']}",
                f"允许范围: {task['reuse_scope']}",
                f"官方图片: {task['official_image_url'] or '未提供'}",
                f"文章页面: {task['article_url'] or '未提供'}",
                f"许可页面: {task['license_url'] or '未提供'}",
                f"SHA-256: {task['source_sha256']}",
                "",
            ]
        )
    return "\n".join(lines)


def _load_initial_feedback(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    submitted = json.loads(path.read_text(encoding="utf-8"))
    if submitted.get("artifact_kind") != "panel_type_annotation_feedback":
        raise ValueError("预标草稿 artifact_kind 不正确。")
    if submitted.get("dataset_id") != payload.get("dataset_id"):
        raise ValueError("预标草稿 dataset_id 与标注包不一致。")
    if submitted.get("task_ids") != payload.get("task_ids"):
        raise ValueError("预标草稿 task_ids 与标注包不一致。")

    task_by_id = {task["task_id"]: task for task in payload["tasks"]}
    state: dict[str, Any] = {}
    for review in submitted.get("task_reviews", []):
        if not isinstance(review, dict):
            raise ValueError("预标草稿 task_reviews 中的每一项必须是对象。")
        task_id = str(review.get("task_id") or "")
        if task_id not in task_by_id or task_id in state:
            raise ValueError(f"预标草稿包含无效或重复 task_id：{task_id}")
        status = str(review.get("status") or "pending")
        if status not in {"pending", "complete", "not_evaluable"}:
            raise ValueError(f"预标草稿任务 {task_id} 的 status 不支持：{status}")
        scope = str(review.get("annotation_scope") or "targeted")
        if scope not in {"complete", "targeted"}:
            raise ValueError(f"预标草稿任务 {task_id} 的 annotation_scope 不正确。")
        panels: list[dict[str, Any]] = []
        for panel_index, raw_panel in enumerate(review.get("panels") or [], start=1):
            if not isinstance(raw_panel, dict):
                raise ValueError(f"预标草稿任务 {task_id} 的 Panel 必须是对象。")
            region = _region(raw_panel.get("region"), f"预标草稿任务 {task_id} Panel {panel_index}")
            task = task_by_id[task_id]
            _validate_region_in_image(region, task["width"], task["height"], task_id)
            modality = str(raw_panel.get("modality") or "")
            if modality not in ALLOWED_MODALITIES:
                raise ValueError(f"预标草稿任务 {task_id} Panel {panel_index} 的类型不正确。")
            panels.append({"region": region, "modality": modality})
        if status == "complete" and not panels:
            raise ValueError(f"预标草稿任务 {task_id} 标为 complete 但没有 Panel。")
        state[task_id] = {
            "panels": panels,
            "scope": scope,
            "note": str(review.get("note") or ""),
            "notEvaluable": status == "not_evaluable",
            "hasDraft": True,
            "draftReviewed": False,
        }
    return state


def _asset_catalog(batch: Path) -> dict[Path, dict[str, Any]]:
    catalog: dict[Path, dict[str, Any]] = {}
    for asset_path in batch.glob("eval-*/official-assets.json"):
        payload = json.loads(asset_path.read_text(encoding="utf-8"))
        for paper in payload.get("paper_assets", []):
            for figure in paper.get("figures", []):
                raw = str(figure.get("relative_path") or "")
                if not raw:
                    continue
                try:
                    path = _resolve_path(batch, raw)
                except ValueError:
                    continue
                catalog[path] = {
                    "redistributable": bool(figure.get("redistributable")),
                    "source_url": str(figure.get("source_url") or figure.get("image_url") or ""),
                    "article_url": str(figure.get("article_url") or paper.get("article_url") or ""),
                    "license_url": str(figure.get("license_url") or paper.get("license_url") or ""),
                    "reuse_scope": str(
                        figure.get("reuse_scope") or paper.get("reuse_scope") or "unspecified"
                    ),
                    "provider": str(
                        figure.get("provider") or paper.get("provider") or "unspecified"
                    ),
                }
    return catalog


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _write_zip(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            handle.write(path, path.relative_to(source).as_posix())


def _resolve_path(batch: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    candidates = [raw.resolve()] if raw.is_absolute() else [batch / raw, Path.cwd() / raw]
    if not raw.is_absolute():
        candidates.extend(parent / raw for parent in batch.parents)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise ValueError(f"正式图片不存在：{value}")


def _source_group(endpoint: dict[str, Any], source: Path) -> str:
    return str(
        endpoint.get("pmcid") or endpoint.get("doi") or endpoint.get("pmid") or source.parent.name
    )


def _target_belongs_to_panel(target: list[int], panel: list[int]) -> bool:
    target_x, target_y, target_width, target_height = target
    panel_x, panel_y, panel_width, panel_height = panel
    center_x = target_x + target_width / 2
    center_y = target_y + target_height / 2
    return (
        panel_x <= center_x <= panel_x + panel_width
        and panel_y <= center_y <= panel_y + panel_height
    )


def _validate_region_in_image(region: list[int], width: int, height: int, task_id: str) -> None:
    x, y, box_width, box_height = region
    if x + box_width > width or y + box_height > height:
        raise ValueError(f"任务 {task_id} 的 Panel 区域 {region} 超出图片尺寸 {width}x{height}。")


def _region(value: object, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{label} 必须是 [x, y, width, height] 四个整数。")
    x, y, width, height = value
    if x < 0 or y < 0 or width < 1 or height < 1:
        raise ValueError(f"{label} 必须位于图片内且宽高为正数。")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main_build() -> int:
    parser = argparse.ArgumentParser(description="构建本地或远程 Panel 边界与类型标注页面。")
    parser.add_argument("batch", type=Path, help="评测批次目录")
    parser.add_argument("confirmed_relations", type=Path, help="已冻结确认关系 JSON")
    parser.add_argument("output", type=Path, help="批次目录内的新标注页面目录")
    parser.add_argument(
        "--initial-feedback",
        type=Path,
        help="可选的机器/人工预标反馈 JSON；只作为页面草稿，不会冻结为标准答案",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="生成可远程发送的目录；只复制允许再分发的图片，受限图片使用官方 HTTPS 地址",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        help="remote 模式下同时生成的 ZIP 路径",
    )
    arguments = parser.parse_args()
    if arguments.zip is not None and not arguments.remote:
        parser.error("--zip 必须与 --remote 一起使用")
    result = build_panel_type_review_package(
        arguments.batch,
        arguments.confirmed_relations,
        arguments.output,
        initial_feedback=arguments.initial_feedback,
        distribution_mode="remote" if arguments.remote else "local",
        zip_path=arguments.zip,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main_freeze() -> int:
    parser = argparse.ArgumentParser(description="校验并冻结 Panel/type 人工标注。")
    parser.add_argument("batch", type=Path, help="评测批次目录")
    parser.add_argument("review_data", type=Path, help="标注包 review-data.json")
    parser.add_argument("feedback", type=Path, help="页面导出的反馈 JSON")
    parser.add_argument("output", type=Path, help="输出分层标准答案 JSON")
    arguments = parser.parse_args()
    result = freeze_panel_type_feedback(
        arguments.batch, arguments.review_data, arguments.feedback, arguments.output
    )
    print(
        json.dumps(
            {
                "image_count": result["image_count"],
                "panel_count": result["panel_count"],
                "resolved_relation_count": result["resolved_relation_count"],
                "unresolved_relation_count": result["unresolved_relation_count"],
                "pending_task_count": len(result["pending_task_ids"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main_normalize() -> int:
    parser = argparse.ArgumentParser(description="规范化 Panel/type 人工反馈并记录修正审计。")
    parser.add_argument("batch", type=Path, help="评测批次目录")
    parser.add_argument("review_data", type=Path, help="标注包 review-data.json")
    parser.add_argument("feedback", type=Path, help="页面导出的原始反馈 JSON")
    parser.add_argument("output", type=Path, help="规范化反馈 JSON 输出路径")
    parser.add_argument(
        "--complete-task",
        action="append",
        default=[],
        help="人工补充确认为完整标注的 task_id；可重复提供",
    )
    parser.add_argument(
        "--drop-panel",
        action="append",
        default=[],
        metavar="TASK_ID:PANEL_INDEX",
        help="人工确认需要移除的 1-based Panel；可重复提供",
    )
    arguments = parser.parse_args()
    drops: list[tuple[str, int]] = []
    for value in arguments.drop_panel:
        task_id, separator, panel_index = value.rpartition(":")
        if not separator or not task_id:
            parser.error(f"--drop-panel 格式错误：{value}")
        try:
            drops.append((task_id, int(panel_index)))
        except ValueError:
            parser.error(f"--drop-panel 序号必须是整数：{value}")
    result = normalize_panel_type_feedback(
        arguments.batch,
        arguments.review_data,
        arguments.feedback,
        arguments.output,
        complete_task_ids=tuple(arguments.complete_task),
        drop_panels=tuple(drops),
    )
    audit = result["normalization"]
    print(
        json.dumps(
            {
                "clamped_panel_count": audit["clamped_panel_count"],
                "removed_panel_count": audit["removed_panel_count"],
                "scope_override_count": audit["scope_override_count"],
                "output": str(arguments.output.expanduser().resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
