from pathlib import Path

import pytest

from medical_image_check.domain.project import Project
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
    project = Project.create("基础查重").with_sources([source])
    destination = tmp_path / "project.mic-project.json"

    store = ProjectStore()
    store.save(project, destination)
    loaded = store.load(destination)

    assert loaded == project
    assert source.read_bytes() == b"image"
