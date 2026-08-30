from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from medical_image_check.domain.excel_settings import (
    DEFAULT_EXCEL_ABSOLUTE_TOLERANCE,
    DEFAULT_EXCEL_HIGH_RUN_LENGTH,
    DEFAULT_EXCEL_MEDIUM_RUN_LENGTH,
    DEFAULT_EXCEL_OPERATION_TARGETS,
    ExcelAnalysisSettings,
    decimal_text,
)
from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.models import ScanResult
from medical_image_check.domain.panels import PanelSelection

PROJECT_SCHEMA_VERSION = 8
DEFAULT_MINIMUM_DIGIT_RUN = 4


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    created_at: str
    updated_at: str
    source_paths: tuple[str, ...]
    minimum_digit_run: int = DEFAULT_MINIMUM_DIGIT_RUN
    western_single_band_enabled: bool = False
    image_analysis_mode: str = ImageAnalysisMode.AUTO.value
    panel_splitting_enabled: bool = False
    panel_selections: tuple[PanelSelection, ...] = ()
    excel_custom_relative_tolerance_percent: float = 0.0
    excel_absolute_tolerance: str = DEFAULT_EXCEL_ABSOLUTE_TOLERANCE
    excel_operation_targets: tuple[str, ...] = DEFAULT_EXCEL_OPERATION_TARGETS
    excel_medium_run_length: int = DEFAULT_EXCEL_MEDIUM_RUN_LENGTH
    excel_high_run_length: int = DEFAULT_EXCEL_HIGH_RUN_LENGTH
    last_scan_result: ScanResult | None = None
    report_paths: tuple[str, ...] = ()
    schema_version: int = PROJECT_SCHEMA_VERSION

    @classmethod
    def create(cls, name: str) -> Project:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("项目名称不能为空")
        now = _now_iso()
        return cls(
            project_id=str(uuid4()),
            name=normalized_name,
            created_at=now,
            updated_at=now,
            source_paths=(),
        )

    def with_sources(self, paths: list[str | Path]) -> Project:
        combined = {str(Path(path).expanduser().resolve()) for path in self.source_paths}
        combined.update(str(Path(path).expanduser().resolve()) for path in paths)
        normalized = tuple(sorted(combined))
        if normalized == self.source_paths:
            return self
        return replace(
            self,
            source_paths=normalized,
            panel_selections=(),
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def replace_sources(self, paths: list[str | Path]) -> Project:
        normalized = {str(Path(path).expanduser().resolve()) for path in paths}
        ordered = tuple(sorted(normalized))
        if ordered == self.source_paths:
            return self
        return replace(
            self,
            source_paths=ordered,
            panel_selections=(),
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_scan_result(self, result: ScanResult) -> Project:
        return replace(self, last_scan_result=result, updated_at=_now_iso())

    def with_minimum_digit_run(self, value: int) -> Project:
        if value not in range(3, 13):
            raise ValueError("连续数字片段最短长度必须在 3 到 12 之间")
        if value == self.minimum_digit_run:
            return self
        return replace(
            self,
            minimum_digit_run=value,
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_western_single_band_enabled(self, enabled: bool) -> Project:
        if enabled == self.western_single_band_enabled:
            return self
        return replace(
            self,
            western_single_band_enabled=enabled,
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_image_analysis_mode(self, mode: ImageAnalysisMode | str) -> Project:
        normalized = ImageAnalysisMode(mode).value
        if normalized == self.image_analysis_mode:
            return self
        return replace(
            self,
            image_analysis_mode=normalized,
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_panel_splitting_enabled(self, enabled: bool) -> Project:
        if enabled == self.panel_splitting_enabled:
            return self
        return replace(
            self,
            panel_splitting_enabled=enabled,
            panel_selections=(),
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_panel_selections(self, selections: tuple[PanelSelection, ...]) -> Project:
        normalized = tuple(
            replace(selection, source_path=selection.normalized_source_path)
            for selection in selections
        )
        if normalized == self.panel_selections:
            return self
        return replace(
            self,
            panel_selections=normalized,
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_excel_analysis_settings(
        self,
        custom_relative_tolerance_percent: str | float,
        absolute_tolerance: str,
        operation_targets: list[str] | tuple[str, ...],
        medium_run_length: int,
        high_run_length: int,
    ) -> Project:
        settings = ExcelAnalysisSettings.from_values(
            custom_relative_tolerance_percent,
            absolute_tolerance,
            operation_targets,
            medium_run_length,
            high_run_length,
        )
        normalized = (
            float(settings.custom_relative_tolerance_percent),
            decimal_text(settings.absolute_tolerance),
            tuple(decimal_text(value) for value in settings.operation_targets),
            settings.medium_run_length,
            settings.high_run_length,
        )
        current = (
            self.excel_custom_relative_tolerance_percent,
            self.excel_absolute_tolerance,
            self.excel_operation_targets,
            self.excel_medium_run_length,
            self.excel_high_run_length,
        )
        if normalized == current:
            return self
        return replace(
            self,
            excel_custom_relative_tolerance_percent=normalized[0],
            excel_absolute_tolerance=normalized[1],
            excel_operation_targets=normalized[2],
            excel_medium_run_length=normalized[3],
            excel_high_run_length=normalized[4],
            last_scan_result=None,
            updated_at=_now_iso(),
        )

    def with_report(self, path: str | Path) -> Project:
        reports = {str(Path(item).expanduser().resolve()) for item in self.report_paths}
        reports.add(str(Path(path).expanduser().resolve()))
        return replace(self, report_paths=tuple(sorted(reports)), updated_at=_now_iso())
