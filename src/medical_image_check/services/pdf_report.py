from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from functools import partial
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    Image,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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

PDF_FONT = "MedicalImageCheckCJK"
PDF_CID_FALLBACK = "STSong-Light"
MAX_PDF_IMAGE_EVIDENCE = 40


class PdfReportExporter:
    def export(
        self,
        result: ScanResult,
        destination: str | Path,
        project: Project | None = None,
    ) -> Path:
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".pdf":
            output = output.with_suffix(".pdf")
        output.parent.mkdir(parents=True, exist_ok=True)
        clear_image_preview_cache()
        temporary = output.with_suffix(output.suffix + ".tmp")
        _build_pdf(result, project, temporary)
        temporary.replace(output)
        return output


def _build_pdf(result: ScanResult, project: Project | None, output: Path) -> None:
    font_name = _register_font()
    styles = _styles(font_name)
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title="医学实验图像与数据查重报告",
        author="Medical Image Check",
    )
    project_name = project.name if project else "未关联项目"
    story = [
        Paragraph("医学实验图像与数据查重报告", styles["ReportTitle"]),
        Paragraph(f"项目：{escape(project_name)}", styles["Subtitle"]),
        Spacer(1, 5 * mm),
        _overview_table(result, project, styles),
        Spacer(1, 4 * mm),
        Paragraph(REPORT_DISCLAIMER, styles["Notice"]),
        Spacer(1, 6 * mm),
        Paragraph("查重结果", styles["Heading"]),
        _findings_table(result, styles),
    ]
    evidence = _evidence_story(result, styles)
    if evidence:
        story.extend([PageBreak(), Paragraph("图像证据", styles["Heading"]), *evidence])
    story.extend([Spacer(1, 5 * mm), Paragraph("扫描提示", styles["Heading"])])
    if result.issues:
        for issue in result.issues:
            story.append(
                Paragraph(
                    f"[{escape(issue.severity)}] {escape(issue.source_path)}："
                    f"{escape(issue.message)}",
                    styles["Body"],
                )
            )
    else:
        story.append(Paragraph("无扫描提示。", styles["Body"]))
    footer = partial(_page_footer, font_name=font_name)
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def _register_font() -> str:
    if PDF_FONT in pdfmetrics.getRegisteredFontNames():
        return PDF_FONT
    for font_path in _system_cjk_font_paths():
        if not font_path.is_file():
            continue
        try:
            font = TTFont(PDF_FONT, str(font_path), subfontIndex=0)
        except (OSError, TTFError):
            continue
        if ord("医") not in font.face.charWidths or ord("数") not in font.face.charWidths:
            continue
        pdfmetrics.registerFont(font)
        return PDF_FONT
    if PDF_CID_FALLBACK not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(PDF_CID_FALLBACK))
    return PDF_CID_FALLBACK


def _system_cjk_font_paths() -> tuple[Path, ...]:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    return (
        windows / "msyh.ttc",
        windows / "msyhbd.ttc",
        windows / "simsun.ttc",
        windows / "Deng.ttf",
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    )


def _styles(font_name: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "ReportTitle": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=21,
            leading=28,
            textColor=colors.HexColor("#17324d"),
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "Subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=11,
            leading=16,
            textColor=colors.HexColor("#526b7f"),
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "Heading": ParagraphStyle(
            "Heading",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=15,
            leading=21,
            spaceBefore=8,
            spaceAfter=8,
            textColor=colors.HexColor("#1769aa"),
            wordWrap="CJK",
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8.5,
            leading=12,
            wordWrap="CJK",
        ),
        "Small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=7,
            leading=9.5,
            textColor=colors.HexColor("#425a70"),
            wordWrap="CJK",
        ),
        "Notice": ParagraphStyle(
            "Notice",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=13,
            leftIndent=8,
            rightIndent=8,
            borderColor=colors.HexColor("#d99a00"),
            borderWidth=0.8,
            borderPadding=7,
            backColor=colors.HexColor("#fff5d9"),
            wordWrap="CJK",
        ),
    }


