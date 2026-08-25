from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from medical_image_check.domain.models import ScanResult
from medical_image_check.engines.excel_exact import (
    SUPPORTED_SPREADSHEET_EXTENSIONS,
    ExactExcelDuplicateDetector,
)
from medical_image_check.engines.image_exact import (
    SUPPORTED_IMAGE_EXTENSIONS,
)
from medical_image_check.engines.image_similarity import ImageDuplicateDetector

ProgressCallback = Callable[[int, int, str], None]
ALGORITHM_VERSION = (
    "generic-image-local-1+western-blot-1+fluorescence-1+pathology-1+excel-advanced-1"
)


class BasicScanService:
    def __init__(
        self,
        minimum_digit_run: int = 4,
        western_single_band_enabled: bool = False,
    ) -> None:
        self._image_detector = ImageDuplicateDetector(western_single_band_enabled)
        self._excel_detector = ExactExcelDuplicateDetector(minimum_digit_run=minimum_digit_run)

    def collect_supported_files(self, sources: Iterable[str | Path]) -> tuple[Path, ...]:
        collected: set[Path] = set()
        supported = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_SPREADSHEET_EXTENSIONS
        for raw_source in sources:
            source = Path(raw_source).expanduser().resolve()
            if source.is_file() and source.suffix.lower() in supported:
                collected.add(source)
            elif source.is_dir():
                for candidate in source.rglob("*"):
                    if candidate.is_file() and candidate.suffix.lower() in supported:
                        collected.add(candidate.resolve())
        return tuple(sorted(collected, key=str))

    def scan(
        self,
        sources: Iterable[str | Path],
        progress: ProgressCallback | None = None,
    ) -> ScanResult:
        files = self.collect_supported_files(sources)
        images = tuple(path for path in files if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)
        spreadsheets = tuple(
            path for path in files if path.suffix.lower() in SUPPORTED_SPREADSHEET_EXTENSIONS
        )
        completed = 0
        total = len(files)

        def mark_complete(path: Path) -> None:
            nonlocal completed
            completed += 1
            if progress:
                progress(completed, total, f"已处理：{path.name}")

        if progress:
            progress(0, total, "正在准备扫描")

        image_findings, image_issues = self._image_detector.scan(images, mark_complete)
        excel_findings, excel_issues = self._excel_detector.scan(spreadsheets, mark_complete)
        findings = [*image_findings, *excel_findings]
        findings.sort(
            key=lambda item: (item.risk != "high", item.risk != "medium", item.finding_id)
        )
        return ScanResult(
            source_count=len(files),
            image_count=len(images),
            spreadsheet_count=len(spreadsheets),
            findings=tuple(findings),
            issues=tuple([*image_issues, *excel_issues]),
            algorithm_version=ALGORITHM_VERSION,
            completed_at=datetime.now(UTC).isoformat(),
        )
