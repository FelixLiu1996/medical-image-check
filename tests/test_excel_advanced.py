from pathlib import Path

import pytest
from openpyxl import Workbook

from medical_image_check.engines.excel_exact import ExactExcelDuplicateDetector


def _save_columns(path: Path, first: list[float], second: list[float] | None = None) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["第一组", "第二组"])
    row_count = max(len(first), len(second or []))
    for index in range(row_count):
        sheet.append(
            [
                first[index] if index < len(first) else None,
                second[index] if second is not None and index < len(second) else None,
            ]
        )
    workbook.save(path)


@pytest.mark.parametrize(
    ("values", "expected_band"),
    [
        ([100.0, 100.005], "0.01%"),
        ([100.0, 100.05], "0.1%"),
        ([100.0, 100.5], "1%"),
        ([0.0, 0.0000000000005], "绝对容差"),
    ],
)
def test_detector_finds_approximate_value_bands(
    tmp_path: Path,
    values: list[float],
    expected_band: str,
) -> None:
    path = tmp_path / f"approximate-{expected_band}.xlsx"
    _save_columns(path, values)

    findings, issues = ExactExcelDuplicateDetector().scan([path])

    assert issues == []
    approximate = [item for item in findings if item.rule_id == "excel.value.approximate"]
    assert len(approximate) == 1
    assert approximate[0].risk == "low"
    assert approximate[0].details["tolerance_band"] == expected_band
    assert len(approximate[0].details["cells"]) == 2


def test_detector_does_not_report_values_outside_one_percent(tmp_path: Path) -> None:
    path = tmp_path / "not-approximate.xlsx"
    _save_columns(path, [100.0, 102.0])

    findings, _ = ExactExcelDuplicateDetector().scan([path])

    assert not any(item.rule_id == "excel.value.approximate" for item in findings)


@pytest.mark.parametrize(
    ("first", "second", "rule_id", "parameter"),
    [
        ([1, 2, 3, 4], [2, 4, 6, 8], "excel.series.scale", "2"),
        ([1, 2, 3, 4], [6, 7, 8, 9], "excel.series.offset", "5"),
        ([1, 2, 3, 4], [99, 98, 97, 96], "excel.series.target_sum", "100"),
        ([1, 2, 4, 5], [100, 50, 25, 20], "excel.series.target_product", "100"),
        ([1, 2, 3, 4], [1, 2, 3, 4], "excel.series.exact", "1"),
    ],
)
def test_detector_finds_repeated_series_relations(
    tmp_path: Path,
    first: list[float],
    second: list[float],
    rule_id: str,
    parameter: str,
) -> None:
    path = tmp_path / f"{rule_id}.xlsx"
    _save_columns(path, first, second)

    findings, issues = ExactExcelDuplicateDetector().scan([path])

    assert issues == []
    relations = [item for item in findings if item.rule_id == rule_id]
    assert len(relations) == 1
    assert relations[0].risk == "high"
    assert relations[0].details["parameter"] == parameter
    assert relations[0].details["matched_count"] == 4
    assert len(relations[0].details["paired_values"]) == 4


def test_three_repeated_series_relations_are_medium_risk(tmp_path: Path) -> None:
    path = tmp_path / "three-values.xlsx"
    _save_columns(path, [1, 2, 3], [10, 20, 30])

    findings, _ = ExactExcelDuplicateDetector().scan([path])

    scale = [item for item in findings if item.rule_id == "excel.series.scale"]
    assert len(scale) == 1
    assert scale[0].risk == "medium"