def _overview_table(result: ScanResult, project: Project | None, styles) -> Table:
    generated = datetime.now(UTC).isoformat()
    rows = [
        ["软件版本", __version__, "算法版本", result.algorithm_version],
        ["扫描完成", result.completed_at or "未记录", "报告生成", generated],
        [
            "输入文件",
            str(result.source_count),
            "图片 / 表格",
            f"{result.image_count} / {result.spreadsheet_count}",
        ],
        ["结果总数", str(len(result.findings)), "高 / 中 / 低", _risk_summary(result)],
        [
            "数字片段阈值",
            str(project.minimum_digit_run) if project else "-",
            "扫描提示",
            str(len(result.issues)),
        ],
        [
            "Excel 容差",
            (
                f"相对 {project.excel_custom_relative_tolerance_percent}% / "
                f"绝对 {project.excel_absolute_tolerance}"
                if project
                else "-"
            ),
            "连续风险阈值",
            (
                f"{project.excel_medium_run_length} / {project.excel_high_run_length}"
                if project
                else "-"
            ),
        ],
        [
            "Excel 运算目标",
            ", ".join(project.excel_operation_targets) if project else "-",
            "Western 单条带",
            "启用" if project and project.western_single_band_enabled else "关闭",
        ],
    ]
    formatted = [[Paragraph(escape(str(cell)), styles["Small"]) for cell in row] for row in rows]
    table = Table(formatted, colWidths=[27 * mm, 57 * mm, 27 * mm, 67 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf4f8")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#edf4f8")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c9d6df")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _findings_table(result: ScanResult, styles) -> LongTable:
    header = ["风险", "类别", "标题与说明", "置信度", "位置", "复核"]
    rows: list[list[Paragraph]] = [[Paragraph(value, styles["Small"]) for value in header]]
    for finding in result.findings:
        locations = "<br/>".join(escape(location.display_text) for location in finding.locations)
        rows.append(
            [
                Paragraph(RISK_LABELS[finding.risk], styles["Small"]),
                Paragraph(TYPE_LABELS[finding.finding_type], styles["Small"]),
                Paragraph(
                    f"<b>{escape(finding.title)}</b><br/>{escape(finding.description)}",
                    styles["Small"],
                ),
                Paragraph(f"{finding.confidence:.1%}", styles["Small"]),
                Paragraph(locations, styles["Small"]),
                Paragraph(REVIEW_LABELS[finding.review_status], styles["Small"]),
            ]
        )
    if len(rows) == 1:
        rows.append([Paragraph("无结果", styles["Small"]), *[Paragraph("", styles["Small"])] * 5])
    table = LongTable(
        rows,
        repeatRows=1,
        colWidths=[12 * mm, 19 * mm, 55 * mm, 15 * mm, 61 * mm, 16 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcebf4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d6df")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _evidence_story(result: ScanResult, styles) -> list:
    story: list = []
    count = 0
    for finding in result.findings:
        if count >= MAX_PDF_IMAGE_EVIDENCE:
            break
        if len(finding.locations) < 2 or not finding.rule_id.startswith("image."):
            continue
        previews: list[Image] = []
        for index, prefix in enumerate(("first", "second")):
            location = finding.locations[index]
            preview = image_preview_png(
                location.source_path,
                evidence_page(location.coordinate),
                evidence_region(finding.details, prefix),
                700,
                420,
            )
            if preview is None:
                previews = []
                break
            previews.append(_pdf_image(preview, 83 * mm, 48 * mm))
        if len(previews) != 2:
            continue
        count += 1
        story.extend(
            [
                Paragraph(f"{count}. {escape(finding.title)}", styles["Body"]),
                Paragraph(escape(finding.description), styles["Small"]),
                Table([previews], colWidths=[87 * mm, 87 * mm]),
                Paragraph(
                    "位置："
                    + "；".join(escape(item.display_text) for item in finding.locations[:2]),
                    styles["Small"],
                ),
                Paragraph(
                    "参数：" + escape(_compact_details(finding.details)),
                    styles["Small"],
                ),
                Spacer(1, 4 * mm),
            ]
        )
    if count >= MAX_PDF_IMAGE_EVIDENCE:
        story.append(
            Paragraph(
                f"PDF 最多嵌入前 {MAX_PDF_IMAGE_EVIDENCE} 条图像证据；"
                "全部结果请查看结果表或 Excel/HTML 报告。",
                styles["Notice"],
            )
        )
    return story


def _pdf_image(data: bytes, maximum_width: float, maximum_height: float) -> Image:
    image = Image(BytesIO(data))
    scale = min(maximum_width / image.imageWidth, maximum_height / image.imageHeight)
    image.drawWidth = image.imageWidth * scale
    image.drawHeight = image.imageHeight * scale
    return image


def _compact_details(details: dict) -> str:
    encoded = json.dumps(details, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= 2_000:
        return encoded
    return encoded[:1_970] + "…（参数过长，详见 Excel/HTML 报告）"


def _risk_summary(result: ScanResult) -> str:
    counts = [sum(item.risk == risk for item in result.findings) for risk in RiskLevel]
    return f"{counts[0]} / {counts[1]} / {counts[2]}"


def _page_footer(canvas, document, *, font_name: str) -> None:
    del document
    canvas.saveState()
    canvas.setFont(font_name, 7)
    canvas.setFillColor(colors.HexColor("#64788a"))
    canvas.drawString(16 * mm, 8 * mm, REPORT_DISCLAIMER)
    canvas.drawRightString(194 * mm, 8 * mm, f"第 {canvas.getPageNumber()} 页")
    canvas.restoreState()
