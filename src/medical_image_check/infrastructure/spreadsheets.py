from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from itertools import zip_longest
from pathlib import Path

import openpyxl
import xlrd
from openpyxl.utils import get_column_letter

from medical_image_check.domain.models import EvidenceLocation, ScanIssue


@dataclass(frozen=True, slots=True)
class NumericCell:
    source_path: str
    sheet: str
    row: int
    column: int
    coordinate: str
    canonical_value: str
    display_value: str
    hidden_sheet: bool = False

    @property
    def location(self) -> EvidenceLocation:
        return EvidenceLocation(
            source_path=self.source_path,
            sheet=self.sheet,
            coordinate=self.coordinate,
            hidden_sheet=self.hidden_sheet,
        )


@dataclass(frozen=True, slots=True)
class SpreadsheetReadResult:
    cells: tuple[NumericCell, ...]
    issues: tuple[ScanIssue, ...] = ()


def canonical_numeric(value: int | float | Decimal) -> str:
    if isinstance(value, bool):
        raise ValueError("布尔值不属于数值扫描范围")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("非有限数值不属于扫描范围")
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError("非有限数值不属于扫描范围")
    if decimal_value == 0:
        return "0"
    text = format(decimal_value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _numeric_value(value: object, *, is_date: bool = False) -> tuple[str, str] | None:
    if is_date or isinstance(value, (bool, date, datetime, time)):
        return None
    if not isinstance(value, (int, float, Decimal)):
        return None
    try:
        return canonical_numeric(value), str(value)
    except ValueError:
        return None


class SpreadsheetReader:
    def read(self, path: str | Path) -> SpreadsheetReadResult:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            return self._read_openpyxl(source)
        if suffix == ".xls":
            return self._read_xlrd(source)
        if suffix == ".csv":
            return self._read_csv(source)
        raise ValueError(f"不支持的表格格式：{source.suffix or '无扩展名'}")

    def _read_openpyxl(self, path: Path) -> SpreadsheetReadResult:
        values_book = openpyxl.load_workbook(
            path,
            read_only=True,
            data_only=True,
            keep_links=False,
        )
        formulas_book = openpyxl.load_workbook(
            path,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        cells: list[NumericCell] = []
        issues: list[ScanIssue] = []
        try:
            for formulas_sheet in formulas_book.worksheets:
                values_sheet = values_book[formulas_sheet.title]
                hidden = formulas_sheet.sheet_state != "visible"
                missing_formula_results = 0
                for formula_row, value_row in zip_longest(
                    formulas_sheet.iter_rows(), values_sheet.iter_rows(), fillvalue=()
                ):
                    for formula_cell, value_cell in zip_longest(
                        formula_row, value_row, fillvalue=None
                    ):
                        if formula_cell is None or value_cell is None:
                            continue
                        if formula_cell.data_type == "f" and value_cell.value is None:
                            missing_formula_results += 1
                            continue
                        normalized = _numeric_value(
                            value_cell.value,
                            is_date=bool(value_cell.is_date or formula_cell.is_date),
                        )
                        if normalized is None:
                            continue
                        canonical, display = normalized
                        cells.append(
                            NumericCell(
                                source_path=str(path),
                                sheet=formulas_sheet.title,
                                row=value_cell.row,
                                column=value_cell.column,
                                coordinate=value_cell.coordinate,
                                canonical_value=canonical,
                                display_value=display,
                                hidden_sheet=hidden,
                            )
                        )
                if missing_formula_results:
                    issues.append(
                        ScanIssue(
                            str(path),
                            f"工作表 {formulas_sheet.title} 有 {missing_formula_results} 个公式"
                            "没有保存计算结果，已跳过。",
                        )
                    )
        finally:
            formulas_book.close()
            values_book.close()
        return SpreadsheetReadResult(tuple(cells), tuple(issues))

    def _read_xlrd(self, path: Path) -> SpreadsheetReadResult:
        book = xlrd.open_workbook(path, on_demand=True)
        cells: list[NumericCell] = []
        try:
            for sheet in book.sheets():
                hidden = bool(getattr(sheet, "visibility", 0))
                for row_index in range(sheet.nrows):
                    for column_index in range(sheet.ncols):
                        cell = sheet.cell(row_index, column_index)
                        if cell.ctype != xlrd.XL_CELL_NUMBER:
                            continue
                        normalized = _numeric_value(cell.value)
                        if normalized is None:
                            continue
                        canonical, display = normalized
                        cells.append(
                            NumericCell(
                                source_path=str(path),
                                sheet=sheet.name,
                                row=row_index + 1,
                                column=column_index + 1,
                                coordinate=f"{get_column_letter(column_index + 1)}{row_index + 1}",
                                canonical_value=canonical,
                                display_value=display,
                                hidden_sheet=hidden,
                            )
                        )
        finally:
            book.release_resources()
        return SpreadsheetReadResult(tuple(cells))

    def _read_csv(self, path: Path) -> SpreadsheetReadResult:
        text: str | None = None
        last_error: UnicodeError | None = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except UnicodeError as exc:
                last_error = exc
        if text is None:
            raise ValueError(f"无法识别 CSV 编码：{last_error}")

        try:
            dialect = csv.Sniffer().sniff(text[:8192])
        except csv.Error:
            dialect = csv.excel

        cells: list[NumericCell] = []
        for row_index, row in enumerate(csv.reader(text.splitlines(), dialect), start=1):
            for column_index, raw_value in enumerate(row, start=1):
                normalized = self._parse_csv_number(raw_value)
                if normalized is None:
                    continue
                canonical, display = normalized
                cells.append(
                    NumericCell(
                        source_path=str(path),
                        sheet="CSV",
                        row=row_index,
                        column=column_index,
                        coordinate=f"{get_column_letter(column_index)}{row_index}",
                        canonical_value=canonical,
                        display_value=display,
                    )
                )
        return SpreadsheetReadResult(tuple(cells))

    @staticmethod
    def _parse_csv_number(raw_value: str) -> tuple[str, str] | None:
        stripped = raw_value.strip()
        if not stripped:
            return None
        is_percentage = stripped.endswith("%")
        candidate = stripped[:-1] if is_percentage else stripped
        candidate = candidate.replace(",", "")
        try:
            value = Decimal(candidate)
        except InvalidOperation:
            return None
        if is_percentage:
            value /= Decimal(100)
        try:
            return canonical_numeric(value), stripped
        except ValueError:
            return None
