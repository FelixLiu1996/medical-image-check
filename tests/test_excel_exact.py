from datetime import datetime
from pathlib import Path

import xlwt
from openpyxl import Workbook

from medical_image_check.engines.excel_exact import ExactExcelDuplicateDetector
from medical_image_check.infrastructure.spreadsheets import SpreadsheetReader


def _save_workbook(path: Path) -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "实验A"
    first.append(["样本", "数值1", "数值2", "日期"])
    first.append(["a", 2.5, 3.5, "2026-08-25"])
    first.append(["b", 0, 1, None])

    second = workbook.create_sheet("实验B")
    second.sheet_state = "hidden"
    second.append(["样本", "数值1", "数值2"])
    second.append(["c", 2.5, 3.5])
    second.append(["d", 0, 1])
    workbook.save(path)


def test_excel_detector_scans_hidden_sheets_and_exact_rows(tmp_path: Path) -> None:
    path = tmp_path / "experiment.xlsx"
    _save_workbook(path)

    findings, issues = ExactExcelDuplicateDetector().scan([path])

    assert issues == []
    assert any(finding.rule_id == "excel.row.exact" for finding in findings)
    value_findings = [finding for finding in findings if finding.rule_id == "excel.value.exact"]
    assert {finding.details["value"] for finding in value_findings} == {"2.5", "3.5"}
    assert any(location.hidden_sheet for finding in findings for location in finding.locations)


def test_reader_reports_formula_without_cached_result(tmp_path: Path) -> None:
    path = tmp_path / "formula.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "=1+1"
    workbook.save(path)

    result = SpreadsheetReader().read(path)

    assert result.cells == ()
    assert len(result.issues) == 1
    assert "没有保存计算结果" in result.issues[0].message


def test_csv_percentages_use_underlying_numeric_value(tmp_path: Path) -> None:
    path = tmp_path / "values.csv"
    path.write_text("组,数值\nA,50%\nB,0.5\n", encoding="utf-8")

    result = SpreadsheetReader().read(path)

    assert [cell.canonical_value for cell in result.cells] == ["0.5", "0.5"]


def test_xls_reader_ignores_dates_and_reads_numbers(tmp_path: Path) -> None:
    path = tmp_path / "legacy.xls"
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("原始数据")
    sheet.write(0, 0, 2.5)
    date_style = xlwt.easyxf(num_format_str="YYYY-MM-DD")
    sheet.write(0, 1, datetime(2026, 8, 25), date_style)
    workbook.save(str(path))

    result = SpreadsheetReader().read(path)

    assert [(cell.coordinate, cell.canonical_value) for cell in result.cells] == [("A1", "2.5")]
