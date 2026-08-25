from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from medical_image_check.ui.main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("医学实验图像与数据查重")
    app.setOrganizationName("Medical Image Check")
    window = MainWindow()
    window.show()
    return app.exec()
