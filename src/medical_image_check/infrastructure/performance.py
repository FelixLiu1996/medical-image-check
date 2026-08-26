from __future__ import annotations

import csv
import io
import os
import platform
import subprocess
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

import cv2

from medical_image_check.domain.performance import (
    PERFORMANCE_SCHEMA_VERSION,
    GpuDevice,
    RuntimeEnvironment,
    ScanPerformance,
    StageTiming,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@dataclass(slots=True)
class _MutableStage:
    duration_seconds: float = 0.0
    calls: int = 0
    items: int = 0


class PerformanceRecorder:
    def __init__(
        self,
        paused_seconds: Callable[[], float] | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._clock = clock
        self._paused_seconds = paused_seconds or (lambda: 0.0)
        self._started_at = clock()
        self._paused_at_start = self._paused_seconds()
        self._stages: OrderedDict[str, _MutableStage] = OrderedDict()

    @contextmanager
    def stage(self, stage_id: str) -> Iterator[None]:
        started_at = self._clock()
        paused_at_start = self._paused_seconds()
        try:
            yield
        finally:
            elapsed = self._clock() - started_at
            paused = max(0.0, self._paused_seconds() - paused_at_start)
            stage = self._stages.setdefault(stage_id, _MutableStage())
            stage.duration_seconds += max(0.0, elapsed - paused)
            stage.calls += 1

    def add_items(self, stage_id: str, count: int) -> None:
        if count <= 0:
            return
        self._stages.setdefault(stage_id, _MutableStage()).items += count

    def finish(self, environment: RuntimeEnvironment) -> ScanPerformance:
        wall_seconds = max(0.0, self._clock() - self._started_at)
        paused_seconds = max(0.0, self._paused_seconds() - self._paused_at_start)
        stages = tuple(
            StageTiming(
                stage_id=stage_id,
                duration_seconds=round(values.duration_seconds, 6),
                calls=values.calls,
                items=values.items,
            )
            for stage_id, values in self._stages.items()
        )
        return ScanPerformance(
            schema_version=PERFORMANCE_SCHEMA_VERSION,
            selected_backend="cpu",
            accelerator_status=_accelerator_status(environment),
            wall_seconds=round(wall_seconds, 6),
            active_seconds=round(max(0.0, wall_seconds - paused_seconds), 6),
            paused_seconds=round(paused_seconds, 6),
            stages=stages,
            environment=environment,
        )


@contextmanager
def profile_stage(
    recorder: PerformanceRecorder | None,
    stage_id: str,
) -> Iterator[None]:
    if recorder is None:
        yield
        return
    with recorder.stage(stage_id):
        yield


def record_items(recorder: PerformanceRecorder | None, stage_id: str, count: int) -> None:
    if recorder is not None:
        recorder.add_items(stage_id, count)


def detect_runtime_environment() -> RuntimeEnvironment:
    operating_system = platform.system() or "unknown"
    os_release = platform.release() or "unknown"
    machine = platform.machine() or "unknown"
    processor = platform.processor() or "unknown"
    nvidia_gpus, nvidia_error = _query_nvidia_gpus()
    cuda_devices = 0
    cuda_error = None
    try:
        cuda_devices = int(cv2.cuda.getCudaEnabledDeviceCount())
    except Exception as exc:  # noqa: BLE001 - diagnostics must never block a scan
        cuda_error = _short_error(exc)
    return RuntimeEnvironment(
        operating_system=operating_system,
        os_release=os_release,
        machine=machine,
        processor=processor,
        logical_cpu_count=os.cpu_count(),
        python_version=platform.python_version(),
        opencv_version=cv2.__version__,
        nvidia_gpus=nvidia_gpus,
        nvidia_probe_error=nvidia_error,
        opencv_cuda_device_count=max(0, cuda_devices),
        opencv_cuda_probe_error=cuda_error,
    )


def parse_nvidia_smi_output(output: str) -> tuple[GpuDevice, ...]:
    devices: list[GpuDevice] = []
    for row in csv.reader(io.StringIO(output.strip())):
        if not row or not row[0].strip():
            continue
        name = row[0].strip()
        driver = row[1].strip() if len(row) > 1 and row[1].strip() else None
        memory = None
        if len(row) > 2:
            try:
                memory = int(float(row[2].strip()))
            except ValueError:
                memory = None
        devices.append(GpuDevice(name=name, driver_version=driver, memory_total_mb=memory))
    return tuple(devices)


def _query_nvidia_gpus() -> tuple[tuple[GpuDevice, ...], str | None]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creation_flags,
        )
    except FileNotFoundError:
        return (), None
    except Exception as exc:  # noqa: BLE001 - optional hardware probe must be isolated
        return (), _short_error(exc)
    try:
        return parse_nvidia_smi_output(completed.stdout), None
    except (csv.Error, UnicodeError) as exc:
        return (), _short_error(exc)


def _accelerator_status(environment: RuntimeEnvironment) -> str:
    if environment.opencv_cuda_device_count > 0:
        return "opencv_cuda_available_not_selected"
    if environment.nvidia_gpus:
        return "nvidia_hardware_detected_opencv_cuda_unavailable"
    if environment.nvidia_probe_error:
        return "nvidia_probe_failed_cpu_selected"
    return "no_nvidia_gpu_detected"


def _short_error(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return text[:500] or error.__class__.__name__
