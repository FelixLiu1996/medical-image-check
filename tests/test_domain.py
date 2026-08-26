import json
from pathlib import Path

import pytest

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    ScanResult,
)
from medical_image_check.domain.performance import (
    GpuDevice,
    RuntimeEnvironment,
    ScanPerformance,
    StageTiming,
)
from medical_image_check.domain.project import PROJECT_SCHEMA_VERSION, Project
from medical_image_check.infrastructure.project_store import ProjectStore
from medical_image_check.infrastructure.spreadsheets import canonical_numeric


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (1.2300, "1.23"),
        (1000, "1000"),
        (1e-5, "0.00001"),
    ],
)
def test_canonical_numeric(value: int | float, expected: str) -> None:
    assert canonical_numeric(value) == expected


def test_project_store_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    finding = Finding(
        finding_id="finding-1",
        rule_id="test.rule",
        finding_type=FindingType.EXACT_DUPLICATE,
        risk=RiskLevel.HIGH,
        title="测试重复",
        description="用于项目恢复测试",
        locations=(EvidenceLocation(str(source)),),
        details={"count": 1, "cells": [{"coordinate": "A1", "value": "2.5"}]},
    )
    performance = ScanPerformance(
        schema_version=1,
        selected_backend="cpu",
        accelerator_status="nvidia_hardware_detected_opencv_cuda_unavailable",
        wall_seconds=1.5,
        active_seconds=1.25,
        paused_seconds=0.25,
        stages=(StageTiming("image.generic_features", 1.0, 1, 1),),
        environment=RuntimeEnvironment(
            "Windows",
            "11",
            "AMD64",
            "test-cpu",
            16,
            "3.12.13",
            "4.14.0",
            (GpuDevice("NVIDIA GeForce RTX 3080 Ti", "999.1", 12288),),
        ),
    )
    result = ScanResult(
        1,
        1,
        0,
        (finding,),
        (ScanIssue(str(source), "测试提示"),),
        performance=performance,
    )
    project = Project.create("基础查重").with_sources([source]).with_scan_result(result)
    destination = tmp_path / "project.mic-project.json"

    store = ProjectStore()
    store.save(project, destination)
    loaded = store.load(destination)

    assert loaded == project
    assert source.read_bytes() == b"image"


def test_project_store_migrates_schema_version_one(tmp_path: Path) -> None:
    destination = tmp_path / "legacy.mic-project.json"
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "legacy-id",
                "name": "旧项目",
                "created_at": "2026-08-25T00:00:00+00:00",
                "updated_at": "2026-08-25T00:00:00+00:00",
                "source_paths": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = ProjectStore().load(destination)

    assert loaded.schema_version == PROJECT_SCHEMA_VERSION
    assert loaded.name == "旧项目"
    assert loaded.last_scan_result is None
    assert loaded.report_paths == ()


