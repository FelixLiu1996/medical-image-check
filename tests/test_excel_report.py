from pathlib import Path

from openpyxl import load_workbook

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    ScanResult,
)
from medical_image_check.domain.project import Project
from medical_image_check.services.excel_report import ExcelReportExporter


def test_excel_report_contains_overview_findings_issues_and_sources(tmp_path: Path) -> None:
    source = tmp_path / "data.xlsx"
    source.write_bytes(b"unchanged-source")
    locations = (
        EvidenceLocation(str(source), "实验A", "B2"),
        EvidenceLocation(str(source), "实验B", "C4", True),
    )
    finding = Finding(
        finding_id="finding-1",
        rule_id="excel.value.exact",
        finding_type=FindingType.EXACT_DUPLICATE,
        risk=RiskLevel.LOW,
        title="数值完全相同",
        description="完整数值 2.5 重复。",
        locations=locations,
        details={"value": "2.5", "count": 2},
    )
    result = ScanResult(
        source_count=1,
        image_count=0,
        spreadsheet_count=1,
        findings=(finding,),
        issues=(ScanIssue(str(source), "公式无缓存"),),
    )
    project = Project.create("报告测试").with_sources([source]).with_scan_result(result)

    output = ExcelReportExporter().export(result, tmp_path / "report", project)

    assert output.name == "report.xlsx"
    workbook = load_workbook(output, read_only=True)
    assert workbook.sheetnames == ["扫描概览", "查重结果", "扫描提示", "项目输入"]
    findings = workbook["查重结果"]
    assert findings["A2"].value == "finding-1"
    assert findings["B2"].value == "低"
    assert "实验A" in findings["H2"].value
    assert "实验B" in findings["I2"].value
    assert workbook["扫描提示"]["C2"].value == "公式无缓存"
    assert workbook["项目输入"]["B2"].value == str(source.resolve())
    workbook.close()
    assert source.read_bytes() == b"unchanged-source"
