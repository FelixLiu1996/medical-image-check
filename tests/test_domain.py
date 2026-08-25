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
        details={"count": 1},
    )
    result = ScanResult(1, 1, 0, (finding,), (ScanIssue(str(source), "测试提示"),))
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
