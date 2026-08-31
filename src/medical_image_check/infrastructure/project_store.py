from __future__ import annotations

import json
from pathlib import Path

from medical_image_check.domain.excel_settings import (
    DEFAULT_EXCEL_ABSOLUTE_TOLERANCE,
    DEFAULT_EXCEL_HIGH_RUN_LENGTH,
    DEFAULT_EXCEL_MEDIUM_RUN_LENGTH,
    DEFAULT_EXCEL_OPERATION_TARGETS,
    ExcelAnalysisSettings,
    decimal_text,
)
from medical_image_check.domain.image_settings import ImageAnalysisMode
from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    ReviewStatus,
    RiskLevel,
    ScanIssue,
    ScanResult,
)
from medical_image_check.domain.panels import PanelSelection
from medical_image_check.domain.performance import (
    GpuDevice,
    RuntimeEnvironment,
    ScanPerformance,
    StageTiming,
)
from medical_image_check.domain.project import PROJECT_SCHEMA_VERSION, Project


class ProjectStore:
    def save(self, project: Project, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_project_to_dict(project), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)

    def load(self, path: str | Path) -> Project:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        schema_version = payload.get("schema_version")
        if schema_version not in set(range(1, PROJECT_SCHEMA_VERSION + 1)):
            raise ValueError(
                f"不支持的项目版本：{schema_version!r}，当前版本为 {PROJECT_SCHEMA_VERSION}"
            )
        source_paths = payload.get("source_paths")
        if not isinstance(source_paths, list):
            raise ValueError("项目文件中的 source_paths 无效")
        report_paths = payload.get("report_paths", [])
        if not isinstance(report_paths, list):
            raise ValueError("项目文件中的 report_paths 无效")
        minimum_digit_run = int(payload.get("minimum_digit_run", 4))
        if minimum_digit_run not in range(3, 13):
            raise ValueError("项目文件中的 minimum_digit_run 必须在 3 到 12 之间")
        western_single_band_enabled = payload.get("western_single_band_enabled", False)
        if not isinstance(western_single_band_enabled, bool):
            raise ValueError("项目文件中的 western_single_band_enabled 必须为布尔值")
        try:
            image_analysis_mode = ImageAnalysisMode(
                payload.get("image_analysis_mode", ImageAnalysisMode.AUTO)
            ).value
        except ValueError as exc:
            raise ValueError("项目文件中的 image_analysis_mode 无效") from exc
        panel_splitting_enabled = payload.get("panel_splitting_enabled", False)
        if not isinstance(panel_splitting_enabled, bool):
            raise ValueError("项目文件中的 panel_splitting_enabled 必须为布尔值")
        panel_payload = payload.get("panel_selections", [])
        if not isinstance(panel_payload, list):
            raise ValueError("项目文件中的 panel_selections 无效")
        panel_selections = tuple(_panel_selection_from_dict(item) for item in panel_payload)
        excel_payload = payload.get("excel_analysis_settings", {})
        if not isinstance(excel_payload, dict):
            raise ValueError("项目文件中的 excel_analysis_settings 无效")
        targets_payload = excel_payload.get("operation_targets", DEFAULT_EXCEL_OPERATION_TARGETS)
        if not isinstance(targets_payload, list | tuple):
            raise ValueError("项目文件中的 Excel 运算目标必须为列表")
        excel_settings = ExcelAnalysisSettings.from_values(
            excel_payload.get("custom_relative_tolerance_percent", 0),
            excel_payload.get("absolute_tolerance", DEFAULT_EXCEL_ABSOLUTE_TOLERANCE),
            targets_payload,
            int(excel_payload.get("medium_run_length", DEFAULT_EXCEL_MEDIUM_RUN_LENGTH)),
            int(excel_payload.get("high_run_length", DEFAULT_EXCEL_HIGH_RUN_LENGTH)),
        )
        scan_payload = payload.get("last_scan_result")
        return Project(
            project_id=str(payload["project_id"]),
            name=str(payload["name"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            source_paths=tuple(str(item) for item in source_paths),
            minimum_digit_run=minimum_digit_run,
            western_single_band_enabled=western_single_band_enabled,
            image_analysis_mode=image_analysis_mode,
            panel_splitting_enabled=panel_splitting_enabled,
            panel_selections=panel_selections,
            excel_custom_relative_tolerance_percent=float(
                excel_settings.custom_relative_tolerance_percent
            ),
            excel_absolute_tolerance=decimal_text(excel_settings.absolute_tolerance),
            excel_operation_targets=tuple(
                decimal_text(value) for value in excel_settings.operation_targets
            ),
            excel_medium_run_length=excel_settings.medium_run_length,
            excel_high_run_length=excel_settings.high_run_length,
            last_scan_result=_scan_result_from_dict(scan_payload) if scan_payload else None,
            report_paths=tuple(str(item) for item in report_paths),
            schema_version=PROJECT_SCHEMA_VERSION,
        )


def _project_to_dict(project: Project) -> dict[str, object]:
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project.project_id,
        "name": project.name,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "source_paths": list(project.source_paths),
        "minimum_digit_run": project.minimum_digit_run,
        "western_single_band_enabled": project.western_single_band_enabled,
        "image_analysis_mode": project.image_analysis_mode,
        "panel_splitting_enabled": project.panel_splitting_enabled,
        "panel_selections": [
            {
                "source_path": selection.source_path,
                "page": selection.page,
                "panel_index": selection.panel_index,
                "x": selection.x,
                "y": selection.y,
                "width": selection.width,
                "height": selection.height,
                "selected": selection.selected,
            }
            for selection in project.panel_selections
        ],
        "excel_analysis_settings": {
            "custom_relative_tolerance_percent": (project.excel_custom_relative_tolerance_percent),
            "absolute_tolerance": project.excel_absolute_tolerance,
            "operation_targets": list(project.excel_operation_targets),
            "medium_run_length": project.excel_medium_run_length,
            "high_run_length": project.excel_high_run_length,
        },
        "last_scan_result": (
            _scan_result_to_dict(project.last_scan_result) if project.last_scan_result else None
        ),
        "report_paths": list(project.report_paths),
    }


def _panel_selection_from_dict(payload: object) -> PanelSelection:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的子面板选择无效")
    try:
        selected = payload.get("selected", True)
        if not isinstance(selected, bool):
            raise ValueError("selected 必须为布尔值")
        return PanelSelection(
            source_path=str(payload["source_path"]),
            page=int(payload["page"]),
            panel_index=int(payload["panel_index"]),
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            selected=selected,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("项目文件中的子面板选择无效") from exc


def _scan_result_to_dict(result: ScanResult) -> dict[str, object]:
    return {
        "source_count": result.source_count,
        "image_count": result.image_count,
        "spreadsheet_count": result.spreadsheet_count,
        "findings": [_finding_to_dict(finding) for finding in result.findings],
        "issues": [
            {
                "source_path": issue.source_path,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in result.issues
        ],
        "algorithm_version": result.algorithm_version,
        "completed_at": result.completed_at,
        "performance": (
            _performance_to_dict(result.performance) if result.performance is not None else None
        ),
    }


def _finding_to_dict(finding: Finding) -> dict[str, object]:
    return {
        "finding_id": finding.finding_id,
        "rule_id": finding.rule_id,
        "finding_type": finding.finding_type.value,
        "risk": finding.risk.value,
        "title": finding.title,
        "description": finding.description,
        "locations": [
            {
                "source_path": location.source_path,
                "sheet": location.sheet,
                "coordinate": location.coordinate,
                "hidden_sheet": location.hidden_sheet,
            }
            for location in finding.locations
        ],
        "confidence": finding.confidence,
        "details": finding.details,
        "review_status": finding.review_status.value,
    }


def _scan_result_from_dict(payload: object) -> ScanResult:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的 last_scan_result 无效")
    findings_payload = payload.get("findings", [])
    issues_payload = payload.get("issues", [])
    if not isinstance(findings_payload, list) or not isinstance(issues_payload, list):
        raise ValueError("项目文件中的扫描结果列表无效")
    return ScanResult(
        source_count=int(payload["source_count"]),
        image_count=int(payload["image_count"]),
        spreadsheet_count=int(payload["spreadsheet_count"]),
        findings=tuple(_finding_from_dict(item) for item in findings_payload),
        issues=tuple(_issue_from_dict(item) for item in issues_payload),
        algorithm_version=str(payload.get("algorithm_version", "exact-baseline-1")),
        completed_at=(str(payload["completed_at"]) if payload.get("completed_at") else None),
        performance=(
            _performance_from_dict(payload["performance"])
            if payload.get("performance") is not None
            else None
        ),
    )


def _performance_to_dict(performance: ScanPerformance) -> dict[str, object]:
    environment = performance.environment
    return {
        "schema_version": performance.schema_version,
        "selected_backend": performance.selected_backend,
        "accelerator_status": performance.accelerator_status,
        "wall_seconds": performance.wall_seconds,
        "active_seconds": performance.active_seconds,
        "paused_seconds": performance.paused_seconds,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "duration_seconds": stage.duration_seconds,
                "calls": stage.calls,
                "items": stage.items,
            }
            for stage in performance.stages
        ],
        "environment": {
            "operating_system": environment.operating_system,
            "os_release": environment.os_release,
            "machine": environment.machine,
            "processor": environment.processor,
            "logical_cpu_count": environment.logical_cpu_count,
            "python_version": environment.python_version,
            "opencv_version": environment.opencv_version,
            "nvidia_gpus": [
                {
                    "name": device.name,
                    "driver_version": device.driver_version,
                    "memory_total_mb": device.memory_total_mb,
                }
                for device in environment.nvidia_gpus
            ],
            "nvidia_probe_error": environment.nvidia_probe_error,
            "opencv_cuda_device_count": environment.opencv_cuda_device_count,
            "opencv_cuda_probe_error": environment.opencv_cuda_probe_error,
        },
    }


def _performance_from_dict(payload: object) -> ScanPerformance:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的性能画像无效")
    stages_payload = payload.get("stages", [])
    environment_payload = payload.get("environment")
    if not isinstance(stages_payload, list) or not isinstance(environment_payload, dict):
        raise ValueError("项目文件中的性能画像列表无效")
    gpu_payload = environment_payload.get("nvidia_gpus", [])
    if not isinstance(gpu_payload, list):
        raise ValueError("项目文件中的 GPU 列表无效")
    environment = RuntimeEnvironment(
        operating_system=str(environment_payload.get("operating_system", "unknown")),
        os_release=str(environment_payload.get("os_release", "unknown")),
        machine=str(environment_payload.get("machine", "unknown")),
        processor=str(environment_payload.get("processor", "unknown")),
        logical_cpu_count=(
            int(environment_payload["logical_cpu_count"])
            if environment_payload.get("logical_cpu_count") is not None
            else None
        ),
        python_version=str(environment_payload.get("python_version", "unknown")),
        opencv_version=str(environment_payload.get("opencv_version", "unknown")),
        nvidia_gpus=tuple(_gpu_from_dict(item) for item in gpu_payload),
        nvidia_probe_error=(
            str(environment_payload["nvidia_probe_error"])
            if environment_payload.get("nvidia_probe_error") is not None
            else None
        ),
        opencv_cuda_device_count=int(environment_payload.get("opencv_cuda_device_count", 0)),
        opencv_cuda_probe_error=(
            str(environment_payload["opencv_cuda_probe_error"])
            if environment_payload.get("opencv_cuda_probe_error") is not None
            else None
        ),
    )
    return ScanPerformance(
        schema_version=int(payload.get("schema_version", 1)),
        selected_backend=str(payload.get("selected_backend", "cpu")),
        accelerator_status=str(payload.get("accelerator_status", "unknown")),
        wall_seconds=float(payload.get("wall_seconds", 0)),
        active_seconds=float(payload.get("active_seconds", 0)),
        paused_seconds=float(payload.get("paused_seconds", 0)),
        stages=tuple(_stage_from_dict(item) for item in stages_payload),
        environment=environment,
    )


def _gpu_from_dict(payload: object) -> GpuDevice:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的 GPU 信息无效")
    return GpuDevice(
        name=str(payload.get("name", "unknown")),
        driver_version=(
            str(payload["driver_version"]) if payload.get("driver_version") is not None else None
        ),
        memory_total_mb=(
            int(payload["memory_total_mb"]) if payload.get("memory_total_mb") is not None else None
        ),
    )


def _stage_from_dict(payload: object) -> StageTiming:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的性能阶段无效")
    return StageTiming(
        stage_id=str(payload.get("stage_id", "unknown")),
        duration_seconds=float(payload.get("duration_seconds", 0)),
        calls=int(payload.get("calls", 0)),
        items=int(payload.get("items", 0)),
    )


def _finding_from_dict(payload: object) -> Finding:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的查重结果无效")
    locations_payload = payload.get("locations", [])
    details = payload.get("details", {})
    if not isinstance(locations_payload, list) or not isinstance(details, dict):
        raise ValueError("项目文件中的结果证据无效")
    return Finding(
        finding_id=str(payload["finding_id"]),
        rule_id=str(payload["rule_id"]),
        finding_type=FindingType(str(payload["finding_type"])),
        risk=RiskLevel(str(payload["risk"])),
        title=str(payload["title"]),
        description=str(payload["description"]),
        locations=tuple(_location_from_dict(item) for item in locations_payload),
        confidence=float(payload.get("confidence", 1.0)),
        details={str(key): value for key, value in details.items()},
        review_status=ReviewStatus(str(payload.get("review_status", ReviewStatus.PENDING))),
    )


def _location_from_dict(payload: object) -> EvidenceLocation:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的证据位置无效")
    return EvidenceLocation(
        source_path=str(payload["source_path"]),
        sheet=str(payload["sheet"]) if payload.get("sheet") is not None else None,
        coordinate=(str(payload["coordinate"]) if payload.get("coordinate") is not None else None),
        hidden_sheet=bool(payload.get("hidden_sheet", False)),
    )


def _issue_from_dict(payload: object) -> ScanIssue:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的扫描提示无效")
    return ScanIssue(
        source_path=str(payload["source_path"]),
        message=str(payload["message"]),
        severity=str(payload.get("severity", "warning")),
    )
