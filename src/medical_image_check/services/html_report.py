# ruff: noqa: E501 - embedded HTML, CSS, and JavaScript remain readable as report source lines
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from medical_image_check import __version__
from medical_image_check.domain.models import RiskLevel, ScanResult
from medical_image_check.domain.project import Project
from medical_image_check.services.report_common import (
    REPORT_DISCLAIMER,
    REVIEW_LABELS,
    RISK_LABELS,
    TYPE_LABELS,
    clear_image_preview_cache,
    evidence_page,
    evidence_region,
    image_preview_png,
)

MAX_HTML_IMAGE_EVIDENCE = 120


class HtmlReportExporter:
    def export(
        self,
        result: ScanResult,
        destination: str | Path,
        project: Project | None = None,
    ) -> Path:
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() not in {".html", ".htm"}:
            output = output.with_suffix(".html")
        output.parent.mkdir(parents=True, exist_ok=True)
        clear_image_preview_cache()
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(_render_html(result, project), encoding="utf-8")
        temporary.replace(output)
        return output


def _render_html(result: ScanResult, project: Project | None) -> str:
    generated_at = datetime.now(UTC).isoformat()
    finding_rows = "".join(_finding_row(finding) for finding in result.findings)
    evidence_cards = _image_evidence_cards(result)
    issue_rows = (
        "".join(
            f"<tr><td>{escape(issue.severity)}</td><td>{escape(issue.source_path)}</td>"
            f"<td>{escape(issue.message)}</td></tr>"
            for issue in result.issues
        )
        or '<tr><td colspan="3">无扫描提示</td></tr>'
    )
    project_name = project.name if project else "未关联项目"
    source_count = result.source_count
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(project_name)} - 医学实验查重报告</title>
<style>
:root{{--ink:#17324d;--muted:#61758a;--line:#d8e2ea;--panel:#f7fafc;--blue:#1769aa;--high:#b42318;--medium:#b54708;--low:#1f6f43}}
*{{box-sizing:border-box}} body{{margin:0;color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif;background:#eef3f7}}
main{{max-width:1320px;margin:0 auto;padding:28px}} header{{padding:28px 32px;color:white;background:linear-gradient(120deg,#123a5a,#1769aa);border-radius:16px}}
h1{{margin:0 0 8px;font-size:28px}} h2{{margin:30px 0 12px}} .meta{{opacity:.9}} .notice{{margin:18px 0;padding:12px 16px;background:#fff5d9;border-left:4px solid #d99a00}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px;margin:18px 0}} .card{{padding:16px;background:white;border:1px solid var(--line);border-radius:12px}}
.card b{{display:block;font-size:25px}} .toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}} input,select{{padding:9px 11px;border:1px solid #aebdca;border-radius:8px;background:white}}
.table-wrap{{overflow:auto;background:white;border:1px solid var(--line);border-radius:12px}} table{{width:100%;border-collapse:collapse}} th,td{{padding:10px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}} th{{position:sticky;top:0;background:#edf4f8;white-space:nowrap}}
.risk-high{{color:var(--high);font-weight:700}} .risk-medium{{color:var(--medium);font-weight:700}} .risk-low{{color:var(--low);font-weight:700}}
.evidence-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}} .evidence{{background:white;border:1px solid var(--line);border-radius:12px;padding:16px;break-inside:avoid}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .pair figure{{margin:0}} .pair img{{width:100%;height:220px;object-fit:contain;background:var(--panel);border:1px solid var(--line)}} figcaption{{word-break:break-all;color:var(--muted);font-size:12px}}
details{{margin-top:8px}} pre{{white-space:pre-wrap;word-break:break-word;background:var(--panel);padding:10px;border-radius:8px}} footer{{margin:32px 0;color:var(--muted)}}
@media(max-width:760px){{main{{padding:12px}}.cards{{grid-template-columns:1fr 1fr}}.pair{{grid-template-columns:1fr}}.evidence-grid{{grid-template-columns:1fr}}}}
@media print{{body{{background:white}}main{{max-width:none;padding:0}}.toolbar{{display:none}}header{{border-radius:0}}th{{position:static}}}}
</style>
</head>
<body><main>
<header><h1>医学实验图像与数据查重报告</h1><div class="meta">项目：{escape(project_name)} · 软件 {escape(__version__)} · 算法 {escape(result.algorithm_version)}</div><div class="meta">扫描：{escape(result.completed_at or "未记录")} · 生成：{escape(generated_at)}</div></header>
<div class="notice">{escape(REPORT_DISCLAIMER)}</div>
<section class="cards">
<div class="card"><span>输入文件</span><b>{source_count}</b></div><div class="card"><span>图片 / 表格</span><b>{result.image_count} / {result.spreadsheet_count}</b></div>
<div class="card"><span>结果总数</span><b>{len(result.findings)}</b></div><div class="card"><span>高 / 中 / 低</span><b>{_risk_count(result, RiskLevel.HIGH)} / {_risk_count(result, RiskLevel.MEDIUM)} / {_risk_count(result, RiskLevel.LOW)}</b></div>
</section>
<h2>查重结果</h2><div class="toolbar"><input id="search" type="search" placeholder="搜索标题、说明、位置"><select id="risk"><option value="">全部风险</option><option value="high">高风险</option><option value="medium">中风险</option><option value="low">低风险</option></select></div>
<div class="table-wrap"><table id="findings"><thead><tr><th>风险</th><th>类别</th><th>标题与说明</th><th>置信度</th><th>位置</th><th>复核</th></tr></thead><tbody>{finding_rows}</tbody></table></div>
<h2>图像证据</h2><div class="evidence-grid">{evidence_cards or '<div class="card">当前结果没有可嵌入的双图像证据。</div>'}</div>
<h2>扫描提示</h2><div class="table-wrap"><table><thead><tr><th>级别</th><th>来源</th><th>说明</th></tr></thead><tbody>{issue_rows}</tbody></table></div>
<footer>输入路径仅作为定位证据记录；报告为完全本地生成的单文件，不加载网络资源。</footer>
</main>
<script>
const search=document.querySelector('#search'),risk=document.querySelector('#risk'),rows=[...document.querySelectorAll('#findings tbody tr')];
function filterRows(){{const q=search.value.trim().toLowerCase(),r=risk.value;for(const row of rows){{row.hidden=!!((r&&row.dataset.risk!==r)||(q&&!row.innerText.toLowerCase().includes(q)))}}}}
search.addEventListener('input',filterRows);risk.addEventListener('change',filterRows);
</script></body></html>"""


def _finding_row(finding) -> str:
    locations = "<br>".join(escape(location.display_text) for location in finding.locations)
    risk = finding.risk.value
    return (
        f'<tr data-risk="{risk}"><td class="risk-{risk}">{RISK_LABELS[finding.risk]}</td>'
        f"<td>{TYPE_LABELS[finding.finding_type]}</td>"
        f"<td><strong>{escape(finding.title)}</strong><br>{escape(finding.description)}"
        f"<details><summary>结构化证据</summary><pre>{escape(json.dumps(finding.details, ensure_ascii=False, indent=2))}</pre></details></td>"
        f"<td>{finding.confidence:.1%}</td><td>{locations}</td>"
        f"<td>{REVIEW_LABELS[finding.review_status]}</td></tr>"
    )


def _image_evidence_cards(result: ScanResult) -> str:
    cards: list[str] = []
    for finding in result.findings:
        if len(cards) >= MAX_HTML_IMAGE_EVIDENCE:
            break
        if len(finding.locations) < 2 or not finding.rule_id.startswith("image."):
            continue
        images: list[str] = []
        for index, prefix in enumerate(("first", "second")):
            location = finding.locations[index]
            preview = image_preview_png(
                location.source_path,
                evidence_page(location.coordinate),
                evidence_region(finding.details, prefix),
            )
            if preview is None:
                images = []
                break
            encoded = base64.b64encode(preview).decode("ascii")
            images.append(
                f'<figure><img alt="证据图 {index + 1}" src="data:image/png;base64,{encoded}">'
                f"<figcaption>{escape(location.display_text)}</figcaption></figure>"
            )
        if len(images) != 2:
            continue
        paired_images = "".join(images)
        cards.append(
            f'<article class="evidence"><h3>{escape(finding.title)}</h3>'
            f'<p>{escape(finding.description)}</p><div class="pair">{paired_images}</div>'
            f"<details><summary>证据参数</summary><pre>"
            f"{escape(json.dumps(finding.details, ensure_ascii=False, indent=2))}"
            f"</pre></details></article>"
        )
    if len(cards) >= MAX_HTML_IMAGE_EVIDENCE:
        cards.append(
            f'<div class="card">图像证据较多，HTML 最多内嵌前 {MAX_HTML_IMAGE_EVIDENCE} 条；'
            "全部结果仍保留在上方结果表和 Excel 报告中。</div>"
        )
    return "".join(cards)


def _risk_count(result: ScanResult, risk: RiskLevel) -> int:
    return sum(finding.risk == risk for finding in result.findings)
