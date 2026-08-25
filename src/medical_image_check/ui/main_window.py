from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medical_image_check.domain.models import ScanResult
from medical_image_check.services.basic_scan import BasicScanService


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, sources: list[str]) -> None:
        super().__init__()
        self._sources = sources

    @Slot()
    def run(self) -> None:
        try:
            result = BasicScanService().scan(self._sources, self.progress.emit)
        except Exception as exc:  # noqa: BLE001 - worker must report unexpected failures to UI
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("医学实验图像与数据查重 · 基础开发版")
        self.resize(1100, 720)
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        title = QLabel("医学实验图像与数据查重")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        subtitle = QLabel("当前开发版仅执行图片文件完全重复、Excel 完整数值与整行重复检测。")
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        actions = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_folder = QPushButton("添加文件夹")
        clear = QPushButton("清空")
        self._scan_button = QPushButton("开始基础扫描")
        self._scan_button.setStyleSheet("font-weight: 600;")
        actions.addWidget(add_files)
        actions.addWidget(add_folder)
        actions.addWidget(clear)
        actions.addStretch(1)
        actions.addWidget(self._scan_button)
        layout.addLayout(actions)

        self._sources = QListWidget()
        self._sources.setMinimumHeight(120)
        layout.addWidget(self._sources)

        self._status = QLabel("请选择图片、Excel 或文件夹。")
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        layout.addWidget(self._status)
        layout.addWidget(self._progress)

        self._results = QTableWidget(0, 4)
        self._results.setHorizontalHeaderLabels(["风险", "类型", "说明", "位置"])
        self._results.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._results, 1)

        add_files.clicked.connect(self._select_files)
        add_folder.clicked.connect(self._select_folder)
        clear.clicked.connect(self._sources.clear)
        self._scan_button.clicked.connect(self._start_scan)
        self.setCentralWidget(central)

    @Slot()
    def _select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择实验图片或表格",
            "",
            "支持的文件 (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff *.xlsx *.xls *.xlsm *.csv)",
        )
        self._append_sources(paths)

    @Slot()
    def _select_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择包含实验数据的文件夹")
        if path:
            self._append_sources([path])

    def _append_sources(self, paths: list[str]) -> None:
        existing = {self._sources.item(index).text() for index in range(self._sources.count())}
        for path in paths:
            normalized = str(Path(path).resolve())
            if normalized not in existing:
                self._sources.addItem(normalized)
                existing.add(normalized)

    @Slot()
    def _start_scan(self) -> None:
        sources = [self._sources.item(index).text() for index in range(self._sources.count())]
        if not sources:
            QMessageBox.information(self, "没有输入", "请先添加文件或文件夹。")
            return

        self._results.setRowCount(0)
        self._scan_button.setEnabled(False)
        self._thread = QThread(self)
        self._worker = ScanWorker(sources)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._update_progress)
        self._worker.finished.connect(self._show_result)
        self._worker.failed.connect(self._show_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    @Slot(int, int, str)
    def _update_progress(self, completed: int, total: int, message: str) -> None:
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(completed)
        self._status.setText(message)

    @Slot(object)
    def _show_result(self, result: ScanResult) -> None:
        for finding in result.findings:
            row = self._results.rowCount()
            self._results.insertRow(row)
            locations = "\n".join(location.display_text for location in finding.locations)
            values = [finding.risk.value, finding.title, finding.description, locations]
            for column, value in enumerate(values):
                self._results.setItem(row, column, QTableWidgetItem(value))
        self._status.setText(
            f"扫描完成：{result.source_count} 个文件，{len(result.findings)} 条结果，"
            f"{len(result.issues)} 个提示。"
        )
        if result.issues:
            preview = "\n".join(issue.message for issue in result.issues[:8])
            QMessageBox.warning(self, "扫描提示", preview)

    @Slot(str)
    def _show_failure(self, message: str) -> None:
        self._status.setText("扫描失败")
        QMessageBox.critical(self, "扫描失败", message)

    @Slot()
    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._scan_button.setEnabled(True)
