from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GpuDevice:
    name: str
    driver_version: str | None = None
    memory_total_mb: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEnvironment:
    operating_system: str
    os_release: str
    machine: str
    processor: str
    logical_cpu_count: int | None
    python_version: str
    opencv_version: str
    nvidia_gpus: tuple[GpuDevice, ...] = ()
    nvidia_probe_error: str | None = None
    opencv_cuda_device_count: int = 0
    opencv_cuda_probe_error: str | None = None


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage_id: str
    duration_seconds: float
    calls: int = 1
    items: int = 0


@dataclass(frozen=True, slots=True)
class ScanPerformance:
    schema_version: int
    selected_backend: str
    accelerator_status: str
    wall_seconds: float
    active_seconds: float
    paused_seconds: float
    stages: tuple[StageTiming, ...]
    environment: RuntimeEnvironment


PERFORMANCE_SCHEMA_VERSION = 1
