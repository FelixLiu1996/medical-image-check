from PySide6.QtWidgets import QApplication

from medical_image_check.ui.main_window import MainWindow


def test_main_window_can_be_created_offscreen() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert "医学实验图像与数据查重" in window.windowTitle()

    window.close()
    app.processEvents()
