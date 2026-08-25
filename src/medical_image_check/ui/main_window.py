from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medical_image_check.domain.models import EvidenceLocation, Finding, RiskLevel, ScanResult
from medical_image_check.domain.project import Project
from medical_image_check.engines.image_exact import SUPPORTED_IMAGE_EXTENSIONS
from medical_image_check.infrastructure.project_store import ProjectStore
from medical_image_check.services.basic_scan import BasicScanService
from medical_image_check.services.excel_report import ExcelReportExporter

PROJECT_FILTER = "医学查重项目 (*.mic-project.json)"
RISK_LABELS = {
    RiskLevel.HIGH: "高",
    RiskLevel.MEDIUM: "中",
    RiskLevel.LOW: "低",
}


class ImageEvidenceView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self._source_path: str | None = None
        self._page = 1
        self._image = QImage()
        self._region: tuple[int, int, int, int] | None = None

    def set_evidence(
        self,
        source_path: str | None,
        region: tuple[int, int, int, int] | None = None,
        page: int = 1,
    ) -> None:
        self._source_path = source_path
        self._page = max(1, page)
        self._image = _read_evidence_image(source_path, self._page)
        self._region = region
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f5f7fa"))
        painter.setPen(QColor("#52606d"))
        if not self._source_path:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "请选择一条双图像结果")
            return
        if self._image.isNull():
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"图片无法预览或已移动\n{self._source_path}",
            )
            return

        page_text = f" · 第 {self._page} 页" if self._page > 1 else ""
        painter.drawText(8, 20, f"{Path(self._source_path).name}{page_text}")
        available = QRectF(8, 28, max(1, self.width() - 16), max(1, self.height() - 36))
        image_ratio = self._image.width() / max(self._image.height(), 1)
        available_ratio = available.width() / max(available.height(), 1)
        if image_ratio >= available_ratio:
            target_width = available.width()
            target_height = target_width / image_ratio
        else:
            target_height = available.height()
            target_width = target_height * image_ratio
        target = QRectF(
            available.x() + (available.width() - target_width) / 2,
            available.y() + (available.height() - target_height) / 2,
            target_width,
            target_height,
        )
        painter.drawImage(target, self._image)
        painter.setPen(QPen(QColor("#e12d39"), 3))
        painter.drawRect(target)
        if self._region is not None:
            x, y, width, height = self._region
            evidence_rect = QRectF(
                target.x() + x * target.width() / self._image.width(),
                target.y() + y * target.height() / self._image.height(),
                width * target.width() / self._image.width(),
                height * target.height() / self._image.height(),
            )
            painter.setPen(QPen(QColor("#ffb000"), 4))
            painter.drawRect(evidence_rect)


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        sources: list[str],
        minimum_digit_run: int,
        western_single_band_enabled: bool,
    ) -> None:
        super().__init__()
        self._sources = sources
        self._minimum_digit_run = minimum_digit_run
        self._western_single_band_enabled = western_single_band_enabled

    @Slot()
    def run(self) -> None:
        try:
            result = BasicScanService(
                self._minimum_digit_run,
                self._western_single_band_enabled,
            ).scan(self._sources, self.progress.emit)
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
        self._rendered_findings: list[Finding] = []
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
        subtitle = QLabel(
            "当前 Alpha 支持项目与 Excel 报告、通用图片查重及 Western blot 专项候选。"
        )
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

        scan_settings = QHBoxLayout()
        scan_settings.addWidget(QLabel("连续数字片段最短报警位数："))
        self._digit_run_spin = QSpinBox()
        self._digit_run_spin.setRange(3, 12)
        self._digit_run_spin.setValue(4)
        self._digit_run_spin.setToolTip("默认 4 位；数值越小召回越多，低风险结果也会明显增加。")
        scan_settings.addWidget(self._digit_run_spin)
        self._western_single_band_check = QCheckBox("检测 Western blot 单条带相似")
        self._western_single_band_check.setToolTip(
            "默认关闭；单条带自然相似较常见，启用后只生成低风险人工复核候选。"
        )
        scan_settings.addWidget(self._western_single_band_check)
        scan_settings.addStretch(1)
        layout.addLayout(scan_settings)

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

        evidence_group = QGroupBox("结果证据预览")
        evidence_layout = QVBoxLayout(evidence_group)
        self._evidence_images_container = QWidget()
        evidence_images = QHBoxLayout(self._evidence_images_container)
        evidence_images.setContentsMargins(0, 0, 0, 0)
        self._first_evidence = ImageEvidenceView()
        self._second_evidence = ImageEvidenceView()
        evidence_images.addWidget(self._first_evidence, 1)
        evidence_images.addWidget(self._second_evidence, 1)
        self._evidence_summary = QLabel("选择一条结果后显示图像或数值证据。")
        self._evidence_summary.setWordWrap(True)
        self._evidence_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        evidence_layout.addWidget(self._evidence_images_container)
        evidence_layout.addWidget(self._evidence_summary)
        layout.addWidget(evidence_group)

        new_project.clicked.connect(self._new_project_dialog)
        open_project.clicked.connect(self._open_project_dialog)
        self._save_button.clicked.connect(self._save_current_project)
        self._export_button.clicked.connect(self._export_excel_report_dialog)
        add_files.clicked.connect(self._select_files)
        add_folder.clicked.connect(self._select_folder)
        clear.clicked.connect(self._clear_sources)
        self._scan_button.clicked.connect(self._start_scan)
        self._digit_run_spin.valueChanged.connect(self._scan_settings_changed)
        self._western_single_band_check.toggled.connect(self._western_settings_changed)
        self._results.cellClicked.connect(self._show_selected_evidence)
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
        self._digit_run_spin.blockSignals(True)
        self._digit_run_spin.setValue(self._project.minimum_digit_run)
        self._digit_run_spin.blockSignals(False)
        self._western_single_band_check.blockSignals(True)
        self._western_single_band_check.setChecked(self._project.western_single_band_enabled)
        self._western_single_band_check.blockSignals(False)
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
        self._digit_run_spin.blockSignals(True)
        self._digit_run_spin.setValue(project.minimum_digit_run)
        self._digit_run_spin.blockSignals(False)
        self._western_single_band_check.blockSignals(True)
        self._western_single_band_check.setChecked(project.western_single_band_enabled)
        self._western_single_band_check.blockSignals(False)
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
            self._render_result(None)
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

        self._render_result(None)
        self._scan_button.setEnabled(False)
        self._thread = QThread(self)
        minimum_digit_run = self._project.minimum_digit_run if self._project else 4
        western_single_band_enabled = (
            self._project.western_single_band_enabled if self._project else False
        )
        self._worker = ScanWorker(
            sources,
            minimum_digit_run,
            western_single_band_enabled,
        )
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
        self._rendered_findings = []
        self._clear_evidence()
        if result is None:
            return
        for finding in result.findings:
            self._rendered_findings.append(finding)
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

    @Slot(int, int)
    def _show_selected_evidence(self, row: int, column: int = 0) -> None:
        del column
        if row < 0 or row >= len(self._rendered_findings):
            self._clear_evidence()
            return
        finding = self._rendered_findings[row]
        if finding.rule_id.startswith("excel."):
            self._evidence_images_container.hide()
            self._evidence_summary.setText(_excel_evidence_summary_text(finding))
            return
        if len(finding.locations) < 2 or not all(
            Path(location.source_path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            for location in finding.locations[:2]
        ):
            self._clear_evidence("当前结果不是可并排预览的双图像证据。")
            return

        self._evidence_images_container.show()
        first_region = _evidence_region(finding, "first")
        second_region = _evidence_region(finding, "second")
        self._first_evidence.set_evidence(
            finding.locations[0].source_path,
            first_region,
            _evidence_page(finding.locations[0]),
        )
        self._second_evidence.set_evidence(
            finding.locations[1].source_path,
            second_region,
            _evidence_page(finding.locations[1]),
        )
        self._evidence_summary.setText(_evidence_summary_text(finding))

    def _clear_evidence(self, message: str | None = None) -> None:
        self._evidence_images_container.show()
        self._first_evidence.set_evidence(None)
        self._second_evidence.set_evidence(None)
        self._evidence_summary.setText(message or "选择一条结果后显示图像或数值证据。")

    @Slot(str)
    def _show_failure(self, message: str) -> None:
        self._status.setText("扫描失败")
        QMessageBox.critical(self, "扫描失败", message)

    @Slot(int)
    def _scan_settings_changed(self, minimum_digit_run: int) -> None:
        if self._project is None or self._project.minimum_digit_run == minimum_digit_run:
            return
        self._project = self._project.with_minimum_digit_run(minimum_digit_run)
        self._current_result = None
        self._render_result(None)
        self._mark_dirty()
        self._status.setText(f"连续数字片段最短位数已改为 {minimum_digit_run}，原扫描结果已失效。")

    @Slot(bool)
    def _western_settings_changed(self, enabled: bool) -> None:
        if self._project is None or self._project.western_single_band_enabled == enabled:
            return
        self._project = self._project.with_western_single_band_enabled(enabled)
        self._current_result = None
        self._render_result(None)
        self._mark_dirty()
        state = "启用" if enabled else "关闭"
        self._status.setText(f"Western blot 单条带检测已{state}，原扫描结果已失效。")

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
        self._digit_run_spin.setEnabled(self._project is not None)
        self._western_single_band_check.setEnabled(self._project is not None)

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


def _evidence_region(finding: Finding, prefix: str) -> tuple[int, int, int, int] | None:
    keys = tuple(f"{prefix}_region_{suffix}" for suffix in ("x", "y", "width", "height"))
    if not all(key in finding.details for key in keys):
        return None
    try:
        values = [int(finding.details[key]) for key in keys]
        return values[0], values[1], values[2], values[3]
    except (TypeError, ValueError):
        return None


def _evidence_page(location: EvidenceLocation) -> int:
    coordinate = location.coordinate or ""
    match = re.search(r"第\s*(\d+)\s*页", coordinate)
    if match:
        return max(1, int(match.group(1)))
    return 1


def _read_evidence_image(source_path: str | None, page: int) -> QImage:
    if not source_path:
        return QImage()
    reader = QImageReader(source_path)
    reader.setAutoTransform(True)
    if page > 1 and not reader.jumpToImage(page - 1):
        return QImage()
    return reader.read()


def _evidence_summary_text(finding: Finding) -> str:
    details = finding.details
    if finding.rule_id.startswith("image.western_blot."):
        return (
            f"Western blot 证据：匹配条带 {details.get('matched_band_count', '-')} 条；"
            f"条带结构 {_as_percent(details.get('structure_similarity'))}；"
            f"排列几何 {_as_percent(details.get('geometry_similarity'))}；"
            f"背景纹理 {_as_percent(details.get('background_similarity'))}；"
            f"掩膜重叠 {_as_percent(details.get('band_mask_iou'))}；"
            f"变换 {details.get('transform_second_to_first', '-')}；"
            f"极性 {details.get('first_polarity', '-')} / "
            f"{details.get('second_polarity', '-')}。"
        )
    if finding.rule_id != "image.local.geometric":
        return f"{finding.title}：{finding.description}"
    return (
        f"几何模型：{details.get('transform_model', '-')}；"
        f"双向匹配点：{details.get('matched_keypoints', '-')}；"
        f"几何内点：{details.get('inlier_count', '-')}；"
        f"内点比例：{_as_percent(details.get('inlier_ratio'))}；"
        f"区域覆盖：{_as_percent(details.get('first_coverage'))} / "
        f"{_as_percent(details.get('second_coverage'))}；"
        f"旋转：{details.get('rotation_degrees_second_to_first', '-')}°；"
        f"缩放：{details.get('scale_x_second_to_first', '-')} × "
        f"{details.get('scale_y_second_to_first', '-')}。"
    )


def _excel_evidence_summary_text(finding: Finding) -> str:
    fragments = finding.details.get("fragments")
    cells = finding.details.get("cells")
    if isinstance(cells, list):
        if isinstance(fragments, list):
            fragment_text = "、".join(str(fragment) for fragment in fragments[:8])
            lines = [
                f"匹配数字片段：{fragment_text}",
                f"最长连续位数：{finding.details.get('maximum_length', '-')}；"
                f"涉及 {finding.details.get('cell_count', len(cells))} 个单元格。",
            ]
        else:
            lines = [
                f"{finding.title}：{finding.description}",
                f"绝对差：{finding.details.get('absolute_difference', '-')}；"
                f"相对误差：{finding.details.get('relative_error_percent', '-')}%；"
                f"档位：{finding.details.get('tolerance_band', '-')}。",
            ]
        for cell in cells[:12]:
            if not isinstance(cell, dict):
                continue
            location = " / ".join(
                str(value)
                for value in (
                    cell.get("source_path"),
                    cell.get("sheet"),
                    cell.get("coordinate"),
                )
                if value
            )
            lines.append(
                f"{location}：完整值 {cell.get('canonical_value', '-')}；"
                f"读取值 {cell.get('display_value', '-')}"
            )
        if len(cells) > 12:
            lines.append(f"其余 {len(cells) - 12} 个单元格请在 Excel 报告中查看。")
        return "\n".join(lines)

    paired_values = finding.details.get("paired_values")
    if isinstance(paired_values, list):
        lines = [
            f"{finding.title}：{finding.description}",
            f"关系参数/目标：{finding.details.get('parameter', '-')}；"
            f"连续匹配：{finding.details.get('matched_count', len(paired_values))} 组；"
            f"对齐方式：{finding.details.get('alignment', '-')}。",
        ]
        for pair in paired_values[:12]:
            if not isinstance(pair, dict):
                continue
            lines.append(
                f"{pair.get('first_coordinate', '-')}={pair.get('first_value', '-')}；"
                f"{pair.get('second_coordinate', '-')}={pair.get('second_value', '-')}；"
                f"关系结果={pair.get('relation_result', '-')}"
            )
        if len(paired_values) > 12:
            lines.append(f"其余 {len(paired_values) - 12} 组请在 Excel 报告中查看。")
        return "\n".join(lines)

    return f"{finding.title}：{finding.description}"


def _as_percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"
