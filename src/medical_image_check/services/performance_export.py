from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from medical_image_check import __version__
from medical_image_check.domain.models import ScanResult
from medical_image_check.domain.performance import RuntimeEnvironment, ScanPerformance, StageTiming
from medical_image_check.domain.project import Project

STAGE_LABELS = {
    "source.collect": "收集支持的输入文件",
    "image.read_decode": "读取并解码图片",
    "image.generic_features": "提取通用图片特征",
    "image.dot_blot_routing": "AUTO Dot blot 页面准入",
    "image.dot_blot_features": "提取 Dot blot 特征",
    "image.fluorescence_features": "提取荧光图特征",
    "image.pathology_features": "提取病理图特征",
    "image.western_features": "提取 Western blot 特征",
    "image.exact_verification": "验证图片文件与像素重复",
    "image.perceptual_verification": "验证图片整体近似",
    "image.generic_candidate_selection": "补充小图候选预筛",
    "image.local_geometric_verification": "验证图片局部几何重叠",
    "image.small_content_verification": "验证小区域细节复用",
    "image.dot_blot_verification": "验证 Dot blot 候选",
    "image.fluorescence_verification": "验证荧光图候选",
    "image.pathology_verification": "验证病理图候选",
    "image.western_verification": "验证 Western blot 候选",
    "image.result_deduplication": "合并图片专项结果",
    "spreadsheet.read": "读取表格数值",
    "spreadsheet.exact_values": "检查完整数值重复",
    "spreadsheet.exact_rows": "检查完整数值行重复",
    "spreadsheet.digit_fragments": "检查连续数字片段",
    "spreadsheet.advanced_rules": "检查表格高级规则",
    "spreadsheet.result_sort": "整理表格结果",
    "result.sort": "整理全部扫描结果",
}

ACCELERATOR_STATUS_LABELS = {
    "opencv_cuda_available_not_selected": "检测到 OpenCV CUDA 设备，但当前版本仍选择 CPU",
    "nvidia_hardware_detected_opencv_cuda_unavailable": (
        "检测到 NVIDIA GPU，但当前 OpenCV 运行时不包含 CUDA 后端"
    ),
    "nvidia_probe_failed_cpu_selected": "NVIDIA GPU 探测失败，当前选择 CPU",
    "no_nvidia_gpu_detected": "未检测到 NVIDIA GPU，当前选择 CPU",
}


class PerformanceExporter:
    def export(
        self,
        result: ScanResult,
        destination: str | Path,
        project: Project | None = None,
    ) -> Path:
        if result.performance is None:
            raise ValueError("当前结果不包含性能诊断数据，请使用此版本重新扫描")
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".json":
            output = output.with_suffix(".json")
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = _diagnostic_payload(result, result.performance, project)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(output)
        return output


def _diagnostic_payload(
    result: ScanResult,
    performance: ScanPerformance,
    project: Project | None,
) -> dict[str, object]:
    active = max(performance.active_seconds, 1e-12)
    stages = sorted(performance.stages, key=lambda stage: stage.duration_seconds, reverse=True)
    return {
        "diagnostic_schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "software_version": __version__,
        "algorithm_version": result.algorithm_version,
        "project": ({"project_id": project.project_id, "name": project.name} if project else None),
        "privacy": {
            "raw_files_included": False,
            "source_paths_included": False,
            "finding_evidence_included": False,
        },
        "scan": {
            "completed_at": result.completed_at,
            "source_count": result.source_count,
            "image_count": result.image_count,
            "spreadsheet_count": result.spreadsheet_count,
            "finding_count": len(result.findings),
            "issue_count": len(result.issues),
        },
        "performance": {
            "schema_version": performance.schema_version,
            "selected_backend": performance.selected_backend,
            "accelerator_status": performance.accelerator_status,
            "accelerator_status_label": ACCELERATOR_STATUS_LABELS.get(
                performance.accelerator_status,
                performance.accelerator_status,
            ),
            "wall_seconds": performance.wall_seconds,
            "active_seconds": performance.active_seconds,
            "paused_seconds": performance.paused_seconds,
            "stages": [
                _stage_payload(stage, active, rank) for rank, stage in enumerate(stages, start=1)
            ],
        },
        "environment": _environment_payload(performance.environment),
    }


def _stage_payload(stage: StageTiming, active_seconds: float, rank: int) -> dict[str, object]:
    return {
        "rank_by_duration": rank,
        "stage_id": stage.stage_id,
        "label": STAGE_LABELS.get(stage.stage_id, stage.stage_id),
        "duration_seconds": stage.duration_seconds,
        "active_time_percent": round(100 * stage.duration_seconds / active_seconds, 4),
        "calls": stage.calls,
        "items": stage.items,
    }


def _environment_payload(environment: RuntimeEnvironment) -> dict[str, object]:
    return {
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
    }
