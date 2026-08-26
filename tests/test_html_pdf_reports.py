from pathlib import Path

import cv2
import numpy as np

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    ScanResult,
)
from medical_image_check.domain.project import Project
from medical_image_check.services.html_report import HtmlReportExporter
from medical_image_check.services.pdf_report import PdfReportExporter


def _report_fixture(tmp_path: Path) -> tuple[ScanResult, Project, Path, Path]:
    first = tmp_path / "荧光图-A.png"
    second = tmp_path / "荧光图-B.png"
    image = np.zeros((120, 180, 3), dtype=np.uint8)
    cv2.circle(image, (70, 55), 24, (255, 80, 20), -1)
    encoded, first_png = cv2.imencode(".png", image)
    assert encoded
    first.write_bytes(first_png.tobytes())
    changed = image.copy()
    changed[:, :, 1] = np.maximum(changed[:, :, 1], image[:, :, 0] // 3)
    encoded, second_png = cv2.imencode(".png", changed)
    assert encoded
    second.write_bytes(second_png.tobytes())
    finding = Finding(
        finding_id="html-pdf-evidence",
        rule_id="image.fluorescence.merge_component",
        finding_type=FindingType.NORMAL_RELATION,
        risk=RiskLevel.LOW,
        title="荧光单通道与 Merge 成分对应",
        description="结构与前景证据一致，默认为正常实验关系。",
        locations=(EvidenceLocation(str(first)), EvidenceLocation(str(second))),
        confidence=0.91,
        details={
            "evidence_kind": "fluorescence",
            "relationship_class": "normal_merge_component",
            "first_region_x": 35,
            "first_region_y": 20,
            "first_region_width": 75,
            "first_region_height": 70,
            "second_region_x": 35,
            "second_region_y": 20,
            "second_region_width": 75,
            "second_region_height": 70,
            "structure_similarity": 0.95,
            "foreground_mask_iou": 0.84,
        },
    )
    result = ScanResult(
        source_count=2,
        image_count=2,
        spreadsheet_count=0,
        findings=(finding,),
        issues=(ScanIssue(str(first), "合成提示"),),
        algorithm_version="test-algorithm-1",
        completed_at="2026-08-25T00:00:00+00:00",
    )
    project = Project.create("三种报告测试").with_sources([first, second]).with_scan_result(result)
    return result, project, first, second


def test_html_report_is_single_file_with_search_and_embedded_evidence(tmp_path: Path) -> None:
    result, project, first, second = _report_fixture(tmp_path)
    first_digest = first.read_bytes()
    second_digest = second.read_bytes()

    output = HtmlReportExporter().export(result, tmp_path / "报告", project)
    content = output.read_text(encoding="utf-8")

    assert output.name == "报告.html"
    assert "三种报告测试" in content
    assert "荧光单通道与 Merge 成分对应" in content
    assert "data:image/png;base64," in content
    assert 'id="search"' in content
    assert 'id="attention"' in content
    assert "filterRows" in content
    assert "图片内容类型 自动识别（推荐）" in content
    assert "Excel 相对容差 0.0%" in content
    assert "连续风险阈值 3/4" in content
    assert "https://" not in content
    assert first.read_bytes() == first_digest
    assert second.read_bytes() == second_digest


def test_pdf_report_generates_local_archive_with_image_evidence(tmp_path: Path) -> None:
    result, project, first, second = _report_fixture(tmp_path)
    first_digest = first.read_bytes()
    second_digest = second.read_bytes()

    output = PdfReportExporter().export(result, tmp_path / "报告", project)
    encoded = output.read_bytes()

    assert output.name == "报告.pdf"
    assert encoded.startswith(b"%PDF-")
    assert len(encoded) > 5_000
    assert b"ReportLab" in encoded
    assert first.read_bytes() == first_digest
    assert second.read_bytes() == second_digest
