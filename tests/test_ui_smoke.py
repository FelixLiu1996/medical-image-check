from pathlib import Path

from openpyxl import Workbook
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from medical_image_check.app import main
from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    ReviewStatus,
    RiskLevel,
    ScanResult,
)
from medical_image_check.domain.performance import (
    RuntimeEnvironment,
    ScanPerformance,
    StageTiming,
)
from medical_image_check.services.basic_scan import ScanMode
from medical_image_check.ui.main_window import MainWindow


def test_main_window_can_be_created_offscreen() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert "科研数据查重助手" in window.windowTitle()
    assert window._pages.currentWidget() is window._home_page
    assert window._pause_button.text() == "暂停"
    assert not window._pause_button.isEnabled()
    assert not window._cancel_button.isEnabled()

    window.close()
    app.processEvents()


def test_main_window_separates_image_and_data_workspaces(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    image = tmp_path / "source.png"
    image.write_bytes(b"image-placeholder")
    workbook = tmp_path / "values.xlsx"
    workbook.write_bytes(b"workbook-placeholder")

    window = MainWindow()
    window._set_scan_mode(ScanMode.IMAGE)
    window._append_sources([str(image), str(workbook)])

    assert window._sources.count() == 1
    assert window._sources.item(0).text() == str(image.resolve())
    assert not window._image_settings_group.isHidden()
    assert window._excel_settings_group.isHidden()

    window._set_scan_mode(ScanMode.DATA)
    window._append_sources([str(workbook), str(image)])

    assert window._sources.count() == 1
    assert window._sources.item(0).text() == str(workbook.resolve())
    assert window._image_settings_group.isHidden()
    assert not window._excel_settings_group.isHidden()
    assert window._evidence_images_container.isHidden()

    window._dirty = False
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
    window._digit_run_spin.setValue(6)
    window._western_single_band_check.setChecked(True)
    dot_blot_index = window._image_analysis_mode_combo.findData("dot_blot")
    window._image_analysis_mode_combo.setCurrentIndex(dot_blot_index)
    window._excel_relative_tolerance_spin.setValue(0.25)
    window._excel_absolute_tolerance_edit.setText("1e-9")
    window._excel_operation_targets_edit.setText("0, 1, 50, 100")
    window._excel_medium_run_spin.setValue(4)
    window._excel_high_run_spin.setValue(6)
    window._excel_settings_changed()
    window._show_result(ScanResult(1, 1, 0, (), ()))
    window.save_project()
    report = window.export_excel_report(tmp_path / "ui-report.xlsx")
    html_report = window.export_html_report(tmp_path / "ui-report.html")
    pdf_report = window.export_pdf_report(tmp_path / "ui-report.pdf")

    restored = MainWindow()
    restored.open_project(project_path)

    assert restored._project is not None
    assert restored._project.name == "界面项目"
    assert restored._sources.count() == 1
    assert restored._project.minimum_digit_run == 6
    assert restored._digit_run_spin.value() == 6
    assert restored._project.western_single_band_enabled is True
    assert restored._western_single_band_check.isChecked()
    assert restored._project.image_analysis_mode == "dot_blot"
    assert restored._image_analysis_mode_combo.currentData() == "dot_blot"
    assert restored._project.excel_custom_relative_tolerance_percent == 0.25
    assert restored._excel_absolute_tolerance_edit.text() == "0.000000001"
    assert restored._project.excel_operation_targets == ("0", "1", "50", "100")
    assert restored._excel_medium_run_spin.value() == 4
    assert restored._excel_high_run_spin.value() == 6
    assert restored._current_result == ScanResult(1, 1, 0, (), ())
    assert report.exists()
    assert html_report.exists()
    assert pdf_report.exists()
    assert str(html_report.resolve()) in restored._project.report_paths
    assert str(pdf_report.resolve()) in restored._project.report_paths

    window.close()
    restored.close()
    app.processEvents()


def test_application_packaging_smoke_mode_exits_without_event_loop() -> None:
    assert main(["--smoke-test"]) == 0


def test_main_window_marks_filters_persists_and_exports_feedback(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    project_path = tmp_path / "review-project.mic-project.json"
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(first))
    assert image.save(str(second))
    finding = Finding(
        finding_id="review-me",
        rule_id="image.local.geometric",
        finding_type=FindingType.SUSPECTED_REUSE,
        risk=RiskLevel.MEDIUM,
        title="图片存在局部重叠",
        description="用于轻量反馈测试。",
        locations=(EvidenceLocation(str(first)), EvidenceLocation(str(second))),
        confidence=0.9,
    )
    result = ScanResult(2, 2, 0, (finding,), algorithm_version="review-test-1")

    window = MainWindow()
    window.create_project("轻量复核", project_path)
    window._show_result(result)
    window._results.selectRow(0)
    window._show_selected_evidence(0)
    assert window._review_confirm_button.isEnabled()

    window._review_confirm_button.click()

    assert window._current_result is not None
    assert window._current_result.findings[0].review_status == ReviewStatus.CONFIRMED
    assert window._results.item(0, 2).text() == "准确"
    assert window._feedback_export_button.isEnabled()
    feedback = window.export_feedback(tmp_path / "feedback.json")
    assert feedback.exists()

    false_positive_filter = window._review_filter_combo.findData("false_positive")
    window._review_filter_combo.setCurrentIndex(false_positive_filter)
    assert window._results.rowCount() == 0
    confirmed_filter = window._review_filter_combo.findData("confirmed")
    window._review_filter_combo.setCurrentIndex(confirmed_filter)
    assert window._results.rowCount() == 1

    restored = MainWindow()
    restored.open_project(project_path)
    assert restored._current_result is not None
    assert restored._current_result.findings[0].review_status == ReviewStatus.CONFIRMED

    window.close()
    restored.close()
    app.processEvents()


def test_main_window_exports_performance_diagnostic(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    performance = ScanPerformance(
        schema_version=1,
        selected_backend="cpu",
        accelerator_status="no_nvidia_gpu_detected",
        wall_seconds=1.25,
        active_seconds=1.25,
        paused_seconds=0,
        stages=(StageTiming("image.generic_features", 1.0, 1, 2),),
        environment=RuntimeEnvironment(
            "Darwin",
            "test",
            "arm64",
            "test-cpu",
            8,
            "3.12.13",
            "4.14.0",
        ),
    )
    result = ScanResult(2, 2, 0, (), performance=performance)

    window = MainWindow()
    window.create_project("性能诊断")
    window._show_result(result)

    assert window._performance_export_button.isEnabled()
    assert "有效用时 1.25 秒" in window._status.text()
    output = window.export_performance_diagnostic(tmp_path / "diagnostic")
    assert output.name == "diagnostic.json"
    assert output.exists()

    window._dirty = False
    window.close()
    app.processEvents()


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
    assert window._crop_evidence_check.isEnabled()
    window._crop_evidence_check.setChecked(True)
    assert window._first_evidence._crop_to_region is True
    assert window._second_evidence._crop_to_region is True
    window._copy_evidence_button.click()
    assert "几何内点：20" in QApplication.clipboard().text()

    window.close()
    app.processEvents()


def test_main_window_displays_excel_digit_fragment_evidence(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    workbook = tmp_path / "values.xlsx"
    source_book = Workbook()
    source_book.active.title = "Sheet1"
    source_book.active.append([1123456, "对照"])
    source_book.active.append([9123457, "实验"])
    source_book.save(workbook)
    finding = Finding(
        finding_id="excel-fragment",
        rule_id="excel.digit_fragment",
        finding_type=FindingType.SUSPECTED_REUSE,
        risk=RiskLevel.MEDIUM,
        title="数值包含连续重复数字片段",
        description="两个完整值共享片段 12345。",
        locations=(
            EvidenceLocation(str(workbook), "Sheet1", "A1"),
            EvidenceLocation(str(workbook), "Sheet1", "A2"),
        ),
        confidence=0.7,
        details={
            "fragments": ["12345"],
            "maximum_length": 5,
            "cell_count": 2,
            "cells": [
                {
                    "source_path": str(workbook),
                    "sheet": "Sheet1",
                    "coordinate": "A1",
                    "canonical_value": "1123456",
                    "display_value": "1123456",
                },
                {
                    "source_path": str(workbook),
                    "sheet": "Sheet1",
                    "coordinate": "A2",
                    "canonical_value": "9123457",
                    "display_value": "9123457",
                },
            ],
        },
    )
    window = MainWindow()
    window._set_scan_mode(ScanMode.DATA)
    window._render_result(ScanResult(1, 0, 1, (finding,)))

    window._show_selected_evidence(0)

    summary = window._evidence_summary.text()
    assert "匹配数字片段：12345" in summary
    assert "完整值 1123456" in summary
    assert window._evidence_images_container.isHidden()
    assert not window._spreadsheet_evidence_container.isHidden()
    assert window._first_spreadsheet_evidence._table.item(0, 0).text() == "1123456"
    assert window._first_spreadsheet_evidence._table.item(0, 0).background().color() == QColor(
        "#fff1a8"
    )
    assert window._result_evidence_splitter.orientation() == Qt.Orientation.Vertical

    window.close()
    app.processEvents()


def test_main_window_displays_western_blot_evidence(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    image = QImage(160, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(first))
    assert image.save(str(second))
    finding = Finding(
        finding_id="western-evidence",
        rule_id="image.western_blot.panel_reuse",
        finding_type=FindingType.SUSPECTED_REUSE,
        risk=RiskLevel.MEDIUM,
        title="Western blot 面板或泳道疑似复用",
        description="多个证据共同匹配。",
        locations=(EvidenceLocation(str(first)), EvidenceLocation(str(second))),
        confidence=0.91,
        details={
            "first_region_x": 10,
            "first_region_y": 20,
            "first_region_width": 100,
            "first_region_height": 40,
            "second_region_x": 20,
            "second_region_y": 25,
            "second_region_width": 110,
            "second_region_height": 45,
            "matched_band_count": 4,
            "structure_similarity": 0.95,
            "geometry_similarity": 0.9,
            "background_similarity": 0.85,
            "band_mask_iou": 0.8,
            "transform_second_to_first": "flip_horizontal",
            "first_polarity": "dark",
            "second_polarity": "dark",
        },
    )
    window = MainWindow()
    window._render_result(ScanResult(2, 2, 0, (finding,)))

    window._show_selected_evidence(0)

    assert window._first_evidence._region == (10, 20, 100, 40)
    assert window._second_evidence._region == (20, 25, 110, 45)
    assert "匹配条带 4 条" in window._evidence_summary.text()
    assert "背景纹理 85.0%" in window._evidence_summary.text()

    window.close()
    app.processEvents()


def test_main_window_displays_fluorescence_and_pathology_evidence(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    image = QImage(180, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(first))
    assert image.save(str(second))
    locations = (EvidenceLocation(str(first)), EvidenceLocation(str(second)))
    fluorescence = Finding(
        finding_id="fluorescence-evidence",
        rule_id="image.fluorescence.merge_component",
        finding_type=FindingType.NORMAL_RELATION,
        risk=RiskLevel.LOW,
        title="荧光单通道与 Merge 成分对应",
        description="正常关系。",
        locations=locations,
        details={
            "first_region_x": 0,
            "first_region_y": 0,
            "first_region_width": 180,
            "first_region_height": 120,
            "second_region_x": 0,
            "second_region_y": 0,
            "second_region_width": 180,
            "second_region_height": 120,
            "relationship_class": "normal_merge_component",
            "first_inferred_role": "blue",
            "second_inferred_role": "merge",
            "first_channel": "blue",
            "second_channel": "blue",
            "structure_similarity": 0.96,
            "foreground_mask_iou": 0.82,
            "normalized_mutual_information": 0.75,
            "alignment_shift_x": 1.5,
            "alignment_shift_y": -2.0,
        },
    )
    pathology = Finding(
        finding_id="pathology-evidence",
        rule_id="image.pathology.same_region_different_magnification",
        finding_type=FindingType.NORMAL_RELATION,
        risk=RiskLevel.LOW,
        title="病理图疑似同一区域的不同倍率",
        description="正常关系。",
        locations=locations,
        details={
            "first_region_x": 10,
            "first_region_y": 20,
            "first_region_width": 80,
            "first_region_height": 60,
            "second_region_x": 0,
            "second_region_y": 0,
            "second_region_width": 180,
            "second_region_height": 120,
            "relationship_class": "normal_different_magnification",
            "structure_similarity": 0.94,
            "tissue_mask_iou": 0.8,
            "first_magnification": 10.0,
            "second_magnification": 40.0,
            "estimated_scale_ratio": 4.0,
            "transform_second_to_first": "identity",
        },
    )
    window = MainWindow()
    window._render_result(ScanResult(2, 2, 0, (fluorescence, pathology)))

    window._show_selected_evidence(0)
    assert "荧光证据" in window._evidence_summary.text()
    assert "前景重叠 82.0%" in window._evidence_summary.text()
    window._show_selected_evidence(1)
    assert window._first_evidence._region == (10, 20, 80, 60)
    assert "病理证据" in window._evidence_summary.text()
    assert "倍率 10.0× / 40.0×" in window._evidence_summary.text()

    window.close()
    app.processEvents()
