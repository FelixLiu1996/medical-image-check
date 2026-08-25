from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
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

from medical_image_check.domain.models import RiskLevel, ScanResult
from medical_image_check.domain.project import Project
from medical_image_check.infrastructure.project_store import ProjectStore
from medical_image_check.services.basic_scan import BasicScanService
from medical_image_check.services.excel_report import ExcelReportExporter

PROJECT_FILTER = "医学查重项目 (*.mic-project.json)"
RISK_LABELS = {
    RiskLevel.HIGH: "高",
    RiskLevel.MEDIUM: "中",
    RiskLevel.LOW: "低",
}


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
        self.resize(1100, 720)
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._project: Project | None = None
        self._project_path: Path | None = None
        self._current_result: ScanResult | None = None
        self._dirty = False
        self._project_store = ProjectStore()
        self._report_exporter = ExcelReportExporter()
        self._build_ui()
        self._update_project_state()

    def _build_ui(self) -> None:
        self._build_menu()
        central = QWidget(self)
        layout = QVBoxLayout(central)

        title = QLabel("医学实验图像与数据查重")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        subtitle = QLabel("当前 Alpha 支持项目与 Excel 报告、图片文件/解码像素重复及整体近似查重。")
        subtitle.setStyleSheet("color: #666;")
        self._project_label = QLabel()
        self._project_label.setStyleSheet("color: #1f4e78; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self._project_label)

        project_actions = QHBoxLayout()
        new_project = QPushButton("新建项目")
        open_project = QPushButton("打开项目")
        self._save_button = QPushButton("保存项目")
        self._export_button = QPushButton("导出 Excel 报告")
        project_actions.addWidget(new_project)
        project_actions.addWidget(open_project)
        project_actions.addWidget(self._save_button)
        project_actions.addWidget(self._export_button)
        project_actions.addStretch(1)
        layout.addLayout(project_actions)

        actions = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_folder = QPushButton("添加文件夹")
        clear = QPushButton("清空输入")
        self._scan_button = QPushButton("开始扫描")
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

        self._status = QLabel("请新建或打开项目，然后添加图片、Excel 或文件夹。")
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        layout.addWidget(self._status)
        layout.addWidget(self._progress)

        self._results = QTableWidget(0, 4)
        self._results.setHorizontalHeaderLabels(["风险", "类型", "说明", "位置"])
        self._results.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._results, 1)

        new_project.clicked.connect(self._new_project_dialog)
        open_project.clicked.connect(self._open_project_dialog)
        self._save_button.clicked.connect(self._save_current_project)
        self._export_button.clicked.connect(self._export_excel_report_dialog)
        add_files.clicked.connect(self._select_files)
        add_folder.clicked.connect(self._select_folder)
        clear.clicked.connect(self._clear_sources)
        self._scan_button.clicked.connect(self._start_scan)
        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        actions = [
            ("新建项目", QKeySequence.StandardKey.New, self._new_project_dialog),
            ("打开项目", QKeySequence.StandardKey.Open, self._open_project_dialog),
            ("保存项目", QKeySequence.StandardKey.Save, self._save_current_project),
            ("项目另存为", QKeySequence.StandardKey.SaveAs, self._save_project_as),
            ("导出 Excel 报告", "Ctrl+E", self._export_excel_report_dialog),
        ]
        for text, shortcut, callback in actions:
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def create_project(self, name: str, path: str | Path | None = None) -> Project:
        self._project = Project.create(name)
        self._project_path = Path(path).expanduser().resolve() if path else None
        self._current_result = None
        self._sources.clear()
        self._results.setRowCount(0)
        self._dirty = True
        self._status.setText("项目已创建，请添加实验图片、表格或文件夹。")
        if self._project_path is not None:
            self._save_current_project(silent=True)
        self._update_project_state()
        return self._project

    def open_project(self, path: str | Path) -> Project:
        project_path = Path(path).expanduser().resolve()
        project = self._project_store.load(project_path)
        self._project = project
        self._project_path = project_path
        self._current_result = project.last_scan_result
        self._dirty = False
        self._sources.clear()
        for source in project.source_paths:
            self._sources.addItem(source)
        self._render_result(project.last_scan_result)
        result_count = len(project.last_scan_result.findings) if project.last_scan_result else 0
        self._status.setText(
            f"已打开项目：{project.name}。恢复 {len(project.source_paths)} 个输入路径"
            f"和 {result_count} 条结果。"
        )
        self._update_project_state()
        return project

    def save_project(self, path: str | Path | None = None) -> Path:
        if self._project is None:
            raise ValueError("当前没有可保存的项目")
        destination = Path(path).expanduser().resolve() if path else self._project_path
        if destination is None:
            raise ValueError("尚未选择项目保存位置")
        if not destination.name.endswith(".mic-project.json"):
            destination = destination.with_name(destination.name + ".mic-project.json")
        self._project_store.save(self._project, destination)
        self._project_path = destination
        self._dirty = False
        self._status.setText(f"项目已保存：{destination}")
        self._update_project_state()
        return destination

    def export_excel_report(self, path: str | Path) -> Path:
        if self._project is None or self._current_result is None:
            raise ValueError("请先完成或打开一次扫描结果")
        output = self._report_exporter.export(self._current_result, path, self._project)
        self._project = self._project.with_report(output)
        self._dirty = True
        if self._project_path is not None:
            self._save_current_project(silent=True)
        self._status.setText(f"Excel 报告已导出：{output}")
        self._update_project_state()
        return output

    @Slot()
    def _new_project_dialog(self) -> None:
        if not self._confirm_discard_changes():
            return
        name, accepted = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not accepted or not name.strip():
            return
        suggested = f"{name.strip()}.mic-project.json"
        path, _ = QFileDialog.getSaveFileName(self, "保存新项目", suggested, PROJECT_FILTER)
        self.create_project(name, path or None)

    @Slot()
    def _open_project_dialog(self) -> None:
        if not self._confirm_discard_changes():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", "", PROJECT_FILTER)
        if not path:
            return
        try:
            self.open_project(path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "无法打开项目", str(exc))

    @Slot()
    def _save_current_project(self, *, silent: bool = False) -> bool:
        if self._project is None:
            if not silent:
                QMessageBox.information(self, "没有项目", "请先新建或打开项目。")
            return False
        if self._project_path is None:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "保存项目",
                f"{self._project.name}.mic-project.json",
                PROJECT_FILTER,
            )
            if not path:
                return False
        else:
            path = str(self._project_path)
        try:
            self.save_project(path)
        except OSError as exc:
            if not silent:
                QMessageBox.critical(self, "保存失败", str(exc))
            return False
        return True

    @Slot()
    def _save_project_as(self) -> None:
        if self._project is None:
            QMessageBox.information(self, "没有项目", "请先新建或打开项目。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "项目另存为",
            f"{self._project.name}.mic-project.json",
            PROJECT_FILTER,
        )
        if path:
            try:
                self.save_project(path)
            except OSError as exc:
                QMessageBox.critical(self, "保存失败", str(exc))

    @Slot()
    def _export_excel_report_dialog(self) -> None:
        if self._current_result is None:
            QMessageBox.information(self, "没有扫描结果", "请先完成扫描或打开已有结果的项目。")
            return
        project_name = self._project.name if self._project else "查重"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Excel 报告",
            f"{project_name}-查重报告.xlsx",
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return
        try:
            output = self.export_excel_report(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "报告导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"报告已保存到：\n{output}")

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
        if not paths:
            return
        if self._project is None:
            self.create_project("未命名项目")
        existing = {self._sources.item(index).text() for index in range(self._sources.count())}
        added: list[str] = []
        for path in paths:
            normalized = str(Path(path).expanduser().resolve())
            if normalized not in existing:
                self._sources.addItem(normalized)
                existing.add(normalized)
                added.append(normalized)
        if added and self._project is not None:
            self._project = self._project.with_sources(added)
            self._current_result = None
            self._render_result(None)
            self._mark_dirty()
            self._status.setText(f"已添加 {len(added)} 个输入路径，原扫描结果已失效。")

    @Slot()
    def _clear_sources(self) -> None:
        if self._sources.count() == 0:
            return
        self._sources.clear()
        if self._project is not None:
            self._project = self._project.replace_sources([])
            self._current_result = None
            self._results.setRowCount(0)
            self._mark_dirty()
        self._status.setText("输入已清空。")

    @Slot()
    def _start_scan(self) -> None:
        sources = [self._sources.item(index).text() for index in range(self._sources.count())]
        if not sources:
            QMessageBox.information(self, "没有输入", "请先添加文件或文件夹。")
            return
        if self._project is None:
            self.create_project("未命名项目")
            self._project = self._project.with_sources(sources)

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
        self._current_result = result
        self._render_result(result)
        if self._project is not None:
            self._project = self._project.with_scan_result(result)
            self._mark_dirty()
            if self._project_path is not None:
                self._save_current_project(silent=True)
        self._status.setText(
            f"扫描完成：{result.source_count} 个文件，{len(result.findings)} 条结果，"
            f"{len(result.issues)} 个提示。"
        )
        if result.issues:
            preview = "\n".join(issue.message for issue in result.issues[:8])
            QMessageBox.warning(self, "扫描提示", preview)

    def _render_result(self, result: ScanResult | None) -> None:
        self._results.setRowCount(0)
        if result is None:
            return
        for finding in result.findings:
            row = self._results.rowCount()
            self._results.insertRow(row)
            locations = "\n".join(location.display_text for location in finding.locations)
            values = [
                RISK_LABELS[finding.risk],
                finding.title,
                finding.description,
                locations,
            ]
            for column, value in enumerate(values):
                self._results.setItem(row, column, QTableWidgetItem(value))

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

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_project_state()

    def _update_project_state(self) -> None:
        name = self._project.name if self._project else "未打开项目"
        marker = " *" if self._dirty else ""
        path_text = str(self._project_path) if self._project_path else "尚未保存"
        self._project_label.setText(f"当前项目：{name}{marker}  ·  {path_text}")
        self.setWindowTitle(f"医学实验图像与数据查重 · {name}{marker}")
        self._save_button.setEnabled(self._project is not None)
        self._export_button.setEnabled(self._current_result is not None)

    def _confirm_discard_changes(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "项目尚未保存",
            "当前项目有未保存更改，是否先保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save_current_project()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard_changes():
            event.accept()
        else:
            event.ignore()
