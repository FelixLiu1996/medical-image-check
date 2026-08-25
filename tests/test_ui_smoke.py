from pathlib import Path

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from medical_image_check.app import main
from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanResult,
)
from medical_image_check.ui.main_window import MainWindow


def test_main_window_can_be_created_offscreen() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert "医学实验图像与数据查重" in window.windowTitle()

    window.close()
    app.processEvents()


def test_main_window_can_save_restore_project_and_export_report(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    project_path = tmp_path / "ui-project.mic-project.json"
    source = tmp_path / "source.png"
    source.write_bytes(b"source")

    window = MainWindow()
    window.create_project("界面项目", project_path)
    window._append_sources([str(source)])
    window._show_result(ScanResult(1, 1, 0, (), ()))
    window.save_project()
    report = window.export_excel_report(tmp_path / "ui-report.xlsx")

    restored = MainWindow()
    restored.open_project(project_path)

    assert restored._project is not None
    assert restored._project.name == "界面项目"
    assert restored._sources.count() == 1
    assert restored._current_result == ScanResult(1, 1, 0, (), ())
    assert report.exists()

    window.close()
    restored.close()
    app.processEvents()


def test_application_packaging_smoke_mode_exits_without_event_loop() -> None:
    assert main(["--smoke-test"]) == 0


def test_main_window_displays_local_geometric_evidence(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    image = QImage(120, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(first))
    assert image.save(str(second))
    finding = Finding(
        finding_id="local-evidence",
        rule_id="image.local.geometric",
        finding_type=FindingType.SUSPECTED_REUSE,
        risk=RiskLevel.MEDIUM,
        title="图片存在局部重叠",
        description="局部几何证据",
        locations=(EvidenceLocation(str(first)), EvidenceLocation(str(second))),
        confidence=0.9,
        details={
            "transform_model": "affine",
            "matched_keypoints": 24,
            "inlier_count": 20,
            "inlier_ratio": 0.83,
            "first_region_x": 10,
            "first_region_y": 8,
            "first_region_width": 60,
            "first_region_height": 40,
            "second_region_x": 5,
            "second_region_y": 4,
            "second_region_width": 90,
            "second_region_height": 60,
            "first_coverage": 0.25,
            "second_coverage": 0.56,
            "rotation_degrees_second_to_first": 0.0,
            "scale_x_second_to_first": 1.0,
            "scale_y_second_to_first": 1.0,
        },
    )
    window = MainWindow()
    window._render_result(ScanResult(2, 2, 0, (finding,)))

    window._show_selected_evidence(0)

    assert window._first_evidence._region == (10, 8, 60, 40)
    assert window._second_evidence._region == (5, 4, 90, 60)
    assert "几何内点：20" in window._evidence_summary.text()

    window.close()
    app.processEvents()
