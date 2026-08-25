from pathlib import Path

import cv2
import numpy as np
from openpyxl import Workbook

from medical_image_check.services.basic_scan import BasicScanService


def test_basic_scan_collects_directory_and_reports_duplicates(tmp_path: Path) -> None:
    image = np.arange(32 * 32, dtype=np.uint8).reshape(32, 32)
    first_image = tmp_path / "one.png"
    assert cv2.imwrite(str(first_image), image)
    (tmp_path / "two.png").write_bytes(first_image.read_bytes())
    (tmp_path / "ignored.txt").write_text("same", encoding="utf-8")

    workbook = Workbook()
    workbook.active.append([2.5, 3.5])
    workbook.active.append([2.5, 3.5])
    workbook.save(tmp_path / "data.xlsx")

    progress: list[tuple[int, int, str]] = []
    result = BasicScanService().scan(
        [tmp_path], lambda done, total, text: progress.append((done, total, text))
    )

    assert result.source_count == 3
    assert result.image_count == 2
    assert result.spreadsheet_count == 1
    assert {finding.rule_id for finding in result.findings} == {
        "image.file.sha256",
        "excel.value.exact",
        "excel.row.exact",
    }
    assert result.algorithm_version == "generic-image-baseline-1"
    assert result.completed_at is not None
    assert progress[-1][:2] == (3, 3)


def test_corrupt_workbook_does_not_abort_other_files(tmp_path: Path) -> None:
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    corrupt = tmp_path / "broken.xlsx"
    image = np.arange(32 * 32, dtype=np.uint8).reshape(32, 32)
    assert cv2.imwrite(str(first), image)
    second.write_bytes(first.read_bytes())
    corrupt.write_bytes(b"not-an-excel-archive")

    result = BasicScanService().scan([tmp_path])

    assert any(finding.rule_id == "image.file.sha256" for finding in result.findings)
    assert len(result.issues) == 1
    assert result.issues[0].source_path == str(corrupt)
    assert first.read_bytes() == second.read_bytes()
    assert corrupt.read_bytes() == b"not-an-excel-archive"
