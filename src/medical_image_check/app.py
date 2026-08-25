from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from medical_image_check import __version__
from medical_image_check.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--version" in arguments:
        print(__version__)
        return 0

    app = QApplication.instance() or QApplication([sys.argv[0], *arguments])
    app.setApplicationName("医学实验图像与数据查重")
    app.setOrganizationName("Medical Image Check")
    window = MainWindow()
    if "--smoke-test" in arguments:
        window.close()
        app.processEvents()
        return 0
    window.show()
    return app.exec()
