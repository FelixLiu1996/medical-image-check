from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from medical_image_check import __version__
from medical_image_check.domain.models import FindingType, ReviewStatus, RiskLevel, ScanResult
from medical_image_check.domain.project import Project

REPORT_SCHEMA_VERSION = 1

RISK_LABELS = {
    RiskLevel.HIGH: "高",
    RiskLevel.MEDIUM: "中",
    RiskLevel.LOW: "低",
}
TYPE_LABELS = {
    FindingType.EXACT_DUPLICATE: "确认重复",
    FindingType.SUSPECTED_REUSE: "疑似复用",
    FindingType.HIGH_SIMILARITY: "高度相似",
    FindingType.NORMAL_RELATION: "正常关联",
    FindingType.STATISTICAL_ANOMALY: "统计异常",
}
REVIEW_LABELS = {
    ReviewStatus.PENDING: "待复核",
    ReviewStatus.CONFIRMED: "确认重复",
    ReviewStatus.NORMAL: "正常关联",
    ReviewStatus.FALSE_POSITIVE: "误报",
}


class ExcelReportExporter:
    def export(
        self,
        result: ScanResult,
        destination: str | Path,
        project: Project | None = None,
    ) -> Path:
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        output.parent.mkdir(parents=True, exist_ok=True)

        workbook = Workbook()
        overview = workbook.active
        overview.title = "扫描概览"
        self._write_overview(overview, result, project)
        self._write_findings(workbook, result)
        self._write_issues(workbook, result)
        self._write_sources(workbook, project)

        temporary = output.with_suffix(output.suffix + ".tmp")
        workbook.save(temporary)
        temporary.replace(output)
        return output

    @staticmethod
    def _write_overview(worksheet, result: ScanResult, project: Project | None) -> None:
        rows = [
            ("报告格式版本", REPORT_SCHEMA_VERSION),
            ("软件版本", __version__),
            ("算法版本", result.algorithm_version),
            ("扫描完成时间（UTC）", result.completed_at or "旧结果未记录"),
            ("生成时间（UTC）", datetime.now(UTC).isoformat()),
            ("项目名称", project.name if project else "未关联项目"),
            ("项目 ID", project.project_id if project else ""),
            ("扫描文件数", result.source_count),
            ("图片数", result.image_count),
            ("表格数", result.spreadsheet_count),
            ("结果数", len(result.findings)),
            ("高风险结果", sum(item.risk == RiskLevel.HIGH for item in result.findings)),
            ("中风险结果", sum(item.risk == RiskLevel.MEDIUM for item in result.findings)),
            ("低风险结果", sum(item.risk == RiskLevel.LOW for item in result.findings)),
            ("扫描提示数", len(result.issues)),
            ("用途说明", "本报告仅提供科研数据复核候选证据，不自动判定学术不端。"),
        ]
        worksheet.append(["项目", "内容"])
        for row in rows:
            worksheet.append(row)
        _format_sheet(worksheet, (24, 72))

    @staticmethod
    def _write_findings(workbook: Workbook, result: ScanResult) -> None:
        worksheet = workbook.create_sheet("查重结果")
        worksheet.append(
            [
                "结果 ID",
                "风险",
                "结果类别",
                "检测规则",
                "标题",
                "说明",
                "置信度",
                "位置 1",
                "位置 2",
                "其他位置",
                "证据详情",
                "人工复核",
            ]
        )
        for finding in result.findings:
            locations = [location.display_text for location in finding.locations]
            worksheet.append(
                [
                    finding.finding_id,
                    RISK_LABELS[finding.risk],
                    TYPE_LABELS[finding.finding_type],
                    finding.rule_id,
                    finding.title,
                    finding.description,
                    finding.confidence,
                    locations[0] if locations else "",
                    locations[1] if len(locations) > 1 else "",
                    "\n".join(locations[2:]),
                    json.dumps(finding.details, ensure_ascii=False, sort_keys=True),
                    REVIEW_LABELS[finding.review_status],
                ]
            )
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        _format_sheet(worksheet, (22, 8, 12, 24, 22, 48, 10, 48, 48, 48, 48, 12))

    @staticmethod
    def _write_issues(workbook: Workbook, result: ScanResult) -> None:
        worksheet = workbook.create_sheet("扫描提示")
        worksheet.append(["级别", "来源", "说明"])
        for issue in result.issues:
            worksheet.append([issue.severity, issue.source_path, issue.message])
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        _format_sheet(worksheet, (12, 60, 72))

    @staticmethod
    def _write_sources(workbook: Workbook, project: Project | None) -> None:
        worksheet = workbook.create_sheet("项目输入")
        worksheet.append(["序号", "原始路径"])
        for index, source in enumerate(project.source_paths if project else (), start=1):
            worksheet.append([index, source])
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        _format_sheet(worksheet, (10, 100))


def _format_sheet(worksheet, widths: tuple[int, ...]) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in worksheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
