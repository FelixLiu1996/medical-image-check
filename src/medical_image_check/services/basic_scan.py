from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock
from time import perf_counter

from medical_image_check.domain.excel_settings import (
    DEFAULT_EXCEL_ABSOLUTE_TOLERANCE,
    DEFAULT_EXCEL_HIGH_RUN_LENGTH,
    DEFAULT_EXCEL_MEDIUM_RUN_LENGTH,
    DEFAULT_EXCEL_OPERATION_TARGETS,
    ExcelAnalysisSettings,
)
from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.models import ScanResult
from medical_image_check.engines.excel_exact import (
    SUPPORTED_SPREADSHEET_EXTENSIONS,
    ExactExcelDuplicateDetector,
)
from medical_image_check.engines.image_exact import (
    SUPPORTED_IMAGE_EXTENSIONS,
)
from medical_image_check.engines.image_similarity import ImageDuplicateDetector
from medical_image_check.infrastructure.performance import (
    PerformanceRecorder,
    detect_runtime_environment,
    profile_stage,
    record_items,
)

ProgressCallback = Callable[[int, int, str], None]
ALGORITHM_VERSION = (
    "generic-image-local-1+western-blot-1+dot-blot-1+fluorescence-1+pathology-2+excel-advanced-4"
)


class ScanMode(StrEnum):
    ALL = "all"
    IMAGE = "image"
    DATA = "data"


class ScanCancelled(Exception):
    """Raised at a cooperative checkpoint when the user cancels a scan."""


class ScanControl:
    def __init__(self) -> None:
        self._cancelled = Event()
        self._running = Event()
        self._running.set()
        self._pause_lock = Lock()
        self._pause_started_at: float | None = None
        self._completed_pause_seconds = 0.0

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set() and not self.cancelled

    @property
    def paused_seconds(self) -> float:
        with self._pause_lock:
            total = self._completed_pause_seconds
            if self._pause_started_at is not None:
                total += max(0.0, perf_counter() - self._pause_started_at)
            return total

    def pause(self) -> None:
        if not self.cancelled:
            with self._pause_lock:
                if self._pause_started_at is None:
                    self._pause_started_at = perf_counter()
            self._running.clear()

    def resume(self) -> None:
        self._finish_pause()
        self._running.set()

    def cancel(self) -> None:
        self._finish_pause()
        self._cancelled.set()
        self._running.set()

    def _finish_pause(self) -> None:
        with self._pause_lock:
            if self._pause_started_at is None:
                return
            self._completed_pause_seconds += max(0.0, perf_counter() - self._pause_started_at)
            self._pause_started_at = None

    def checkpoint(self) -> None:
        if self.cancelled:
            raise ScanCancelled("扫描已由用户取消")
        while not self._running.wait(timeout=0.1):
            if self.cancelled:
                raise ScanCancelled("扫描已由用户取消")
        if self.cancelled:
            raise ScanCancelled("扫描已由用户取消")


class BasicScanService:
    def __init__(
        self,
        minimum_digit_run: int = 4,
        western_single_band_enabled: bool = False,
        image_analysis_mode: ImageAnalysisMode | str = ImageAnalysisMode.AUTO,
        excel_custom_relative_tolerance_percent: str | float = 0,
        excel_absolute_tolerance: str = DEFAULT_EXCEL_ABSOLUTE_TOLERANCE,
        excel_operation_targets: tuple[str, ...] = DEFAULT_EXCEL_OPERATION_TARGETS,
        excel_medium_run_length: int = DEFAULT_EXCEL_MEDIUM_RUN_LENGTH,
        excel_high_run_length: int = DEFAULT_EXCEL_HIGH_RUN_LENGTH,
        scan_mode: ScanMode | str = ScanMode.ALL,
    ) -> None:
        self.scan_mode = ScanMode(scan_mode)
        self._image_detector = (
            ImageDuplicateDetector(western_single_band_enabled, image_analysis_mode)
            if self.scan_mode != ScanMode.DATA
            else None
        )
        self._excel_detector = None
        if self.scan_mode != ScanMode.IMAGE:
            excel_settings = ExcelAnalysisSettings.from_values(
                excel_custom_relative_tolerance_percent,
                excel_absolute_tolerance,
                excel_operation_targets,
                excel_medium_run_length,
                excel_high_run_length,
            )
            self._excel_detector = ExactExcelDuplicateDetector(
                minimum_digit_run=minimum_digit_run,
                analysis_settings=excel_settings,
            )

    def collect_supported_files(
        self,
        sources: Iterable[str | Path],
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[Path, ...]:
        collected: set[Path] = set()
        if self.scan_mode == ScanMode.IMAGE:
            supported = SUPPORTED_IMAGE_EXTENSIONS
        elif self.scan_mode == ScanMode.DATA:
            supported = SUPPORTED_SPREADSHEET_EXTENSIONS
        else:
            supported = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_SPREADSHEET_EXTENSIONS
        for raw_source in sources:
            if checkpoint:
                checkpoint()
            source = Path(raw_source).expanduser().resolve()
            if source.is_file() and source.suffix.lower() in supported:
                collected.add(source)
            elif source.is_dir():
                for candidate_index, candidate in enumerate(source.rglob("*")):
                    if checkpoint and candidate_index % 256 == 0:
                        checkpoint()
                    if candidate.is_file() and candidate.suffix.lower() in supported:
                        collected.add(candidate.resolve())
        return tuple(sorted(collected, key=str))

    def scan(
        self,
        sources: Iterable[str | Path],
        progress: ProgressCallback | None = None,
        control: ScanControl | None = None,
    ) -> ScanResult:
        scan_control = control or ScanControl()
        environment = detect_runtime_environment()
        profiler = PerformanceRecorder(lambda: scan_control.paused_seconds)
        scan_control.checkpoint()
        with profile_stage(profiler, "source.collect"):
            files = self.collect_supported_files(sources, scan_control.checkpoint)
        record_items(profiler, "source.collect", len(files))
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
            scan_control.checkpoint()

        if progress:
            progress(0, total, "正在准备扫描")

        image_findings, image_issues = ([], [])
        if self.scan_mode != ScanMode.DATA:
            assert self._image_detector is not None
            image_findings, image_issues = self._image_detector.scan(
                images,
                mark_complete,
                scan_control.checkpoint,
                profiler,
            )
        scan_control.checkpoint()
        if progress and images and self.scan_mode == ScanMode.ALL:
            progress(completed, total, "图片候选验证完成，正在处理表格")
        excel_findings, excel_issues = ([], [])
        if self.scan_mode != ScanMode.IMAGE:
            assert self._excel_detector is not None
            excel_findings, excel_issues = self._excel_detector.scan(
                spreadsheets,
                mark_complete,
                scan_control.checkpoint,
                profiler,
            )
        scan_control.checkpoint()
        if progress:
            progress(completed, total, "正在整理扫描结果")
        with profile_stage(profiler, "result.sort"):
            findings = [*image_findings, *excel_findings]
            findings.sort(
                key=lambda item: (
                    item.risk != "high",
                    item.risk != "medium",
                    -int(
                        item.details.get("matched_count", item.details.get("matched_spot_count", 0))
                    ),
                    item.finding_id,
                )
            )
        record_items(profiler, "result.sort", len(findings))
        return ScanResult(
            source_count=len(files),
            image_count=len(images),
            spreadsheet_count=len(spreadsheets),
            findings=tuple(findings),
            issues=tuple([*image_issues, *excel_issues]),
            algorithm_version=ALGORITHM_VERSION,
            completed_at=datetime.now(UTC).isoformat(),
            performance=profiler.finish(environment),
        )
