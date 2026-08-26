from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, Signal, Slot
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
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medical_image_check.domain.image_settings import (
    IMAGE_ANALYSIS_MODE_LABELS,
    ImageAnalysisMode,
)
from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    ReviewStatus,
    RiskLevel,
    ScanResult,
)
from medical_image_check.domain.project import Project
from medical_image_check.domain.review import (
    carry_forward_review_status,
    marked_findings,
    update_finding_review_status,
)
from medical_image_check.engines.excel_exact import SUPPORTED_SPREADSHEET_EXTENSIONS
from medical_image_check.engines.image_exact import SUPPORTED_IMAGE_EXTENSIONS
from medical_image_check.infrastructure.project_store import ProjectStore
from medical_image_check.infrastructure.spreadsheets import read_spreadsheet_preview
from medical_image_check.services.basic_scan import (
    BasicScanService,
    ScanCancelled,
    ScanControl,
    ScanMode,
)
from medical_image_check.services.excel_report import ExcelReportExporter
from medical_image_check.services.feedback_export import FeedbackExporter
from medical_image_check.services.html_report import HtmlReportExporter
from medical_image_check.services.pdf_report import PdfReportExporter
from medical_image_check.services.report_common import REVIEW_LABELS

PROJECT_FILTER = "医学查重项目 (*.mic-project.json)"
IMAGE_FILE_FILTER = "支持的图片 (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)"
DATA_FILE_FILTER = "支持的表格 (*.xlsx *.xls *.xlsm *.csv)"
FEEDBACK_FILE_FILTER = "Excel 反馈清单 (*.xlsx);;JSON 反馈清单 (*.json)"
RISK_LABELS = {
    RiskLevel.HIGH: "高",
    RiskLevel.MEDIUM: "中",
    RiskLevel.LOW: "低",
}

MAIN_WINDOW_STYLE = """
QMainWindow, QWidget#appRoot {
    background: #f5f7fb;
    color: #172033;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 13px;
}
QFrame#topBar, QFrame#homeHero, QFrame#modeCard, QGroupBox {
    background: #ffffff;
    border: 1px solid #e4e9f2;
    border-radius: 12px;
}
QFrame#topBar { border-radius: 14px; }
QFrame#modeCard { min-height: 210px; }
QGroupBox {
    margin-top: 12px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #33415c;
}
QPushButton {
    min-height: 32px;
    padding: 0 14px;
    border: 1px solid #d7dfeb;
    border-radius: 8px;
    background: #ffffff;
    color: #26344d;
}
QPushButton:hover { background: #f1f5ff; border-color: #8aa9e8; }
QPushButton:pressed { background: #e7efff; }
QPushButton:disabled { color: #9ca8ba; background: #f5f7fa; border-color: #e7ebf1; }
QPushButton[role="primary"] {
    background: #356ae6;
    color: #ffffff;
    border-color: #356ae6;
    font-weight: 600;
}
QPushButton[role="primary"]:hover { background: #2859cc; border-color: #2859cc; }
QPushButton[role="nav"] { border: 0; background: transparent; font-weight: 600; }
QPushButton[role="nav"]:checked { background: #e8efff; color: #2457c5; }
QListWidget, QTableWidget, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #dce3ed;
    border-radius: 7px;
    selection-background-color: #dce8ff;
    selection-color: #172033;
}
QListWidget { padding: 6px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { min-height: 30px; padding: 0 6px; }
QHeaderView::section {
    background: #f3f6fb;
    color: #53627a;
    border: 0;
    border-bottom: 1px solid #dfe5ef;
    padding: 8px;
    font-weight: 600;
}
QProgressBar {
    border: 0;
    border-radius: 5px;
    background: #e8edf5;
    min-height: 10px;
    max-height: 10px;
    text-align: center;
}
QProgressBar::chunk { background: #4b7bec; border-radius: 5px; }
"""


class ImageEvidenceView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self._source_path: str | None = None
        self._page = 1
        self._image = QImage()
        self._region: tuple[int, int, int, int] | None = None
        self._crop_to_region = False

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

    def set_crop_to_region(self, enabled: bool) -> None:
        self._crop_to_region = enabled
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
        if self._crop_to_region and self._region is not None:
            page_text += " · 匹配区域"
        painter.drawText(8, 20, f"{Path(self._source_path).name}{page_text}")
        display_image = self._image
        if self._crop_to_region and self._region is not None:
            x, y, width, height = self._region
            x = max(0, min(x, self._image.width() - 1))
            y = max(0, min(y, self._image.height() - 1))
            width = max(1, min(width, self._image.width() - x))
            height = max(1, min(height, self._image.height() - y))
            display_image = self._image.copy(x, y, width, height)
        available = QRectF(8, 28, max(1, self.width() - 16), max(1, self.height() - 36))
        image_ratio = display_image.width() / max(display_image.height(), 1)
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
        painter.drawImage(target, display_image)
        border_color = "#ffb000" if self._crop_to_region else "#e12d39"
        painter.setPen(QPen(QColor(border_color), 3))
        painter.drawRect(target)
        if self._region is not None and not self._crop_to_region:
            x, y, width, height = self._region
            evidence_rect = QRectF(
                target.x() + x * target.width() / self._image.width(),
                target.y() + y * target.height() / self._image.height(),
                width * target.width() / self._image.width(),
                height * target.height() / self._image.height(),
            )
            painter.setPen(QPen(QColor("#ffb000"), 4))
            painter.drawRect(evidence_rect)


