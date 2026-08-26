import json
import subprocess
from pathlib import Path

import pytest

from medical_image_check.domain.models import ScanResult
from medical_image_check.domain.performance import (
    GpuDevice,
    RuntimeEnvironment,
    ScanPerformance,
    StageTiming,
)
from medical_image_check.domain.project import Project
from medical_image_check.infrastructure.performance import (
    PerformanceRecorder,
    detect_runtime_environment,
    parse_nvidia_smi_output,
)
from medical_image_check.services.performance_export import PerformanceExporter


def _environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        operating_system="Windows",
        os_release="11",
        machine="AMD64",
        processor="test-cpu",
        logical_cpu_count=16,
        python_version="3.12.13",
        opencv_version="4.14.0",
        nvidia_gpus=(GpuDevice("NVIDIA GeForce RTX 3080 Ti", "999.1", 12288),),
        opencv_cuda_device_count=0,
    )


def test_performance_recorder_excludes_paused_time_and_accumulates_stages() -> None:
    state = {"clock": 0.0, "paused": 0.0}
    recorder = PerformanceRecorder(
        paused_seconds=lambda: state["paused"],
        clock=lambda: state["clock"],
    )

    with recorder.stage("image.generic_features"):
        state["clock"] = 5.0
        state["paused"] = 1.0
    recorder.add_items("image.generic_features", 10)
    with recorder.stage("image.generic_features"):
        state["clock"] = 8.0
    recorder.add_items("image.generic_features", 5)

    profile = recorder.finish(_environment())

    assert profile.wall_seconds == 8.0
    assert profile.active_seconds == 7.0
    assert profile.paused_seconds == 1.0
    assert profile.accelerator_status == "nvidia_hardware_detected_opencv_cuda_unavailable"
    assert profile.stages == (StageTiming("image.generic_features", 7.0, 2, 15),)


def test_parse_nvidia_smi_output_supports_multiple_devices_and_missing_memory() -> None:
    devices = parse_nvidia_smi_output(
        "NVIDIA GeForce RTX 3080 Ti, 591.00, 12288\nSecond GPU, 591.00, unknown\n"
    )

    assert devices == (
        GpuDevice("NVIDIA GeForce RTX 3080 Ti", "591.00", 12288),
        GpuDevice("Second GPU", "591.00", None),
    )


def test_runtime_probe_reads_nvidia_smi_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="NVIDIA GeForce RTX 3080 Ti, 591.00, 12288\n",
            stderr="",
        )

    monkeypatch.setattr("medical_image_check.infrastructure.performance.subprocess.run", fake_run)

    environment = detect_runtime_environment()

    assert environment.nvidia_gpus[0].name == "NVIDIA GeForce RTX 3080 Ti"
    assert captured["command"] == [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ]
    assert captured["check"] is True
    assert captured["timeout"] == 3
    assert "shell" not in captured


def test_runtime_probe_failures_do_not_abort_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise RuntimeError("driver probe failed")

    def failed_cuda_probe() -> int:
        raise ValueError("cuda probe failed")

    monkeypatch.setattr("medical_image_check.infrastructure.performance.subprocess.run", failed_run)
    monkeypatch.setattr(
        "medical_image_check.infrastructure.performance.cv2.cuda.getCudaEnabledDeviceCount",
        failed_cuda_probe,
    )

    environment = detect_runtime_environment()

    assert environment.nvidia_gpus == ()
    assert environment.nvidia_probe_error == "driver probe failed"
    assert environment.opencv_cuda_device_count == 0
    assert environment.opencv_cuda_probe_error == "cuda probe failed"


def test_performance_export_contains_hardware_and_timings_without_sources(tmp_path: Path) -> None:
    performance = ScanPerformance(
        schema_version=1,
        selected_backend="cpu",
        accelerator_status="nvidia_hardware_detected_opencv_cuda_unavailable",
        wall_seconds=4.5,
        active_seconds=4.0,
        paused_seconds=0.5,
        stages=(
            StageTiming("image.generic_features", 3.0, 2, 20),
            StageTiming("image.read_decode", 1.0, 2, 20),
        ),
        environment=_environment(),
    )
    result = ScanResult(
        20,
        20,
        0,
        (),
        algorithm_version="test-algorithm",
        performance=performance,
    )
    project = Project.create("性能测试").with_sources([tmp_path / "secret.png"])

    output = PerformanceExporter().export(result, tmp_path / "profile", project)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert output.suffix == ".json"
    assert payload["algorithm_version"] == "test-algorithm"
    assert payload["environment"]["nvidia_gpus"][0]["name"] == ("NVIDIA GeForce RTX 3080 Ti")
    assert payload["performance"]["stages"][0]["stage_id"] == "image.generic_features"
    assert payload["performance"]["stages"][0]["active_time_percent"] == 75.0
    assert payload["privacy"] == {
        "raw_files_included": False,
        "source_paths_included": False,
        "finding_evidence_included": False,
    }
    assert "secret.png" not in output.read_text(encoding="utf-8")


def test_performance_export_requires_profile(tmp_path: Path) -> None:
    result = ScanResult(0, 0, 0, ())

    with pytest.raises(ValueError, match="不包含性能诊断"):
        PerformanceExporter().export(result, tmp_path / "profile.json")
