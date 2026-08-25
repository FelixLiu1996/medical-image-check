from pathlib import Path

from PySide6.QtWidgets import QApplication

from medical_image_check.app import main
from medical_image_check.domain.models import ScanResult
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
