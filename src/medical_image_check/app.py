from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from medical_image_check import __version__
from medical_image_check.domain.models import ScanResult
from medical_image_check.domain.project import Project
from medical_image_check.services.excel_report import ExcelReportExporter
from medical_image_check.services.html_report import HtmlReportExporter
from medical_image_check.services.pdf_report import PdfReportExporter
from medical_image_check.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--version" in arguments:
        print(__version__)
        return 0

    app = QApplication.instance() or QApplication([sys.argv[0], *arguments])
    app.setApplicationName("科研数据查重助手")
    app.setOrganizationName("Medical Image Check")
    window = MainWindow()
    if "--smoke-test" in arguments:
        _verify_packaged_reports()
        window.close()
        app.processEvents()
        return 0
    window.show()
    return app.exec()


def _verify_packaged_reports() -> None:
    result = ScanResult(0, 0, 0, (), algorithm_version="packaging-smoke")
    project = Project.create("打包冒烟")
    with TemporaryDirectory(prefix="medical-image-check-smoke-") as directory:
        destination = Path(directory)
        excel = ExcelReportExporter().export(result, destination / "report.xlsx", project)
        html = HtmlReportExporter().export(result, destination / "report.html", project)
        pdf = PdfReportExporter().export(result, destination / "report.pdf", project)
        if not excel.is_file() or not html.read_text(encoding="utf-8").startswith("<!doctype"):
            raise RuntimeError("打包报告冒烟失败：Excel 或 HTML 文件无效")
        if not pdf.read_bytes().startswith(b"%PDF-"):
            raise RuntimeError("打包报告冒烟失败：PDF 文件无效")