class SpreadsheetEvidenceView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self._title = QLabel("请选择一条表格结果")
        self._title.setStyleSheet("color: #42526b; font-weight: 600;")
        self._title.setWordWrap(True)
        self._table = QTableWidget()
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(220)
        layout.addWidget(self._title)
        layout.addWidget(self._table, 1)

    def set_evidence(self, location: EvidenceLocation | None) -> None:
        self._table.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        if location is None:
            self._title.setText("请选择一条表格结果")
            return
        try:
            preview = read_spreadsheet_preview(location)
        except Exception as exc:  # noqa: BLE001 - preview failure must not hide scan evidence
            self._title.setText(f"{Path(location.source_path).name} · 无法读取原表预览")
            self._table.setRowCount(1)
            self._table.setColumnCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(str(exc)))
            return

        self._title.setText(
            f"{Path(preview.source_path).name} · {preview.sheet} · {location.coordinate or '-'}"
        )
        row_count = len(preview.values)
        column_count = max((len(row) for row in preview.values), default=0)
        self._table.setRowCount(row_count)
        self._table.setColumnCount(column_count)
        self._table.setVerticalHeaderLabels(
            [str(preview.start_row + index) for index in range(row_count)]
        )
        self._table.setHorizontalHeaderLabels(
            [_column_label(preview.start_column + index) for index in range(column_count)]
        )
        for row_index, row in enumerate(preview.values):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                absolute_row = preview.start_row + row_index
                absolute_column = preview.start_column + column_index
                if preview.is_target(absolute_row, absolute_column):
                    item.setBackground(QColor("#fff1a8"))
                    item.setForeground(QColor("#5a4300"))
                    item.setToolTip("当前候选命中的原始单元格")
                self._table.setItem(row_index, column_index, item)
        self._table.resizeColumnsToContents()
        for column in range(column_count):
            self._table.setColumnWidth(column, min(180, max(64, self._table.columnWidth(column))))

    def clear(self) -> None:
        self.set_evidence(None)


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        sources: list[str],
        minimum_digit_run: int,
        western_single_band_enabled: bool,
        image_analysis_mode: str,
        excel_custom_relative_tolerance_percent: float,
        excel_absolute_tolerance: str,
        excel_operation_targets: tuple[str, ...],
        excel_medium_run_length: int,
        excel_high_run_length: int,
        scan_mode: ScanMode,
    ) -> None:
        super().__init__()
        self._sources = sources
        self._minimum_digit_run = minimum_digit_run
        self._western_single_band_enabled = western_single_band_enabled
        self._image_analysis_mode = image_analysis_mode
        self._excel_custom_relative_tolerance_percent = excel_custom_relative_tolerance_percent
        self._excel_absolute_tolerance = excel_absolute_tolerance
        self._excel_operation_targets = excel_operation_targets
        self._excel_medium_run_length = excel_medium_run_length
        self._excel_high_run_length = excel_high_run_length
        self._scan_mode = scan_mode
        self.control = ScanControl()

    @Slot()
    def run(self) -> None:
        try:
            result = BasicScanService(
                minimum_digit_run=self._minimum_digit_run,
                western_single_band_enabled=self._western_single_band_enabled,
                image_analysis_mode=self._image_analysis_mode,
                excel_custom_relative_tolerance_percent=(
                    self._excel_custom_relative_tolerance_percent
                ),
                excel_absolute_tolerance=self._excel_absolute_tolerance,
                excel_operation_targets=self._excel_operation_targets,
                excel_medium_run_length=self._excel_medium_run_length,
                excel_high_run_length=self._excel_high_run_length,
                scan_mode=self._scan_mode,
            ).scan(self._sources, self.progress.emit, self.control)
        except ScanCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - worker must report unexpected failures to UI
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1280, 900)
        self.setMinimumSize(1024, 720)
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._project: Project | None = None
        self._project_path: Path | None = None
        self._scan_mode = ScanMode.IMAGE
        self._current_result: ScanResult | None = None
        self._result_before_scan: ScanResult | None = None
        self._rendered_findings: list[Finding] = []
        self._selected_finding_id: str | None = None
        self._close_after_scan = False
        self._dirty = False
        self._project_store = ProjectStore()
        self._report_exporter = ExcelReportExporter()
        self._html_report_exporter = HtmlReportExporter()
        self._pdf_report_exporter = PdfReportExporter()
        self._feedback_exporter = FeedbackExporter()
        self._build_ui()
        self._update_project_state()

    def _build_ui(self) -> None:
        self._build_menu()
        central = QWidget(self)
        central.setObjectName("appRoot")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(14)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(18, 14, 18, 14)
        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(2)
        title = QLabel("科研数据查重助手")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #172033;")
        subtitle = QLabel("医学基础实验图片与表格的本地重复检测")
        subtitle.setStyleSheet("color: #718096;")
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        top_bar_layout.addLayout(brand_layout)
        top_bar_layout.addStretch(1)

        self._home_nav_button = QPushButton("首页")
        self._image_nav_button = QPushButton("图片查重")
        self._data_nav_button = QPushButton("数据查重")
        for button in (
            self._home_nav_button,
            self._image_nav_button,
            self._data_nav_button,
        ):
            button.setCheckable(True)
            button.setProperty("role", "nav")
            top_bar_layout.addWidget(button)

        top_bar_layout.addSpacing(10)
        self._new_project_button = QPushButton("新建")
        self._new_project_button.setToolTip("新建一个可保存的查重项目")
        self._open_project_button = QPushButton("打开")
        self._open_project_button.setToolTip("打开以前保存的项目")
        self._save_button = QPushButton("保存")
        self._save_button.setToolTip("保存当前输入、参数和最近结果")
        self._export_button = QPushButton("导出报告")
        self._export_button.setProperty("role", "primary")
        self._feedback_export_button = QPushButton("导出反馈")
        self._feedback_export_button.setToolTip("只导出已标记为准确、误报或正常关联的结果")
        top_bar_layout.addWidget(self._new_project_button)
        top_bar_layout.addWidget(self._open_project_button)
        top_bar_layout.addWidget(self._save_button)
        top_bar_layout.addWidget(self._export_button)
        top_bar_layout.addWidget(self._feedback_export_button)
        layout.addWidget(top_bar)

        self._project_label = QLabel()
        self._project_label.setStyleSheet("color: #60708a; padding-left: 4px;")
        layout.addWidget(self._project_label)

        self._pages = QStackedWidget()
        self._home_page = self._build_home_page()
        self._workspace_page = self._build_workspace_page()
        self._pages.addWidget(self._home_page)
        self._pages.addWidget(self._workspace_page)
        layout.addWidget(self._pages, 1)

        self._home_nav_button.clicked.connect(self._show_home)
        self._image_nav_button.clicked.connect(lambda: self._set_scan_mode(ScanMode.IMAGE))
        self._data_nav_button.clicked.connect(lambda: self._set_scan_mode(ScanMode.DATA))
        self._new_project_button.clicked.connect(self._new_project_dialog)
        self._open_project_button.clicked.connect(self._open_project_dialog)
        self._save_button.clicked.connect(self._save_current_project)
        self._export_button.clicked.connect(self._export_report_dialog)
        self._feedback_export_button.clicked.connect(self._export_feedback_dialog)
        self._add_files_button.clicked.connect(self._select_files)
        self._add_folder_button.clicked.connect(self._select_folder)
        self._clear_sources_button.clicked.connect(self._clear_sources)
        self._scan_button.clicked.connect(self._start_scan)
        self._pause_button.clicked.connect(self._toggle_scan_pause)
        self._cancel_button.clicked.connect(self._cancel_scan)
        self._digit_run_spin.valueChanged.connect(self._scan_settings_changed)
        self._western_single_band_check.toggled.connect(self._western_settings_changed)
        self._image_analysis_mode_combo.currentIndexChanged.connect(
            self._image_analysis_mode_changed
        )
        self._excel_relative_tolerance_spin.editingFinished.connect(self._excel_settings_changed)
        self._excel_absolute_tolerance_edit.editingFinished.connect(self._excel_settings_changed)
        self._excel_operation_targets_edit.editingFinished.connect(self._excel_settings_changed)
        self._excel_medium_run_spin.editingFinished.connect(self._excel_settings_changed)
        self._excel_high_run_spin.editingFinished.connect(self._excel_settings_changed)
        self._results.cellClicked.connect(self._show_selected_evidence)
        self._review_filter_combo.currentIndexChanged.connect(self._review_filter_changed)
        self._review_confirm_button.clicked.connect(
            lambda: self._set_selected_review_status(ReviewStatus.CONFIRMED)
        )
        self._review_false_positive_button.clicked.connect(
            lambda: self._set_selected_review_status(ReviewStatus.FALSE_POSITIVE)
        )
        self._review_normal_button.clicked.connect(
            lambda: self._set_selected_review_status(ReviewStatus.NORMAL)
        )
        self._review_clear_button.clicked.connect(
            lambda: self._set_selected_review_status(ReviewStatus.PENDING)
        )
        self._crop_evidence_check.toggled.connect(self._toggle_evidence_crop)
        self._copy_evidence_button.clicked.connect(self._copy_evidence_summary)
        self.setCentralWidget(central)
        self.setStyleSheet(MAIN_WINDOW_STYLE)
        self._show_home()

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 4, 0, 0)
        page_layout.setSpacing(16)

        hero = QFrame()
        hero.setObjectName("homeHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        hero_title = QLabel("选择要检查的内容")
        hero_title.setStyleSheet("font-size: 25px; font-weight: 700;")
        hero_text = QLabel(
            "图片和表格使用独立工作区。直接选择文件或文件夹即可开始，项目保存是可选的辅助功能。"
        )
        hero_text.setStyleSheet("font-size: 14px; color: #66758c;")
        hero_text.setWordWrap(True)
        hero_layout.addWidget(hero_title)
        hero_layout.addWidget(hero_text)
        page_layout.addWidget(hero)

        cards = QHBoxLayout()
        cards.setSpacing(16)
        cards.addWidget(
            self._build_mode_card(
                "图片查重",
                "检查完全重复、近似图片、局部重叠，以及 Western blot、荧光图和病理图专项候选。",
                "JPG · PNG · BMP · WebP · TIFF",
                "进入图片查重",
                ScanMode.IMAGE,
            ),
            1,
        )
        cards.addWidget(
            self._build_mode_card(
                "数据查重",
                "检查表格中的完整数值、连续片段、近似值、固定变换、运算关系和结构相似。",
                "XLSX · XLS · XLSM · CSV",
                "进入数据查重",
                ScanMode.DATA,
            ),
            1,
        )
        page_layout.addLayout(cards, 1)
        note = QLabel(
            "所有核心检测均在本机完成，不会修改原始文件。当前结果是人工复核候选，不自动等同于科研结论。"
        )
        note.setStyleSheet("color: #718096; padding: 4px;")
        note.setWordWrap(True)
        page_layout.addWidget(note)
        return page

    def _build_mode_card(
        self,
        title: str,
        description: str,
        formats: str,
        button_text: str,
        mode: ScanMode,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("modeCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(12)
        card_title = QLabel(title)
        card_title.setStyleSheet("font-size: 21px; font-weight: 700; color: #20304d;")
        card_description = QLabel(description)
        card_description.setWordWrap(True)
        card_description.setStyleSheet("font-size: 14px; color: #5f6f87;")
        card_formats = QLabel(formats)
        card_formats.setStyleSheet("color: #356ae6; font-weight: 600;")
        entry_button = QPushButton(button_text)
        entry_button.setProperty("role", "primary")
        entry_button.setMinimumHeight(40)
        entry_button.clicked.connect(lambda: self._set_scan_mode(mode))
        card_layout.addWidget(card_title)
        card_layout.addWidget(card_description)
        card_layout.addWidget(card_formats)
        card_layout.addStretch(1)
        card_layout.addWidget(entry_button)
        return card

    def _build_workspace_page(self) -> QWidget:
        content = QWidget()
        content.setObjectName("appRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        workspace_header = QHBoxLayout()
        workspace_text = QVBoxLayout()
        workspace_text.setSpacing(2)
        self._mode_title = QLabel()
        self._mode_title.setStyleSheet("font-size: 23px; font-weight: 700;")
        self._mode_subtitle = QLabel()
        self._mode_subtitle.setStyleSheet("color: #66758c;")
        self._mode_subtitle.setWordWrap(True)
        workspace_text.addWidget(self._mode_title)
        workspace_text.addWidget(self._mode_subtitle)
        workspace_header.addLayout(workspace_text)
        workspace_header.addStretch(1)
        layout.addLayout(workspace_header)

        self._input_group = QGroupBox()
        input_layout = QVBoxLayout(self._input_group)
        actions = QHBoxLayout()
        self._add_files_button = QPushButton("添加文件")
        self._add_files_button.setProperty("role", "primary")
        self._add_folder_button = QPushButton("添加文件夹")
        self._clear_sources_button = QPushButton("清空输入")
        self._scan_button = QPushButton("开始扫描")
        self._scan_button.setProperty("role", "primary")
        self._pause_button = QPushButton("暂停")
        self._pause_button.setEnabled(False)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setEnabled(False)
        actions.addWidget(self._add_files_button)
        actions.addWidget(self._add_folder_button)
        actions.addWidget(self._clear_sources_button)
        actions.addStretch(1)
        actions.addWidget(self._scan_button)
        actions.addWidget(self._pause_button)
        actions.addWidget(self._cancel_button)
        input_layout.addLayout(actions)

        self._sources_label = QLabel()
        self._sources_label.setStyleSheet("color: #718096;")
        input_layout.addWidget(self._sources_label)
        self._sources = QListWidget()
        self._sources.setMinimumHeight(78)
        self._sources.setMaximumHeight(120)
        input_layout.addWidget(self._sources)
        layout.addWidget(self._input_group)

        self._image_settings_group = QGroupBox("图片检测设置")
        image_settings = QHBoxLayout(self._image_settings_group)
        image_settings.addWidget(QLabel("内容类型："))
        self._image_analysis_mode_combo = QComboBox()
        for mode, label in IMAGE_ANALYSIS_MODE_LABELS.items():
            self._image_analysis_mode_combo.addItem(label, mode.value)
        self._image_analysis_mode_combo.setToolTip(
            "自动模式会先判断图像类型再运行专项算法；已知类型时可直接指定，减少串类误报。"
        )
        image_settings.addWidget(self._image_analysis_mode_combo)
        self._western_single_band_check = QCheckBox("检测 Western blot 单条带相似")
        self._western_single_band_check.setToolTip(
            "默认关闭；单条带自然相似较常见，启用后只生成低风险人工复核候选。"
        )
        image_settings.addWidget(self._western_single_band_check)
        image_settings.addWidget(QLabel("通用重复检测始终运行；专项算法按所选类型运行。"))
        image_settings.addStretch(1)
        layout.addWidget(self._image_settings_group)

        self._excel_settings_group = QGroupBox("数据检测设置")
        excel_settings_layout = QVBoxLayout(self._excel_settings_group)
        excel_settings_first_row = QHBoxLayout()
        excel_settings_first_row.addWidget(QLabel("数字片段最短报警位数："))
        self._digit_run_spin = QSpinBox()
        self._digit_run_spin.setRange(3, 12)
        self._digit_run_spin.setValue(4)
        self._digit_run_spin.setToolTip("默认 4 位；数值越小召回越多，低风险结果也会明显增加。")
        excel_settings_first_row.addWidget(self._digit_run_spin)
        excel_settings_first_row.addWidget(QLabel("自定义相对容差："))
        self._excel_relative_tolerance_spin = QDoubleSpinBox()
        self._excel_relative_tolerance_spin.setRange(0, 100)
        self._excel_relative_tolerance_spin.setDecimals(4)
        self._excel_relative_tolerance_spin.setSingleStep(0.01)
        self._excel_relative_tolerance_spin.setSuffix(" %")
        self._excel_relative_tolerance_spin.setToolTip(
            "0 表示仅使用内置 0.01%、0.1%、1% 档位；非零值也用于变换关系容差。"
        )
        excel_settings_first_row.addWidget(self._excel_relative_tolerance_spin)
        excel_settings_first_row.addWidget(QLabel("绝对容差："))
        self._excel_absolute_tolerance_edit = QLineEdit()
        self._excel_absolute_tolerance_edit.setMaximumWidth(150)
        self._excel_absolute_tolerance_edit.setToolTip("默认 1e-12，必须是大于 0 的有限数值。")
        excel_settings_first_row.addWidget(self._excel_absolute_tolerance_edit)
        excel_settings_first_row.addStretch(1)
        excel_settings_layout.addLayout(excel_settings_first_row)

        excel_settings_second_row = QHBoxLayout()
        excel_settings_second_row.addWidget(QLabel("运算目标："))
        self._excel_operation_targets_edit = QLineEdit()
        self._excel_operation_targets_edit.setPlaceholderText("0, 1, 10, 100, 1000")
        self._excel_operation_targets_edit.setToolTip(
            "用逗号或空格分隔，最多 20 个；连续关系还会检测任意整数目标。"
        )
        excel_settings_second_row.addWidget(self._excel_operation_targets_edit, 1)

        excel_settings_second_row.addWidget(QLabel("连续关系中风险起点："))
        self._excel_medium_run_spin = QSpinBox()
        self._excel_medium_run_spin.setRange(2, 20)
        excel_settings_second_row.addWidget(self._excel_medium_run_spin)
        excel_settings_second_row.addWidget(QLabel("高风险起点："))
        self._excel_high_run_spin = QSpinBox()
        self._excel_high_run_spin.setRange(3, 50)
        excel_settings_second_row.addWidget(self._excel_high_run_spin)
        excel_settings_second_row.addWidget(QLabel("统计分布相似始终仅作低风险人工复核提示。"))
        excel_settings_layout.addLayout(excel_settings_second_row)
        layout.addWidget(self._excel_settings_group)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._status)
        layout.addWidget(self._progress)

        self._results_group = QGroupBox("候选结果")
        results_layout = QVBoxLayout(self._results_group)
        review_toolbar = QHBoxLayout()
        review_toolbar.addWidget(QLabel("复核状态："))
        self._review_filter_combo = QComboBox()
        self._review_filter_combo.addItem("全部", "all")
        for status in ReviewStatus:
            self._review_filter_combo.addItem(REVIEW_LABELS[status], status.value)
        review_toolbar.addWidget(self._review_filter_combo)
        self._review_summary_label = QLabel("尚无结果")
        self._review_summary_label.setStyleSheet("color: #66758c;")
        review_toolbar.addWidget(self._review_summary_label)
        review_toolbar.addStretch(1)
        self._review_confirm_button = QPushButton("准确")
        self._review_false_positive_button = QPushButton("误报")
        self._review_normal_button = QPushButton("正常关联")
        self._review_clear_button = QPushButton("清除标记")
        for button in (
            self._review_confirm_button,
            self._review_false_positive_button,
            self._review_normal_button,
            self._review_clear_button,
        ):
            button.setEnabled(False)
            review_toolbar.addWidget(button)
        results_layout.addLayout(review_toolbar)
        self._results = QTableWidget(0, 5)
        self._results.setHorizontalHeaderLabels(["风险", "类型", "复核", "说明", "位置"])
        self._results.horizontalHeader().setStretchLastSection(True)
        self._results.verticalHeader().setVisible(False)
        self._results.setAlternatingRowColors(True)
        self._results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._results.setMinimumHeight(150)
        results_layout.addWidget(self._results)

        self._evidence_group = QGroupBox("结果证据预览")
        evidence_layout = QVBoxLayout(self._evidence_group)
        self._evidence_images_container = QWidget()
        evidence_images = QHBoxLayout(self._evidence_images_container)
        evidence_images.setContentsMargins(0, 0, 0, 0)
        self._first_evidence = ImageEvidenceView()
        self._second_evidence = ImageEvidenceView()
        evidence_images.addWidget(self._first_evidence, 1)
        evidence_images.addWidget(self._second_evidence, 1)
        self._spreadsheet_evidence_container = QWidget()
        spreadsheet_evidence = QHBoxLayout(self._spreadsheet_evidence_container)
        spreadsheet_evidence.setContentsMargins(0, 0, 0, 0)
        self._first_spreadsheet_evidence = SpreadsheetEvidenceView()
        self._second_spreadsheet_evidence = SpreadsheetEvidenceView()
        spreadsheet_evidence.addWidget(self._first_spreadsheet_evidence, 1)
        spreadsheet_evidence.addWidget(self._second_spreadsheet_evidence, 1)
        self._evidence_summary = QLabel("选择一条结果后显示图像或数值证据。")
        self._evidence_summary.setWordWrap(True)
        self._evidence_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        evidence_actions = QHBoxLayout()
        self._crop_evidence_check = QCheckBox("聚焦匹配区域")
        self._crop_evidence_check.setEnabled(False)
        self._copy_evidence_button = QPushButton("复制证据摘要")
        self._copy_evidence_button.setEnabled(False)
        evidence_actions.addWidget(self._crop_evidence_check)
        evidence_actions.addWidget(self._copy_evidence_button)
        evidence_actions.addStretch(1)
        evidence_layout.addWidget(self._evidence_images_container)
        evidence_layout.addWidget(self._spreadsheet_evidence_container)
        evidence_layout.addWidget(self._evidence_summary)
        evidence_layout.addLayout(evidence_actions)
        self._result_evidence_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._result_evidence_splitter.addWidget(self._results_group)
        self._result_evidence_splitter.addWidget(self._evidence_group)
        self._result_evidence_splitter.setStretchFactor(0, 3)
        self._result_evidence_splitter.setStretchFactor(1, 2)
        layout.addWidget(self._result_evidence_splitter, 1)
        page = QScrollArea()
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setWidgetResizable(True)
        page.setWidget(content)
        return page

    @Slot()
    def _show_home(self) -> None:
        if self._thread is not None:
            return
        self._pages.setCurrentWidget(self._home_page)
        self._home_nav_button.setChecked(True)
        self._image_nav_button.setChecked(False)
        self._data_nav_button.setChecked(False)

    def _set_scan_mode(self, mode: ScanMode) -> None:
        if self._thread is not None:
            return
        self._scan_mode = mode
        self._pages.setCurrentWidget(self._workspace_page)
        is_image = mode == ScanMode.IMAGE
        self._home_nav_button.setChecked(False)
        self._image_nav_button.setChecked(is_image)
        self._data_nav_button.setChecked(not is_image)
        self._image_settings_group.setVisible(is_image)
        self._excel_settings_group.setVisible(not is_image)
        self._result_evidence_splitter.setOrientation(
            Qt.Orientation.Horizontal if is_image else Qt.Orientation.Vertical
        )
        if is_image:
            self._mode_title.setText("图片查重")
            self._mode_subtitle.setText(
                "只处理常规静态图片；检测完全重复、近似、局部重叠及三类医学专项候选。"
            )
            self._input_group.setTitle("选择要检查的图片")
            self._add_files_button.setText("添加图片")
            self._add_folder_button.setText("添加图片文件夹")
            self._scan_button.setText("开始图片查重")
            self._results_group.setTitle("图片候选结果")
            self._evidence_group.setTitle("双图证据预览")
            self._sources_label.setText(
                "支持 JPG、JPEG、PNG、BMP、WebP、TIFF；文件夹会自动忽略表格。"
            )
        else:
            self._mode_title.setText("数据查重")
            self._mode_subtitle.setText(
                "只处理 Excel/CSV 的数值内容；文本、日期和空白默认不参与比较。"
            )
            self._input_group.setTitle("选择要检查的表格")
            self._add_files_button.setText("添加表格")
            self._add_folder_button.setText("添加表格文件夹")
            self._scan_button.setText("开始数据查重")
            self._results_group.setTitle("数据候选结果")
            self._evidence_group.setTitle("数值证据预览")
            self._sources_label.setText("支持 XLSX、XLS、XLSM、CSV；文件夹会自动忽略图片。")
        self._refresh_source_list()
        self._render_result(self._current_result)
        count = self._sources.count()
        noun = "图片输入" if is_image else "表格输入"
        self._status.setText(
            f"当前有 {count} 个{noun}路径，可继续添加后开始扫描。"
            if count
            else f"请添加{noun}；无需先创建或保存项目。"
        )
        self._update_project_state()

    def _refresh_source_list(self) -> None:
        self._sources.clear()
        if self._project is None:
            return
        for source in self._project.source_paths:
            if self._source_matches_mode(source):
                self._sources.addItem(source)

    def _source_matches_mode(self, source: str | Path) -> bool:
        path = Path(source)
        if path.is_dir() or not path.suffix:
            return True
        if self._scan_mode == ScanMode.IMAGE:
            return path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        return path.suffix.lower() in SUPPORTED_SPREADSHEET_EXTENSIONS

    @staticmethod
    def _infer_project_mode(project: Project) -> ScanMode:
        image_count = sum(
            Path(source).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            for source in project.source_paths
        )
        data_count = sum(
            Path(source).suffix.lower() in SUPPORTED_SPREADSHEET_EXTENSIONS
            for source in project.source_paths
        )
        if project.last_scan_result is not None:
            image_count += project.last_scan_result.image_count
            data_count += project.last_scan_result.spreadsheet_count
        return ScanMode.DATA if data_count > image_count else ScanMode.IMAGE

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        actions = [
            ("新建项目", QKeySequence.StandardKey.New, self._new_project_dialog),
            ("打开项目", QKeySequence.StandardKey.Open, self._open_project_dialog),
            ("保存项目", QKeySequence.StandardKey.Save, self._save_current_project),
            ("项目另存为", QKeySequence.StandardKey.SaveAs, self._save_project_as),
            ("导出报告…", "Ctrl+E", self._export_report_dialog),
            ("导出反馈清单…", "Ctrl+Shift+E", self._export_feedback_dialog),
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
        self._load_project_settings(self._project)
        self._refresh_source_list()
        self._render_result(None)
        self._dirty = True
        kind = "图片" if self._scan_mode == ScanMode.IMAGE else "表格"
        self._status.setText(f"项目已创建，请添加要检查的{kind}或文件夹。")
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
        self._load_project_settings(project)
        self._dirty = False
        self._set_scan_mode(self._infer_project_mode(project))
        self._refresh_source_list()
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

    def _load_project_settings(self, project: Project) -> None:
        controls = (
            self._digit_run_spin,
            self._western_single_band_check,
            self._image_analysis_mode_combo,
            self._excel_relative_tolerance_spin,
            self._excel_absolute_tolerance_edit,
            self._excel_operation_targets_edit,
            self._excel_medium_run_spin,
            self._excel_high_run_spin,
        )
        for control in controls:
            control.blockSignals(True)
        self._digit_run_spin.setValue(project.minimum_digit_run)
        self._western_single_band_check.setChecked(project.western_single_band_enabled)
        mode_index = self._image_analysis_mode_combo.findData(project.image_analysis_mode)
        self._image_analysis_mode_combo.setCurrentIndex(max(0, mode_index))
        self._excel_relative_tolerance_spin.setValue(
            project.excel_custom_relative_tolerance_percent
        )
        self._excel_absolute_tolerance_edit.setText(project.excel_absolute_tolerance)
        self._excel_operation_targets_edit.setText(", ".join(project.excel_operation_targets))
        self._excel_medium_run_spin.setValue(project.excel_medium_run_length)
        self._excel_high_run_spin.setValue(project.excel_high_run_length)
        for control in controls:
            control.blockSignals(False)

    def export_excel_report(self, path: str | Path) -> Path:
        result = self._result_for_active_mode()
        if self._project is None or result is None or not self._active_result_available():
            raise ValueError("请先完成或打开一次扫描结果")
        output = self._report_exporter.export(result, path, self._project)
        self._project = self._project.with_report(output)
        self._dirty = True
        if self._project_path is not None:
            self._save_current_project(silent=True)
        self._status.setText(f"Excel 报告已导出：{output}")
        self._update_project_state()
        return output

    def export_html_report(self, path: str | Path) -> Path:
        result = self._result_for_active_mode()
        if self._project is None or result is None or not self._active_result_available():
            raise ValueError("请先完成或打开一次扫描结果")
        output = self._html_report_exporter.export(result, path, self._project)
        self._record_report(output, "HTML")
        return output

    def export_pdf_report(self, path: str | Path) -> Path:
        result = self._result_for_active_mode()
        if self._project is None or result is None or not self._active_result_available():
            raise ValueError("请先完成或打开一次扫描结果")
        output = self._pdf_report_exporter.export(result, path, self._project)
        self._record_report(output, "PDF")
        return output

    def export_feedback(self, path: str | Path) -> Path:
        result = self._result_for_active_mode()
        if self._project is None or result is None:
            raise ValueError("请先完成或打开一次扫描结果")
        output = self._feedback_exporter.export(result, path, self._project)
        count = len(marked_findings(result))
        self._status.setText(f"已导出 {count} 条人工反馈：{output}")
        return output

    def _record_report(self, output: Path, report_type: str) -> None:
        if self._project is None:
            return
        self._project = self._project.with_report(output)
        self._dirty = True
        if self._project_path is not None:
            self._save_current_project(silent=True)
        self._status.setText(f"{report_type} 报告已导出：{output}")
        self._update_project_state()

    @Slot()
    def _new_project_dialog(self) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "扫描正在运行", "请先暂停后取消扫描，再切换项目。")
            return
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
        if self._thread is not None:
            QMessageBox.information(self, "扫描正在运行", "请先暂停后取消扫描，再切换项目。")
            return
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
        if not self._active_result_available():
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
    def _export_report_dialog(self) -> None:
        if not self._active_result_available():
            QMessageBox.information(self, "没有扫描结果", "请先完成扫描或打开已有结果的项目。")
            return
        project_name = self._project.name if self._project else "查重"
        filters = "Excel 工作簿 (*.xlsx);;HTML 单文件 (*.html);;PDF 文档 (*.pdf)"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出查重报告",
            f"{project_name}-查重报告.xlsx",
            filters,
        )
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix in {".html", ".htm"} or selected_filter.startswith("HTML"):
                output = self.export_html_report(path)
            elif suffix == ".pdf" or selected_filter.startswith("PDF"):
                output = self.export_pdf_report(path)
            else:
                output = self.export_excel_report(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "报告导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"报告已保存到：\n{output}")

    @Slot()
    def _export_feedback_dialog(self) -> None:
        result = self._result_for_active_mode()
        if result is None or not marked_findings(result):
            QMessageBox.information(
                self,
                "没有人工反馈",
                "请先将至少一条结果标记为准确、误报或正常关联。",
            )
            return
        project_name = self._project.name if self._project else "查重"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出算法反馈清单",
            f"{project_name}-算法反馈.xlsx",
            FEEDBACK_FILE_FILTER,
        )
        if not path:
            return
        if selected_filter.startswith("JSON") and Path(path).suffix.lower() != ".json":
            path = str(Path(path).with_suffix(".json"))
        try:
            output = self.export_feedback(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "反馈导出失败", str(exc))
            return
        QMessageBox.information(
            self,
            "导出完成",
            f"反馈清单已保存到：\n{output}\n\n原始图片和表格没有复制到清单中。",
        )

    @Slot()
    def _select_files(self) -> None:
        is_image = self._scan_mode == ScanMode.IMAGE
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要检查的图片" if is_image else "选择要检查的表格",
            "",
            IMAGE_FILE_FILTER if is_image else DATA_FILE_FILTER,
        )
        self._append_sources(paths)

    @Slot()
    def _select_folder(self) -> None:
        kind = "图片" if self._scan_mode == ScanMode.IMAGE else "表格"
        path = QFileDialog.getExistingDirectory(self, f"选择包含{kind}的文件夹")
        if path:
            self._append_sources([path])

    def _append_sources(self, paths: list[str]) -> None:
        if not paths:
            return
        normalized_paths = [str(Path(path).expanduser().resolve()) for path in paths]
        accepted = [path for path in normalized_paths if self._source_matches_mode(path)]
        rejected_count = len(normalized_paths) - len(accepted)
        if not accepted:
            kind = "图片" if self._scan_mode == ScanMode.IMAGE else "Excel/CSV 表格"
            self._status.setText(f"未添加文件：当前工作区只接受{kind}。")
            return
        if self._project is None:
            name = "未命名图片查重" if self._scan_mode == ScanMode.IMAGE else "未命名数据查重"
            self.create_project(name)
        existing = set(self._project.source_paths) if self._project else set()
        added: list[str] = []
        for path in accepted:
            if path not in existing:
                existing.add(path)
                added.append(path)
        if added and self._project is not None:
            self._project = self._project.with_sources(added)
            self._current_result = None
            self._refresh_source_list()
            self._render_result(None)
            self._mark_dirty()
            suffix = f"；另有 {rejected_count} 个不匹配类型已忽略" if rejected_count else ""
            self._status.setText(f"已添加 {len(added)} 个输入路径{suffix}，原扫描结果已失效。")
        elif rejected_count:
            self._status.setText(f"有 {rejected_count} 个不匹配当前工作区的文件已忽略。")

    @Slot()
    def _clear_sources(self) -> None:
        if self._sources.count() == 0:
            return
        if self._project is not None:
            visible = {self._sources.item(index).text() for index in range(self._sources.count())}
            remaining = [source for source in self._project.source_paths if source not in visible]
            self._project = self._project.replace_sources(remaining)
            self._current_result = None
            self._refresh_source_list()
            self._render_result(None)
            self._mark_dirty()
        self._status.setText("当前工作区的输入已清空。")

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
        self._result_before_scan = self._current_result
        self._scan_button.setEnabled(False)
        self._pause_button.setEnabled(True)
        self._pause_button.setText("暂停")
        self._cancel_button.setEnabled(True)
        self._thread = QThread(self)
        minimum_digit_run = self._project.minimum_digit_run if self._project else 4
        western_single_band_enabled = (
            self._project.western_single_band_enabled if self._project else False
        )
        image_analysis_mode = (
            self._project.image_analysis_mode if self._project else ImageAnalysisMode.AUTO.value
        )
        self._worker = ScanWorker(
            sources,
            minimum_digit_run,
            western_single_band_enabled,
            image_analysis_mode,
            self._project.excel_custom_relative_tolerance_percent,
            self._project.excel_absolute_tolerance,
            self._project.excel_operation_targets,
            self._project.excel_medium_run_length,
            self._project.excel_high_run_length,
            self._scan_mode,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._update_progress)
        self._worker.finished.connect(self._show_result)
        self._worker.failed.connect(self._show_failure)
        self._worker.cancelled.connect(self._show_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()
        self._update_project_state()

    @Slot()
    def _toggle_scan_pause(self) -> None:
        if self._worker is None:
            return
        if self._worker.control.paused:
            self._worker.control.resume()
            self._pause_button.setText("暂停")
            self._status.setText("扫描已继续，正在等待下一个进度更新。")
        else:
            self._worker.control.pause()
            self._pause_button.setText("继续")
            self._status.setText("正在安全暂停；当前文件或验证批次结束后暂停。")

    @Slot()
    def _cancel_scan(self) -> None:
        if self._worker is None:
            return
        self._worker.control.cancel()
        self._pause_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._status.setText("正在安全取消；本次未完成结果不会覆盖上一次扫描。")

    @Slot(int, int, str)
    def _update_progress(self, completed: int, total: int, message: str) -> None:
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(completed)
        self._status.setText(message)

    @Slot(object)
    def _show_result(self, result: ScanResult) -> None:
        result = carry_forward_review_status(self._result_before_scan, result)
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

    @Slot()
    def _show_cancelled(self) -> None:
        self._current_result = self._result_before_scan
        self._render_result(self._current_result)
        if self._current_result is None:
            self._status.setText("扫描已取消，本次未完成结果已丢弃。")
        else:
            self._status.setText("扫描已取消，已恢复扫描前的完整结果。")

    def _render_result(self, result: ScanResult | None) -> None:
        self._results.setRowCount(0)
        self._rendered_findings = []
        self._clear_evidence()
        if result is None:
            self._review_summary_label.setText("尚无结果")
            return
        active_findings = [
            finding for finding in result.findings if self._finding_matches_mode(finding)
        ]
        review_filter = str(self._review_filter_combo.currentData())
        for finding in result.findings:
            if not self._finding_matches_mode(finding):
                continue
            if review_filter != "all" and finding.review_status.value != review_filter:
                continue
            self._rendered_findings.append(finding)
            row = self._results.rowCount()
            self._results.insertRow(row)
            locations = "\n".join(location.display_text for location in finding.locations)
            values = [
                RISK_LABELS[finding.risk],
                finding.title,
                REVIEW_LABELS[finding.review_status],
                finding.description,
                locations,
            ]
            for column, value in enumerate(values):
                self._results.setItem(row, column, QTableWidgetItem(value))
        marked_count = sum(
            finding.review_status != ReviewStatus.PENDING for finding in active_findings
        )
        self._review_summary_label.setText(
            f"显示 {len(self._rendered_findings)} / {len(active_findings)}；已标记 {marked_count}"
        )

    @Slot(int)
    def _review_filter_changed(self, index: int) -> None:
        del index
        self._render_result(self._current_result)

    def _set_selected_review_status(self, status: ReviewStatus) -> None:
        if self._current_result is None or self._selected_finding_id is None:
            return
        finding_id = self._selected_finding_id
        updated = update_finding_review_status(self._current_result, finding_id, status)
        if updated is self._current_result:
            return
        self._current_result = updated
        if self._project is not None:
            self._project = self._project.with_scan_result(updated)
        self._mark_dirty()
        self._render_result(updated)
        selected_row = next(
            (
                row
                for row, finding in enumerate(self._rendered_findings)
                if finding.finding_id == finding_id
            ),
            None,
        )
        if selected_row is not None:
            self._results.selectRow(selected_row)
            self._show_selected_evidence(selected_row)
        if self._project_path is not None:
            self._save_current_project(silent=True)
        self._status.setText(f"结果已标记为“{REVIEW_LABELS[status]}”，反馈仅保存在本地。")
        self._update_project_state()

    def _finding_matches_mode(self, finding: Finding) -> bool:
        is_excel = finding.rule_id.startswith("excel.")
        return is_excel == (self._scan_mode == ScanMode.DATA)

    def _active_result_available(self) -> bool:
        if self._current_result is None:
            return False
        if self._scan_mode == ScanMode.IMAGE:
            return self._current_result.image_count > 0
        return self._current_result.spreadsheet_count > 0

    def _result_for_active_mode(self) -> ScanResult | None:
        result = self._current_result
        if result is None:
            return None
        findings = tuple(
            finding for finding in result.findings if self._finding_matches_mode(finding)
        )
        issues = tuple(
            issue
            for issue in result.issues
            if self._issue_matches_mode(Path(issue.source_path).suffix.lower())
        )
        image_count = result.image_count if self._scan_mode == ScanMode.IMAGE else 0
        spreadsheet_count = result.spreadsheet_count if self._scan_mode == ScanMode.DATA else 0
        return ScanResult(
            source_count=image_count + spreadsheet_count,
            image_count=image_count,
            spreadsheet_count=spreadsheet_count,
            findings=findings,
            issues=issues,
            algorithm_version=result.algorithm_version,
            completed_at=result.completed_at,
        )

    def _issue_matches_mode(self, suffix: str) -> bool:
        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            return self._scan_mode == ScanMode.IMAGE
        if suffix in SUPPORTED_SPREADSHEET_EXTENSIONS:
            return self._scan_mode == ScanMode.DATA
        return True

    @Slot(int, int)
    def _show_selected_evidence(self, row: int, column: int = 0) -> None:
        del column
        if row < 0 or row >= len(self._rendered_findings):
            self._clear_evidence()
            return
        finding = self._rendered_findings[row]
        self._selected_finding_id = finding.finding_id
        self._set_review_controls(finding.review_status)
        if finding.rule_id.startswith("excel."):
            self._evidence_images_container.hide()
            self._spreadsheet_evidence_container.show()
            self._first_spreadsheet_evidence.set_evidence(
                finding.locations[0] if finding.locations else None
            )
            self._second_spreadsheet_evidence.set_evidence(
                finding.locations[1] if len(finding.locations) > 1 else None
            )
            self._evidence_summary.setText(_excel_evidence_summary_text(finding))
            self._crop_evidence_check.setEnabled(False)
            self._copy_evidence_button.setEnabled(True)
            QTimer.singleShot(
                0,
                lambda: self._workspace_page.ensureWidgetVisible(self._evidence_group, 0, 12),
            )
            return
        if len(finding.locations) < 2 or not all(
            Path(location.source_path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            for location in finding.locations[:2]
        ):
            self._clear_evidence("当前结果不是可并排预览的双图像证据。")
            return

        self._evidence_images_container.show()
        self._spreadsheet_evidence_container.hide()
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
        self._crop_evidence_check.setEnabled(first_region is not None or second_region is not None)
        self._copy_evidence_button.setEnabled(True)

    def _clear_evidence(self, message: str | None = None) -> None:
        self._selected_finding_id = None
        self._evidence_images_container.setVisible(self._scan_mode == ScanMode.IMAGE)
        self._spreadsheet_evidence_container.setVisible(self._scan_mode == ScanMode.DATA)
        self._first_evidence.set_evidence(None)
        self._second_evidence.set_evidence(None)
        self._first_spreadsheet_evidence.clear()
        self._second_spreadsheet_evidence.clear()
        default_message = (
            "选择一条图片结果后显示双图和匹配区域。"
            if self._scan_mode == ScanMode.IMAGE
            else "选择一条数据结果后显示单元格、完整数值和规则证据。"
        )
        self._evidence_summary.setText(message or default_message)
        self._crop_evidence_check.setEnabled(False)
        self._copy_evidence_button.setEnabled(False)
        self._set_review_controls(None)

    def _set_review_controls(self, status: ReviewStatus | None) -> None:
        enabled = status is not None and self._thread is None
        for button in (
            self._review_confirm_button,
            self._review_false_positive_button,
            self._review_normal_button,
            self._review_clear_button,
        ):
            button.setEnabled(enabled)
        self._review_clear_button.setEnabled(enabled and status != ReviewStatus.PENDING)

    @Slot(bool)
    def _toggle_evidence_crop(self, enabled: bool) -> None:
        self._first_evidence.set_crop_to_region(enabled)
        self._second_evidence.set_crop_to_region(enabled)

    @Slot()
    def _copy_evidence_summary(self) -> None:
        QApplication.clipboard().setText(self._evidence_summary.text())
        self._status.setText("证据摘要已复制到剪贴板。")

    @Slot(str)
    def _show_failure(self, message: str) -> None:
        self._current_result = self._result_before_scan
        self._render_result(self._current_result)
        self._status.setText("扫描失败，已恢复扫描前的完整结果。")
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

    @Slot(int)
    def _image_analysis_mode_changed(self, index: int) -> None:
        if self._project is None or index < 0:
            return
        mode = str(self._image_analysis_mode_combo.itemData(index))
        if self._project.image_analysis_mode == mode:
            return
        self._project = self._project.with_image_analysis_mode(mode)
        self._current_result = None
        self._render_result(None)
        self._mark_dirty()
        label = self._image_analysis_mode_combo.itemText(index)
        self._status.setText(f"图片内容类型已改为“{label}”，原扫描结果已失效。")

    @Slot()
    def _excel_settings_changed(self) -> None:
        if self._project is None:
            return
        targets = tuple(
            item
            for item in re.split(r"[,，;；\s]+", self._excel_operation_targets_edit.text().strip())
            if item
        )
        try:
            updated = self._project.with_excel_analysis_settings(
                self._excel_relative_tolerance_spin.value(),
                self._excel_absolute_tolerance_edit.text().strip(),
                targets,
                self._excel_medium_run_spin.value(),
                self._excel_high_run_spin.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Excel 参数无效", str(exc))
            self._load_project_settings(self._project)
            return
        if updated == self._project:
            return
        self._project = updated
        self._load_project_settings(updated)
        self._current_result = None
        self._render_result(None)
        self._mark_dirty()
        self._status.setText("Excel 高级规则参数已更新，原扫描结果已失效。")

    @Slot()
    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._result_before_scan = None
        self._scan_button.setEnabled(True)
        self._pause_button.setText("暂停")
        self._pause_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._update_project_state()
        if self._close_after_scan:
            self._close_after_scan = False
            QTimer.singleShot(0, self.close)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_project_state()

    def _update_project_state(self) -> None:
        name = self._project.name if self._project else "临时任务"
        marker = " *" if self._dirty else ""
        if self._project is None:
            path_text = "添加文件后可直接扫描，无需先保存项目"
        elif self._project_path is None:
            path_text = "尚未保存（可选）"
        else:
            path_text = str(self._project_path)
        self._project_label.setText(f"当前任务：{name}{marker}  ·  {path_text}")
        self.setWindowTitle(f"科研数据查重助手 · {name}{marker}")
        scan_running = self._thread is not None
        self._home_nav_button.setEnabled(not scan_running)
        self._image_nav_button.setEnabled(not scan_running)
        self._data_nav_button.setEnabled(not scan_running)
        self._save_button.setEnabled(self._project is not None and not scan_running)
        self._new_project_button.setEnabled(not scan_running)
        self._open_project_button.setEnabled(not scan_running)
        self._add_files_button.setEnabled(not scan_running)
        self._add_folder_button.setEnabled(not scan_running)
        self._clear_sources_button.setEnabled(not scan_running)
        self._scan_button.setEnabled(not scan_running)
        self._export_button.setEnabled(self._active_result_available() and not scan_running)
        active_result = self._result_for_active_mode()
        self._feedback_export_button.setEnabled(
            active_result is not None and bool(marked_findings(active_result)) and not scan_running
        )
        self._review_filter_combo.setEnabled(self._current_result is not None and not scan_running)
        self._digit_run_spin.setEnabled(self._project is not None and not scan_running)
        self._western_single_band_check.setEnabled(self._project is not None and not scan_running)
        self._image_analysis_mode_combo.setEnabled(self._project is not None and not scan_running)
        for control in (
            self._excel_relative_tolerance_spin,
            self._excel_absolute_tolerance_edit,
            self._excel_operation_targets_edit,
            self._excel_medium_run_spin,
            self._excel_high_run_spin,
        ):
            control.setEnabled(self._project is not None and not scan_running)

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
        if self._thread is not None and self._thread.isRunning():
            answer = QMessageBox.question(
                self,
                "扫描仍在运行",
                "关闭窗口将安全取消本次扫描，扫描前的完整结果会保留。是否继续关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._close_after_scan = True
            self._cancel_scan()
            event.ignore()
            return
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
    if finding.rule_id.startswith("image.dot_blot."):
        return (
            f"Dot blot 证据：匹配斑点 {details.get('matched_spot_count', '-')} 个；"
            f"排列相似度 {_as_percent(details.get('layout_similarity'))}；"
            f"强度/形态轮廓 {_as_percent(details.get('profile_similarity'))}；"
            f"归一化排列误差 {details.get('layout_error', '-')}；"
            f"两侧检出斑点 {details.get('first_spot_count', '-')} / "
            f"{details.get('second_spot_count', '-')}；"
            "已允许裁剪、缩放和对比度变化。"
        )
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
    if finding.rule_id.startswith("image.fluorescence."):
        return (
            f"荧光证据：关系 {_relationship_label(details.get('relationship_class'))}；"
            f"角色 {_image_role_label(details.get('first_inferred_role'))} / "
            f"{_image_role_label(details.get('second_inferred_role'))}；"
            f"匹配通道 {_image_role_label(details.get('first_channel'))} / "
            f"{_image_role_label(details.get('second_channel'))}；"
            f"结构 {_as_percent(details.get('structure_similarity'))}；"
            f"前景重叠 {_as_percent(details.get('foreground_mask_iou'))}；"
            f"互信息 {_as_percent(details.get('normalized_mutual_information'))}；"
            f"配准位移 ({details.get('alignment_shift_x', '-')}, "
            f"{details.get('alignment_shift_y', '-')})。"
        )
    if finding.rule_id.startswith("image.pathology."):
        return (
            f"病理证据：关系 {_relationship_label(details.get('relationship_class'))}；"
            f"组织结构 {_as_percent(details.get('structure_similarity'))}；"
            f"组织掩膜重叠 {_as_percent(details.get('tissue_mask_iou'))}；"
            f"倍率 {details.get('first_magnification') or '-'}× / "
            f"{details.get('second_magnification') or '-'}×；"
            f"估算尺度比 {details.get('estimated_scale_ratio', '-')}；"
            f"变换 {details.get('transform_second_to_first', '-')}。"
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


def _relationship_label(value: object) -> str:
    labels = {
        "normal_merge_component": "单通道与合并图正常关系",
        "normal_same_field_channels": "不同通道同视野正常关系",
        "suspected_same_channel_reuse": "同通道疑似复用",
        "normal_different_magnification": "不同倍率正常关系",
        "suspected_pathology_reuse": "组织区域疑似复用",
    }
    return labels.get(str(value), str(value or "-"))


def _image_role_label(value: object) -> str:
    labels = {
        "blue": "蓝色通道",
        "green": "绿色通道",
        "red": "红色通道",
        "far_red": "远红通道",
        "gray": "灰度通道",
        "merge": "合并图",
        "unknown": "未识别",
    }
    return labels.get(str(value), str(value or "-"))


def _column_label(column: int) -> str:
    label = ""
    value = max(1, column)
    while value:
        value, remainder = divmod(value - 1, 26)
        label = chr(65 + remainder) + label
    return label


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
        if "mismatch_count" in finding.details:
            lines.append(
                f"不同完整值位置：{finding.details.get('mismatch_count', '-')}；"
                f"超出容差：{finding.details.get('out_of_tolerance_count', '-')}；"
                f"相似度：{_as_percent(finding.details.get('similarity'))}。"
            )
        if "slope" in finding.details:
            lines.append(
                f"稳健线性参数：a={finding.details.get('slope', '-')}，"
                f"b={finding.details.get('intercept', '-')}；"
                f"离群位置：{finding.details.get('outlier_count', '-')}。"
            )
        if "row_count" in finding.details:
            lines.append(
                f"重复区域：{finding.details.get('row_count', '-')} 行 × "
                f"{finding.details.get('column_count', '-')} 列。"
            )
        if "distribution_correlation" in finding.details:
            lines.append(
                "统计分布：相关系数 "
                f"{finding.details.get('distribution_correlation', '-')}；"
                f"标准化平均误差 {finding.details.get('normalized_mae', '-')}。"
            )
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
