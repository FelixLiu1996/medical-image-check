from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from medical_image_check.domain.models import ScanResult

PROJECT_SCHEMA_VERSION = 3
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

    def with_report(self, path: str | Path) -> Project:
        reports = {str(Path(item).expanduser().resolve()) for item in self.report_paths}
        reports.add(str(Path(path).expanduser().resolve()))
        return replace(self, report_paths=tuple(sorted(reports)), updated_at=_now_iso())
