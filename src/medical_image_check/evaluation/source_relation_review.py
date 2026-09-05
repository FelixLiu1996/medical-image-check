# ruff: noqa: E501
from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from medical_image_check.evaluation.source_relations import build_source_relation_draft

REVIEW_DECISIONS = (
    ("correct", "正确"),
    ("wrong", "错误"),
    ("uncertain", "不确定"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_review_package(
    batch: str | Path,
    output_directory: str | Path,
    zip_path: str | Path | None = None,
) -> dict[str, Any]:
    batch_path = Path(batch).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    if zip_path is not None and Path(zip_path).expanduser().resolve().exists():
        raise FileExistsError(f"ZIP already exists: {Path(zip_path).expanduser().resolve()}")

    draft = build_source_relation_draft(batch_path)
    output.mkdir(parents=True)
    assets = output / "assets"
    source_assets = assets / "source-images"
    source_crop_assets = assets / "source-crops"
    official_overview_assets = assets / "official-overviews"
    official_crop_assets = assets / "official-crops"
    for directory in (
        source_assets,
        source_crop_assets,
        official_overview_assets,
        official_crop_assets,
    ):
        directory.mkdir(parents=True)

    tasks: list[dict[str, Any]] = []
    asset_failures: list[dict[str, str]] = []
    for case in draft["cases"]:
        case_tasks, case_failures = _prepare_case_tasks(
            batch_path,
            output,
            case,
            source_assets,
            source_crop_assets,
            official_overview_assets,
            official_crop_assets,
        )
        tasks.extend(case_tasks)
        asset_failures.extend(case_failures)

    review_payload = {
        "schema_version": 2,
        "artifact_kind": "source_relation_mapping_review_package",
        "dataset_id": draft["dataset_id"],
        "evaluation_role": draft["evaluation_role"],
        "review_scope": "verify_prepared_source_to_official_ab_mappings_only",
        "algorithm_findings_included": False,
        "ground_truth_frozen": False,
        "created_at": datetime.now(UTC).isoformat(),
        "case_count": draft["case_count"],
        "available_source_case_count": draft["available_source_case_count"],
        "source_image_count": draft["statement_count"],
        "source_box_pair_count": draft["source_box_pair_count"],
        "official_pair_candidate_count": draft["official_pair_candidate_count"],
        "review_task_count": len(tasks),
        "review_case_count": len({task["case_id"] for task in tasks}),
        "system_backlog": {
            "source_pairs_without_reviewable_mapping": draft["source_box_pair_count"] - len(tasks),
            "unavailable_source_cases": draft["case_count"] - draft["available_source_case_count"],
            "asset_failures": asset_failures,
            "shown_to_doctor": False,
        },
        "promotion_rule": draft["promotion_rule"],
        "review_tasks": tasks,
        "source_draft": draft,
    }
    data_path = output / "source-relation-draft.json"
    data_path.write_text(json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "index.html").write_text(_render_html(review_payload), encoding="utf-8")
    (output / "README.txt").write_text(_readme(review_payload), encoding="utf-8")

    manifest = {
        "schema_version": 2,
        "artifact_kind": "source_relation_review_package_manifest",
        "dataset_id": draft["dataset_id"],
        "algorithm_findings_included": False,
        "ground_truth_frozen": False,
        "entrypoint": "index.html",
        "files": [],
    }
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        manifest["files"].append(
            {
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    archive: Path | None = None
    if zip_path is not None:
        archive = Path(zip_path).expanduser().resolve()
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(item for item in output.rglob("*") if item.is_file()):
                bundle.write(path, (Path(output.name) / path.relative_to(output)).as_posix())

    return {
        "output_directory": str(output),
        "zip_path": str(archive) if archive else "",
        "zip_sha256": sha256_file(archive) if archive else "",
        "case_count": draft["case_count"],
        "available_source_case_count": draft["available_source_case_count"],
        "source_image_count": draft["statement_count"],
        "source_box_pair_count": draft["source_box_pair_count"],
        "official_pair_candidate_count": draft["official_pair_candidate_count"],
        "review_task_count": len(tasks),
        "review_case_count": len({task["case_id"] for task in tasks}),
        "system_backlog_pair_count": draft["source_box_pair_count"] - len(tasks),
        "asset_failure_count": len(asset_failures),
    }


def _prepare_case_tasks(
    batch: Path,
    output: Path,
    case: dict[str, Any],
    source_assets: Path,
    source_crop_assets: Path,
    official_overview_assets: Path,
    official_crop_assets: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    official_by_id = {str(pair.get("pair_id")): pair for pair in case["official_pair_candidates"]}
    tasks: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for statement in case["statements"]:
        for pair in statement["source_box_pairs"]:
            official_pair_id = str(pair.get("official_pair_id") or "")
            official = official_by_id.get(official_pair_id)
            if not official or official.get("candidate_valid_for_review") is False:
                continue
            task_id = str(pair["source_pair_id"])
            prepared = _prepare_review_task(
                batch,
                output,
                case,
                statement,
                pair,
                official,
                source_assets,
                source_crop_assets,
                official_overview_assets,
                official_crop_assets,
            )
            if prepared is None:
                failures.append(
                    {
                        "task_id": task_id,
                        "reason": "source_or_official_review_asset_unavailable",
                    }
                )
                continue
            tasks.append(prepared)
    return tasks, failures


def _prepare_review_task(
    batch: Path,
    output: Path,
    case: dict[str, Any],
    statement: dict[str, Any],
    pair: dict[str, Any],
    official: dict[str, Any],
    source_assets: Path,
    source_crop_assets: Path,
    official_overview_assets: Path,
    official_crop_assets: Path,
) -> dict[str, Any] | None:
    task_id = str(pair["source_pair_id"])
    source_relative = str(statement["source_image_relative"])
    source = batch / source_relative
    if not source.is_file():
        return None
    packaged_source = source_assets / f"{statement['statement_id']}{source.suffix.lower()}"
    if not packaged_source.exists():
        shutil.copy2(source, packaged_source)
    source_crops = _write_source_pair_crops(
        source,
        pair["source_regions"],
        source_crop_assets,
        output,
        task_id,
    )
    if len(source_crops) != 2:
        return None

    endpoints = official.get("endpoints") or []
    if len(endpoints) != 2:
        return None
    prepared_endpoints: list[dict[str, Any]] = []
    for index, endpoint in enumerate(endpoints):
        prepared = _prepare_official_endpoint(
            batch,
            output,
            endpoint,
            official_overview_assets,
            official_crop_assets,
            task_id,
            "AB"[index],
        )
        if prepared is None:
            return None
        prepared_endpoints.append(prepared)

    return {
        "task_id": task_id,
        "case_id": case["case_id"],
        "source_order": case["source_order"],
        "wechat_url": case["wechat_url"],
        "wechat_title": case["wechat_title"],
        "claim_text": statement.get("claim_text") or "公众号文字未成功提取，请结合原图核对。",
        "statement_id": statement["statement_id"],
        "annotation_color": pair["annotation_color"],
        "source_image_path": packaged_source.relative_to(output).as_posix(),
        "source_crop_paths": source_crops,
        "source_regions": pair["source_regions"],
        "official_pair_id": official.get("pair_id") or "",
        "official_endpoints": prepared_endpoints,
        "candidate_validation_issues": official.get("candidate_validation_issues") or [],
    }


def _write_source_pair_crops(
    source: Path,
    regions: list[list[int]],
    crop_assets: Path,
    output: Path,
    task_id: str,
) -> list[str]:
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError):
        return []
    paths: list[str] = []
    for index, region in enumerate(regions[:2]):
        cropped = _crop_with_padding(image, region, padding_ratio=0.08)
        if cropped is None:
            return []
        path = crop_assets / f"{task_id}-{'ab'[index]}.png"
        cropped.save(path, format="PNG", optimize=True)
        paths.append(path.relative_to(output).as_posix())
    return paths


def _prepare_official_endpoint(
    batch: Path,
    output: Path,
    endpoint: dict[str, Any],
    overview_assets: Path,
    crop_assets: Path,
    task_id: str,
    side: str,
) -> dict[str, Any] | None:
    source = _resolve_official_path(batch, str(endpoint.get("official_path") or ""))
    region = endpoint.get("official_region")
    if source is None or not isinstance(region, list) or len(region) != 4:
        return None
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError):
        return None
    crop = _crop_with_padding(image, region, padding_ratio=0.2)
    if crop is None:
        return None

    crop_path = crop_assets / f"{task_id}-{side.lower()}.jpg"
    crop.save(crop_path, format="JPEG", quality=92, optimize=True)

    overview = image.copy()
    draw = ImageDraw.Draw(overview)
    x, y, width, height = (int(item) for item in region)
    line_width = max(4, round(min(overview.size) / 180))
    draw.rectangle(
        (x, y, x + width, y + height),
        outline=(220, 38, 38),
        width=line_width,
    )
    overview_path = overview_assets / f"{task_id}-{side.lower()}.jpg"
    overview.save(overview_path, format="JPEG", quality=88, optimize=True)

    identity = str(endpoint.get("doi") or "")
    if not identity:
        identity = f"PMID {endpoint.get('pmid') or '未知'}"
    return {
        "side": side,
        "paper_title": _plain_text(endpoint.get("paper_title") or ""),
        "identity": identity,
        "figure": endpoint.get("figure") or "",
        "panel": endpoint.get("panel") or "",
        "crop_path": crop_path.relative_to(output).as_posix(),
        "overview_path": overview_path.relative_to(output).as_posix(),
        "official_region": region,
        "official_sha256": endpoint.get("official_sha256") or "",
        "mapping_evidence": endpoint.get("mapping_evidence") or {},
    }


def _resolve_official_path(batch: Path, value: str) -> Path | None:
    if not value:
        return None
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve() if raw.is_file() else None
    candidates = [batch / raw]
    candidates.extend(parent / raw for parent in batch.parents)
    parts = raw.parts
    if "official-papers" in parts:
        candidates.insert(0, batch / Path(*parts[parts.index("official-papers") :]))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _crop_with_padding(
    image: Image.Image,
    region: list[int],
    *,
    padding_ratio: float,
) -> Image.Image | None:
    x, y, width, height = (int(item) for item in region)
    if width <= 0 or height <= 0:
        return None
    padding = max(12, round(padding_ratio * max(width, height)))
    left = max(0, x - padding)
    top = max(0, y - padding)
    right = min(image.width, x + width + padding)
    bottom = min(image.height, y + height + padding)
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom))


def _render_html(payload: dict[str, Any]) -> str:
    cards = "".join(
        _render_task_card(task, index, payload["review_task_count"])
        for index, task in enumerate(payload["review_tasks"], start=1)
    )
    embedded = json.dumps(
        {
            "schema_version": 2,
            "artifact_kind": "source_relation_doctor_feedback",
            "dataset_id": payload["dataset_id"],
            "review_scope": payload["review_scope"],
            "algorithm_findings_included": False,
            "ground_truth_frozen": False,
            "task_ids": [task["task_id"] for task in payload["review_tasks"]],
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>公众号阳性关系映射复核</title>
<style>
:root{{--ink:#17324d;--muted:#66788a;--line:#d8e2eb;--soft:#f4f7fa;--blue:#1777cc;--green:#147d55;--red:#b42318;--amber:#a15c00}}
*{{box-sizing:border-box}} body{{margin:0;background:#eef3f7;color:var(--ink);font:16px/1.58 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
header{{position:sticky;top:0;z-index:5;background:rgba(255,255,255,.97);border-bottom:1px solid var(--line);padding:16px 26px}} h1{{font-size:24px;margin:0}} .sub{{color:var(--muted);margin-top:3px}}
.toolbar{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:12px}} button,input,select,textarea{{font:inherit}} button{{border:1px solid #aac0d4;background:white;color:var(--ink);border-radius:9px;padding:9px 14px;cursor:pointer}} button.primary{{background:var(--blue);border-color:var(--blue);color:white;font-weight:700}}
input[type=search]{{min-width:250px;border:1px solid #aac0d4;border-radius:9px;padding:9px 11px}} .progress{{margin-left:auto;font-weight:700;color:var(--green)}}
.wrap{{max-width:1320px;margin:auto;padding:22px}} .instruction{{background:white;border:2px solid #8dbde5;border-radius:14px;padding:18px 20px;margin-bottom:18px}} .instruction h2{{margin:0 0 8px;font-size:21px}} .instruction p{{margin:6px 0}} .instruction strong{{color:#0f5e9c}}
.stats{{display:flex;gap:10px;margin-top:12px}} .stat{{background:var(--soft);border-radius:9px;padding:8px 12px}} .stat b{{font-size:19px;margin-right:4px}}
.task{{background:white;border:1px solid var(--line);border-radius:14px;margin:18px 0;overflow:hidden;box-shadow:0 2px 8px rgba(25,50,75,.04)}} .task.done{{border-color:#8dcbb4}} .task-head{{padding:15px 18px;border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:flex-start}} .counter{{font-size:20px;font-weight:800;min-width:105px}} .title{{font-weight:750}} .meta{{color:var(--muted);font-size:13px}} .source-link{{color:var(--blue)}} .task-body{{padding:18px}}
.question{{font-size:19px;font-weight:800;background:#edf7ff;border-left:5px solid var(--blue);border-radius:8px;padding:11px 14px;margin-bottom:16px}}
.stage{{border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}} .stage h3{{margin:0 0 10px;font-size:17px}} .stage.source{{background:#fbfdff}} .stage.official{{background:#f5fbf8;border-color:#b9dcca}}
.ab{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} figure{{margin:0;background:white;border:1px solid var(--line);border-radius:10px;padding:10px}} figure>img{{display:block;width:100%;height:240px;object-fit:contain;background:white}} figcaption{{font-weight:800;text-align:center;margin-top:6px}} .endpoint-meta{{font-size:13px;color:var(--muted);margin-top:5px;overflow-wrap:anywhere}}
details{{margin-top:10px}} summary{{cursor:pointer;color:#38627f;font-weight:650}} .overview{{display:block;max-width:100%;max-height:720px;object-fit:contain;margin:10px auto;border:1px solid var(--line);border-radius:8px}} blockquote{{margin:10px 0 0;border-left:4px solid #65a7df;background:#f2f8fd;padding:10px 12px}}
.answer{{border-top:1px solid var(--line);margin-top:16px;padding-top:16px}} .answer-title{{font-weight:800;margin-bottom:9px}} .choices{{display:flex;gap:10px;flex-wrap:wrap}} .choice{{cursor:pointer}} .choice input{{position:absolute;opacity:0;pointer-events:none}} .choice span{{display:block;min-width:112px;text-align:center;border:2px solid #afc2d3;border-radius:10px;padding:10px 18px;font-weight:800;background:white}} .choice input:checked+span{{border-color:var(--blue);background:#eaf5ff;color:#0d568f}} .choice.correct input:checked+span{{border-color:var(--green);background:#eaf8f2;color:#0f6848}} .choice.wrong input:checked+span{{border-color:var(--red);background:#fff0ef;color:var(--red)}} .choice.uncertain input:checked+span{{border-color:var(--amber);background:#fff7e8;color:var(--amber)}}
textarea{{width:100%;min-height:58px;border:1px solid #b8c9d8;border-radius:9px;padding:9px;margin-top:10px;resize:vertical}} .hidden{{display:none}} .footer-note{{color:var(--muted);font-size:13px;margin:22px 0}}
@media(max-width:760px){{header{{position:static}}.wrap{{padding:12px}}.ab{{grid-template-columns:1fr}}.progress{{margin-left:0}}figure>img{{height:auto;max-height:420px}}}}
</style></head><body>
<header><h1>公众号阳性关系映射复核</h1><div class="sub">只核对程序有没有把公众号 A/B 正确对应到论文原图；本页面不包含算法查重结果。</div>
<div class="toolbar"><input id="search" type="search" placeholder="搜索案例或论文"><select id="filter"><option value="all">全部</option><option value="pending">仅未完成</option><option value="completed">仅已完成</option></select><button class="primary" id="export">导出反馈 JSON</button><span class="progress" id="progress"></span></div></header>
<main class="wrap"><section class="instruction"><h2>你只需要做一件事</h2><p>每张卡片的<strong>上半部分</strong>是公众号给出的 A/B，<strong>下半部分</strong>是程序在论文正式图片中找到的 A/B。</p><p>请判断：<strong>下方 A、B 是否分别对应上方 A、B？</strong>对应无误选“正确”；任一边找错选“错误”；看不清选“不确定”。</p><p>不需要重新判断图片是否重复，不需要查 DOI、PMID 或 Figure/Panel，也不需要处理没有生成明确映射的案例。</p><div class="stats"><div class="stat"><b>{payload["review_task_count"]}</b>组待核对</div><div class="stat"><b>{payload["review_case_count"]}</b>个案例</div></div></section>
{cards}<p class="footer-note">未映射、来源受阻或资产不完整的项目已留给程序继续处理，不属于本次医生任务。导出的 JSON 只记录复核判断，收到后还会经过完整性校验再冻结标准答案。</p></main>
<script id="seed" type="application/json">{embedded}</script><script>
const seed=JSON.parse(document.getElementById('seed').textContent);const key='source-relation-mapping-review-v2:'+seed.dataset_id;let state={{reviews:{{}}}};
function readState(){{try{{const saved=JSON.parse(localStorage.getItem(key)||'{{}}');state=saved&&typeof saved==='object'&&saved.reviews? saved:{{reviews:{{}}}}}}catch(e){{state={{reviews:{{}}}}}}}}
function completed(id){{const value=(state.reviews[id]||{{}}).decision;return ['correct','wrong','uncertain'].includes(value)}}
function persist(){{localStorage.setItem(key,JSON.stringify(state));updateProgress();applyFilter()}}
function bind(){{document.querySelectorAll('[data-task]').forEach(card=>{{const id=card.dataset.task;const saved=state.reviews[id]||{{}};card.querySelectorAll('[data-decision]').forEach(el=>{{el.checked=saved.decision===el.value;el.addEventListener('change',()=>{{state.reviews[id]=state.reviews[id]||{{}};state.reviews[id].decision=el.value;persist()}})}});const note=card.querySelector('[data-note]');if(saved.note!==undefined)note.value=saved.note;note.addEventListener('input',()=>{{state.reviews[id]=state.reviews[id]||{{}};state.reviews[id].note=note.value;persist()}})}})}}
function updateProgress(){{const count=seed.task_ids.filter(completed).length;document.getElementById('progress').textContent=`已完成 ${{count}}/${{seed.task_ids.length}}`;document.querySelectorAll('[data-task]').forEach(card=>card.classList.toggle('done',completed(card.dataset.task)))}}
function applyFilter(){{const q=document.getElementById('search').value.trim().toLowerCase();const filter=document.getElementById('filter').value;document.querySelectorAll('[data-task]').forEach(card=>{{const done=completed(card.dataset.task);const allowed=filter==='all'||(filter==='pending'&&!done)||(filter==='completed'&&done);card.classList.toggle('hidden',!(allowed&&card.textContent.toLowerCase().includes(q)))}})}}
document.getElementById('search').addEventListener('input',applyFilter);document.getElementById('filter').addEventListener('change',applyFilter);
document.getElementById('export').addEventListener('click',()=>{{const payload={{...seed,exported_at:new Date().toISOString(),task_reviews:seed.task_ids.map(id=>({{task_id:id,decision:(state.reviews[id]||{{}}).decision||'pending',note:(state.reviews[id]||{{}}).note||''}}))}};const blob=new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${{seed.dataset_id}}-mapping-feedback.json`;document.body.appendChild(a);a.click();setTimeout(()=>{{URL.revokeObjectURL(a.href);a.remove()}},1000)}});
readState();bind();updateProgress();applyFilter();
</script></body></html>"""


def _render_task_card(task: dict[str, Any], index: int, total: int) -> str:
    source_figures = "".join(
        f'<figure><img src="{_e(path)}" alt="公众号区域 {"AB"[side]}"><figcaption>公众号 {"AB"[side]}</figcaption></figure>'
        for side, path in enumerate(task["source_crop_paths"])
    )
    official_figures = "".join(
        _render_official_endpoint(endpoint) for endpoint in task["official_endpoints"]
    )
    issues = task.get("candidate_validation_issues") or []
    issue_html = f"<p>定位提示：{_e('; '.join(str(item) for item in issues))}</p>" if issues else ""
    decision_html = "".join(
        f'<label class="choice {value}"><input type="radio" name="decision-{_e(task["task_id"])}" value="{_e(value)}" data-decision><span>{_e(label)}</span></label>'
        for value, label in REVIEW_DECISIONS
    )
    return f"""<article class="task" data-task="{_e(task["task_id"])}">
<div class="task-head"><div class="counter">第 {index}/{total} 组<div class="meta">#{task["source_order"]} · {_e(task["case_id"])}</div></div><div><div class="title">{_e(task["wechat_title"] or "公众号标题未取得")}</div><div class="meta"><a class="source-link" href="{_e(task["wechat_url"])}">打开公众号原文</a></div></div></div>
<div class="task-body"><div class="question">只判断：下方论文原图 A/B，是否分别对应上方公众号 A/B？</div>
<section class="stage source"><h3>1. 公众号给出的 A/B</h3><div class="ab">{source_figures}</div><details><summary>需要时查看公众号完整图片和文字说明</summary><img class="overview" src="{_e(task["source_image_path"])}" alt="公众号完整证据图"><blockquote>{_e(task["claim_text"])}</blockquote></details></section>
<section class="stage official"><h3>2. 程序找到的论文原图 A/B（请核对）</h3><div class="ab">{official_figures}</div><details><summary>技术定位信息（通常不用看）</summary><div class="meta">任务 ID：{_e(task["task_id"])}；正式映射 ID：{_e(task["official_pair_id"])}</div>{issue_html}</details></section>
<section class="answer"><div class="answer-title">这组映射是否正确？</div><div class="choices">{decision_html}</div><textarea data-note placeholder="可选备注；如果选错误，可以简单写 A 错、B 错或两边都错"></textarea></section></div></article>"""


def _render_official_endpoint(endpoint: dict[str, Any]) -> str:
    label = f"{endpoint['figure']} {endpoint['panel']}".strip()
    return f"""<figure><img src="{_e(endpoint["crop_path"])}" alt="论文原图区域 {_e(endpoint["side"])}"><figcaption>论文原图 {_e(endpoint["side"])}</figcaption><div class="endpoint-meta">{_e(label)} · {_e(endpoint["identity"])}<br>{_e(endpoint["paper_title"])}</div><details><summary>查看完整 Figure（红框是定位区域）</summary><img class="overview" src="{_e(endpoint["overview_path"])}" alt="带红框的完整 Figure"></details></figure>"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _plain_text(value: object) -> str:
    unescaped = html.unescape(str(value))
    return " ".join(re.sub(r"<[^>]+>", " ", unescaped).split())


def _readme(payload: dict[str, Any]) -> str:
    return f"""公众号阳性关系映射复核包

打开方式：解压后双击 index.html。必须保留整个目录，不能只单独发送 index.html。

你只需要做一件事：核对程序有没有把公众号 A/B 正确对应到论文原图 A/B。

每张卡片：
1. 上半部分是公众号给出的 A/B；
2. 下半部分是程序在论文正式图片中找到的 A/B；
3. 如果下方 A、B 分别对应上方 A、B，选择“正确”；任一边找错选择“错误”；看不清选择“不确定”。

不需要重新判断图片是否重复，不需要查 DOI、PMID 或 Figure/Panel，也不需要处理没有生成明确映射的案例。完整公众号图片、文字和带红框的完整 Figure 默认折叠，仅在需要时展开。

完成后点击页面顶部“导出反馈 JSON”，将 JSON 发回。

本次共有 {payload["review_task_count"]} 组映射，涉及 {payload["review_case_count"]} 个案例。其余 {payload["system_backlog"]["source_pairs_without_reviewable_mapping"]} 组未映射来源线索、{payload["system_backlog"]["unavailable_source_cases"]} 个来源受阻案例和资产异常均由程序继续处理，不属于本次医生任务。

本包不包含算法查重结果；医生是在验收标准答案映射，不是在评价算法，也不是从零重新查重。反馈返回后还会校验完整性，再冻结关系级标准答案。
"""
