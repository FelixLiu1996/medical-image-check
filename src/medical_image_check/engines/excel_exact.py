from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

from medical_image_check.domain.excel_settings import ExcelAnalysisSettings
from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    deterministic_finding_id,
)
from medical_image_check.engines.excel_advanced import find_advanced_excel_findings
from medical_image_check.engines.excel_result_quality import improve_excel_result_quality
from medical_image_check.engines.excel_semantics import filter_low_information_cells
from medical_image_check.infrastructure.performance import (
    PerformanceRecorder,
    profile_stage,
    record_items,
)
from medical_image_check.infrastructure.spreadsheets import (
    NumericCell,
    SpreadsheetReader,
    canonical_digit_string,
)

SUPPORTED_SPREADSHEET_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".csv"})
DEFAULT_MINIMUM_DIGIT_RUN = 4
MINIMUM_DIGIT_RUN_RANGE = range(3, 13)
MAX_FRAGMENT_EVIDENCE_LENGTH = 128
MAX_DIGIT_FRAGMENT_FINDINGS = 300


class ExactExcelDuplicateDetector:
    value_rule_id = "excel.value.exact"
    row_rule_id = "excel.row.exact"
    digit_fragment_rule_id = "excel.digit_fragment"

    def __init__(
        self,
        reader: SpreadsheetReader | None = None,
        minimum_digit_run: int = DEFAULT_MINIMUM_DIGIT_RUN,
        analysis_settings: ExcelAnalysisSettings | None = None,
    ) -> None:
        if minimum_digit_run not in MINIMUM_DIGIT_RUN_RANGE:
            raise ValueError("连续数字片段最短长度必须在 3 到 12 之间")
        self._reader = reader or SpreadsheetReader()
        self.minimum_digit_run = minimum_digit_run
        self.analysis_settings = analysis_settings or ExcelAnalysisSettings()

    def scan(
        self,
        paths: Iterable[Path],
        on_file: Callable[[Path], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
        profiler: PerformanceRecorder | None = None,
    ) -> tuple[list[Finding], list[ScanIssue]]:
        cells: list[NumericCell] = []
        issues: list[ScanIssue] = []

        for path in paths:
            if checkpoint:
                checkpoint()
            try:
                with profile_stage(profiler, "spreadsheet.read"):
                    result = self._reader.read(path)
                    cells.extend(result.cells)
                    issues.extend(result.issues)
                record_items(profiler, "spreadsheet.read", len(result.cells))
            # Third-party readers raise different exception types for corrupt,
            # encrypted, and partially written workbooks. One bad file must not
            # abort the remaining batch.
            except Exception as exc:  # noqa: BLE001
                issues.append(ScanIssue(str(path), f"无法读取表格：{exc}", "error"))
            finally:
                if on_file:
                    on_file(path)

        if checkpoint:
            checkpoint()
        analysis_cells = filter_low_information_cells(cells)
        with profile_stage(profiler, "spreadsheet.exact_values"):
            findings = self._find_value_duplicates(analysis_cells)
        record_items(profiler, "spreadsheet.exact_values", len(findings))
        with profile_stage(profiler, "spreadsheet.exact_rows"):
            row_findings = self._find_row_duplicates(analysis_cells)
            findings.extend(row_findings)
        record_items(profiler, "spreadsheet.exact_rows", len(row_findings))
        if checkpoint:
            checkpoint()
        with profile_stage(profiler, "spreadsheet.digit_fragments"):
            digit_findings = self._find_digit_fragment_duplicates(analysis_cells)
            findings.extend(digit_findings)
        record_items(profiler, "spreadsheet.digit_fragments", len(digit_findings))
        if checkpoint:
            checkpoint()
        with profile_stage(profiler, "spreadsheet.advanced_rules"):
            advanced_findings = find_advanced_excel_findings(
                analysis_cells,
                self.analysis_settings,
                checkpoint,
            )
            findings.extend(advanced_findings)
        record_items(profiler, "spreadsheet.advanced_rules", len(advanced_findings))
        if checkpoint:
            checkpoint()
        with profile_stage(profiler, "spreadsheet.result_sort"):
            findings = improve_excel_result_quality(findings)
            findings.sort(
                key=lambda item: (
                    {"primary": 0, "secondary": 1, "normal": 2}.get(
                        str(item.details.get("attention_tier", "secondary")), 1
                    ),
                    {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2}[item.risk],
                    -int(item.details.get("matched_count", item.details.get("maximum_length", 0))),
                    -item.confidence,
                    item.finding_id,
                )
            )
        record_items(profiler, "spreadsheet.result_sort", len(findings))
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
            values = {value for _, value in signature}
            if len(signature) >= 2 and len(values) >= 2 and values != {"0", "1"}:
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

    def _find_digit_fragment_duplicates(self, cells: list[NumericCell]) -> list[Finding]:
        cells_by_value: dict[str, list[NumericCell]] = defaultdict(list)
        for cell in cells:
            cells_by_value[cell.canonical_value].append(cell)

        value_groups = [
            (canonical_value, canonical_digit_string(canonical_value), grouped_cells)
            for canonical_value, grouped_cells in sorted(cells_by_value.items())
            if len(canonical_digit_string(canonical_value)) >= self.minimum_digit_run
        ]
        if len(value_groups) < 2:
            return []

        fragments_by_group: dict[tuple[int, ...], set[str]] = {}
        index_lengths = (
            range(8, self.minimum_digit_run - 1, -1)
            if self.minimum_digit_run <= 8
            else (self.minimum_digit_run,)
        )
        for length in index_lengths:
            index: dict[str, list[int]] = defaultdict(list)
            for group_index, (_, digits, _) in enumerate(value_groups):
                if len(digits) < length:
                    continue
                for fragment in {
                    digits[start : start + length] for start in range(len(digits) - length + 1)
                }:
                    index[fragment].append(group_index)

            for group_indexes in index.values():
                if len(group_indexes) < 2:
                    continue
                group_key = tuple(group_indexes)
                if group_key in fragments_by_group:
                    continue
                digit_strings = [value_groups[index][1] for index in group_key]
                fragments_by_group[group_key] = set(
                    _longest_common_digit_fragments(digit_strings, length)
                )

        findings: list[Finding] = []
        for group_indexes, fragments in fragments_by_group.items():
            if not fragments:
                continue
            grouped_cells = sorted(
                (cell for group_index in group_indexes for cell in value_groups[group_index][2]),
                key=lambda cell: (cell.source_path, cell.sheet, cell.row, cell.column),
            )
            locations = tuple(cell.location for cell in grouped_cells)
            longest = max(len(fragment) for fragment in fragments)
            risk = _digit_fragment_risk(longest)
            confidence = 0.9 if risk == RiskLevel.HIGH else 0.7 if risk == RiskLevel.MEDIUM else 0.5
            ordered_fragments = sorted(fragments)
            preview = "、".join(ordered_fragments[:3])
            if len(ordered_fragments) > 3:
                preview += f" 等 {len(ordered_fragments)} 个"
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(
                        self.digit_fragment_rule_id,
                        locations,
                    ),
                    rule_id=self.digit_fragment_rule_id,
                    finding_type=FindingType.SUSPECTED_REUSE,
                    risk=risk,
                    title="数值包含连续重复数字片段",
                    description=(
                        f"{len(locations)} 个单元格中的不同完整数值共享连续数字片段：{preview}。"
                    ),
                    locations=locations,
                    confidence=confidence,
                    details={
                        "fragments": ordered_fragments,
                        "maximum_length": longest,
                        "minimum_configured_length": self.minimum_digit_run,
                        "cell_count": len(grouped_cells),
                        "distinct_value_count": len(group_indexes),
                        "cells": [
                            {
                                "source_path": cell.source_path,
                                "sheet": cell.sheet,
                                "coordinate": cell.coordinate,
                                "canonical_value": cell.canonical_value,
                                "display_value": cell.display_value,
                                "hidden_sheet": cell.hidden_sheet,
                            }
                            for cell in grouped_cells
                        ],
                    },
                )
            )
        findings.sort(
            key=lambda item: (
                -int(item.details["maximum_length"]),
                -int(item.details["distinct_value_count"]),
                -int(item.details["cell_count"]),
                item.finding_id,
            )
        )
        return findings[:MAX_DIGIT_FRAGMENT_FINDINGS]


def _longest_common_digit_fragments(
    digit_strings: list[str],
    minimum_length: int,
) -> tuple[str, ...]:
    shortest = min(digit_strings, key=lambda value: (len(value), value))
    others = list(digit_strings)
    others.remove(shortest)
    lower = minimum_length
    upper = min(len(shortest), MAX_FRAGMENT_EVIDENCE_LENGTH)
    best: set[str] = set()
    while lower <= upper:
        length = (lower + upper) // 2
        common = {shortest[start : start + length] for start in range(len(shortest) - length + 1)}
        for digits in others:
            common.intersection_update(
                digits[start : start + length] for start in range(len(digits) - length + 1)
            )
            if not common:
                break
        if common:
            best = common
            lower = length + 1
        else:
            upper = length - 1
    return tuple(sorted(best))


def _digit_fragment_risk(length: int) -> RiskLevel:
    if length >= 8:
        return RiskLevel.HIGH
    if length >= 5:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
