from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    deterministic_finding_id,
)
from medical_image_check.infrastructure.spreadsheets import NumericCell, SpreadsheetReader

SUPPORTED_SPREADSHEET_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".csv"})


class ExactExcelDuplicateDetector:
    value_rule_id = "excel.value.exact"
    row_rule_id = "excel.row.exact"

    def __init__(self, reader: SpreadsheetReader | None = None) -> None:
        self._reader = reader or SpreadsheetReader()

    def scan(
        self,
        paths: Iterable[Path],
        on_file: Callable[[Path], None] | None = None,
    ) -> tuple[list[Finding], list[ScanIssue]]:
        cells: list[NumericCell] = []
        issues: list[ScanIssue] = []

        for path in paths:
            try:
                result = self._reader.read(path)
                cells.extend(result.cells)
                issues.extend(result.issues)
            # Third-party readers raise different exception types for corrupt,
            # encrypted, and partially written workbooks. One bad file must not
            # abort the remaining batch.
            except Exception as exc:  # noqa: BLE001
                issues.append(ScanIssue(str(path), f"无法读取表格：{exc}", "error"))
            finally:
                if on_file:
                    on_file(path)

        findings = self._find_value_duplicates(cells)
        findings.extend(self._find_row_duplicates(cells))
        findings.sort(
            key=lambda item: (
                {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2}[item.risk],
                item.finding_id,
            )
        )
        return findings, issues

    def _find_value_duplicates(self, cells: list[NumericCell]) -> list[Finding]:
        grouped: dict[str, list[NumericCell]] = defaultdict(list)
        for cell in cells:
            if cell.canonical_value in {"0", "1"}:
                continue
            grouped[cell.canonical_value].append(cell)

        findings: list[Finding] = []
        for value, duplicate_cells in grouped.items():
            if len(duplicate_cells) < 2:
                continue
            locations = tuple(cell.location for cell in duplicate_cells)
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.value_rule_id, locations),
                    rule_id=self.value_rule_id,
                    finding_type=FindingType.EXACT_DUPLICATE,
                    risk=RiskLevel.LOW,
                    title="数值完全相同",
                    description=f"完整数值 {value} 在 {len(locations)} 个单元格中出现。",
                    locations=locations,
                    details={"value": value, "count": len(locations)},
                )
            )
        return findings

    def _find_row_duplicates(self, cells: list[NumericCell]) -> list[Finding]:
        rows: dict[tuple[str, str, int], list[NumericCell]] = defaultdict(list)
        for cell in cells:
            rows[(cell.source_path, cell.sheet, cell.row)].append(cell)

        signatures: dict[tuple[tuple[int, str], ...], list[list[NumericCell]]] = defaultdict(list)
        for row_cells in rows.values():
            signature = tuple(sorted((cell.column, cell.canonical_value) for cell in row_cells))
            if len(signature) >= 2:
                signatures[signature].append(row_cells)

        findings: list[Finding] = []
        for signature, duplicate_rows in signatures.items():
            if len(duplicate_rows) < 2:
                continue
            locations = tuple(
                EvidenceLocation(
                    source_path=row_cells[0].source_path,
                    sheet=row_cells[0].sheet,
                    coordinate=f"第 {row_cells[0].row} 行",
                    hidden_sheet=row_cells[0].hidden_sheet,
                )
                for row_cells in duplicate_rows
            )
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.row_rule_id, locations),
                    rule_id=self.row_rule_id,
                    finding_type=FindingType.EXACT_DUPLICATE,
                    risk=RiskLevel.HIGH,
                    title="数值行完全重复",
                    description=f"{len(locations)} 行包含相同位置和完整数值。",
                    locations=locations,
                    details={"numeric_cell_count": len(signature), "row_count": len(locations)},
                )
            )
        return findings