def test_changing_project_sources_invalidates_previous_scan(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    result = ScanResult(1, 1, 0, ())
    project = Project.create("变更输入").with_sources([first]).with_scan_result(result)

    changed = project.with_sources([second])

    assert changed.last_scan_result is None
    assert set(changed.source_paths) == {str(first.resolve()), str(second.resolve())}


def test_project_digit_fragment_setting_persists_and_invalidates_scan(tmp_path: Path) -> None:
    result = ScanResult(0, 0, 0, ())
    project = Project.create("片段设置").with_scan_result(result)

    changed = project.with_minimum_digit_run(6)
    destination = tmp_path / "settings.mic-project.json"
    ProjectStore().save(changed, destination)
    loaded = ProjectStore().load(destination)

    assert changed.last_scan_result is None
    assert loaded.minimum_digit_run == 6


def test_project_store_migrates_schema_version_two_digit_setting(tmp_path: Path) -> None:
    destination = tmp_path / "schema-two.mic-project.json"
    destination.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "project_id": "schema-two",
                "name": "版本二项目",
                "created_at": "2026-08-25T00:00:00+00:00",
                "updated_at": "2026-08-25T00:00:00+00:00",
                "source_paths": [],
                "last_scan_result": None,
                "report_paths": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = ProjectStore().load(destination)

    assert loaded.schema_version == PROJECT_SCHEMA_VERSION
    assert loaded.minimum_digit_run == 4
    assert loaded.western_single_band_enabled is False


def test_project_western_single_band_setting_persists_and_invalidates_scan(
    tmp_path: Path,
) -> None:
    result = ScanResult(0, 0, 0, ())
    project = Project.create("Western 设置").with_scan_result(result)

    changed = project.with_western_single_band_enabled(True)
    destination = tmp_path / "western-settings.mic-project.json"
    ProjectStore().save(changed, destination)
    loaded = ProjectStore().load(destination)

    assert changed.last_scan_result is None
    assert loaded.western_single_band_enabled is True


def test_project_image_analysis_mode_persists_and_invalidates_scan(tmp_path: Path) -> None:
    project = Project.create("图片类型").with_scan_result(ScanResult(0, 0, 0, ()))

    changed = project.with_image_analysis_mode("dot_blot")
    destination = tmp_path / "image-mode.mic-project.json"
    ProjectStore().save(changed, destination)
    loaded = ProjectStore().load(destination)

    assert changed.last_scan_result is None
    assert loaded.image_analysis_mode == "dot_blot"


def test_project_store_migrates_schema_version_three_western_setting(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "schema-three.mic-project.json"
    destination.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "project_id": "schema-three",
                "name": "版本三项目",
                "created_at": "2026-08-25T00:00:00+00:00",
                "updated_at": "2026-08-25T00:00:00+00:00",
                "source_paths": [],
                "minimum_digit_run": 4,
                "last_scan_result": None,
                "report_paths": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = ProjectStore().load(destination)

    assert loaded.schema_version == PROJECT_SCHEMA_VERSION
    assert loaded.western_single_band_enabled is False


def test_project_excel_settings_persist_and_invalidate_scan(tmp_path: Path) -> None:
    project = Project.create("Excel 参数").with_scan_result(ScanResult(0, 0, 0, ()))

    changed = project.with_excel_analysis_settings(
        0.25,
        "1e-9",
        ("0", "1", "50", "100"),
        4,
        6,
    )
    destination = tmp_path / "excel-settings.mic-project.json"
    ProjectStore().save(changed, destination)
    loaded = ProjectStore().load(destination)

    assert changed.last_scan_result is None
    assert loaded.excel_custom_relative_tolerance_percent == 0.25
    assert loaded.excel_absolute_tolerance == "0.000000001"
    assert loaded.excel_operation_targets == ("0", "1", "50", "100")
    assert loaded.excel_medium_run_length == 4
    assert loaded.excel_high_run_length == 6


def test_project_store_migrates_schema_version_four_excel_settings(tmp_path: Path) -> None:
    destination = tmp_path / "schema-four.mic-project.json"
    destination.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "project_id": "schema-four",
                "name": "版本四项目",
                "created_at": "2026-08-25T00:00:00+00:00",
                "updated_at": "2026-08-25T00:00:00+00:00",
                "source_paths": [],
                "minimum_digit_run": 4,
                "western_single_band_enabled": False,
                "last_scan_result": None,
                "report_paths": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = ProjectStore().load(destination)

    assert loaded.schema_version == PROJECT_SCHEMA_VERSION
    assert loaded.excel_custom_relative_tolerance_percent == 0
    assert loaded.excel_operation_targets == ("0", "1", "10", "100", "1000")


@pytest.mark.parametrize("schema_version", [5, 6])
def test_project_store_migrates_recent_schema_without_performance(
    tmp_path: Path,
    schema_version: int,
) -> None:
    destination = tmp_path / f"schema-{schema_version}.mic-project.json"
    destination.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "project_id": f"schema-{schema_version}",
                "name": "旧性能项目",
                "created_at": "2026-08-25T00:00:00+00:00",
                "updated_at": "2026-08-25T00:00:00+00:00",
                "source_paths": [],
                "last_scan_result": {
                    "source_count": 0,
                    "image_count": 0,
                    "spreadsheet_count": 0,
                    "findings": [],
                    "issues": [],
                },
                "report_paths": [],
            }
        ),
        encoding="utf-8",
    )

    loaded = ProjectStore().load(destination)

    assert loaded.schema_version == PROJECT_SCHEMA_VERSION
    assert loaded.last_scan_result is not None
    assert loaded.last_scan_result.performance is None
