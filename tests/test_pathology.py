from pathlib import Path

import cv2
import numpy as np

from medical_image_check.domain.models import FindingType
from medical_image_check.engines.image_similarity import ImageDuplicateDetector
from medical_image_check.engines.pathology import (
    PathologyDuplicateDetector,
    _looks_like_pathology,
    _stain_invariant_morphology,
)
from medical_image_check.infrastructure.images import decode_image_pages


def _histology_image(seed: int = 19) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.full((480, 480, 3), (242, 234, 246), dtype=np.uint8)
    for _ in range(95):
        center = (int(rng.integers(15, 465)), int(rng.integers(15, 465)))
        axes = (int(rng.integers(7, 24)), int(rng.integers(5, 17)))
        angle = float(rng.integers(0, 180))
        color = (
            int(rng.integers(145, 210)),
            int(rng.integers(75, 150)),
            int(rng.integers(165, 230)),
        )
        cv2.ellipse(image, center, axes, angle, 0, 360, color, -1)
        cv2.ellipse(image, center, axes, angle, 0, 360, (110, 60, 135), 1)
    noise = rng.normal(0, 2.0, image.shape).astype(np.int16)
    return np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _write(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image)


def test_pathology_multiscale_match_with_different_magnification_is_normal(
    tmp_path: Path,
) -> None:
    overview = _histology_image()
    detail = cv2.resize(overview[120:360, 120:360], (480, 480), interpolation=cv2.INTER_CUBIC)
    first = tmp_path / "case_10x_HE.png"
    second = tmp_path / "case_40x_HE.png"
    _write(first, overview)
    _write(second, detail)

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert not issues
    relation = next(
        item for item in findings if item.rule_id.endswith("same_region_different_magnification")
    )
    assert relation.finding_type == FindingType.NORMAL_RELATION
    assert relation.details["first_magnification"] == 10.0
    assert relation.details["second_magnification"] == 40.0
    assert relation.details["structure_similarity"] >= 0.86


def test_pathology_same_magnification_local_region_is_suspected_reuse(tmp_path: Path) -> None:
    overview = _histology_image()
    detail = cv2.resize(overview[120:360, 120:360], (420, 420), interpolation=cv2.INTER_LINEAR)
    first = tmp_path / "group_a_20x_HE.png"
    second = tmp_path / "group_b_20x_HE.png"
    _write(first, overview)
    _write(second, detail)

    detector = PathologyDuplicateDetector()
    regions = [
        *detector.extract_from_pages(first, decode_image_pages(first)),
        *detector.extract_from_pages(second, decode_image_pages(second)),
    ]
    findings = detector.findings(regions)

    assert len(findings) == 1
    assert findings[0].rule_id.endswith("local_reuse")
    assert findings[0].finding_type == FindingType.SUSPECTED_REUSE
    assert findings[0].details["tissue_mask_iou"] >= 0.5


def test_pathology_unrelated_tissue_does_not_match(tmp_path: Path) -> None:
    first = tmp_path / "case_a_20x_HE.png"
    second = tmp_path / "case_b_20x_HE.png"
    _write(first, _histology_image(11))
    _write(second, _histology_image(97))

    detector = PathologyDuplicateDetector()
    regions = [
        *detector.extract_from_pages(first, decode_image_pages(first)),
        *detector.extract_from_pages(second, decode_image_pages(second)),
    ]

    assert detector.findings(regions) == []


def test_integrated_scan_does_not_repeat_exact_duplicate_as_pathology(tmp_path: Path) -> None:
    first = tmp_path / "same_a_20x_HE.png"
    second = tmp_path / "same_b_20x_HE.png"
    _write(first, _histology_image())
    second.write_bytes(first.read_bytes())

    findings, _ = ImageDuplicateDetector().scan([first, second])

    assert any(item.rule_id == "image.file.sha256" for item in findings)
    assert not any(item.rule_id.startswith("image.pathology.") for item in findings)


def test_auto_pathology_gate_rejects_white_scientific_layout() -> None:
    image = np.full((320, 520, 3), 250, dtype=np.uint8)
    cv2.line(image, (45, 270), (475, 270), (35, 35, 35), 3)
    cv2.line(image, (45, 40), (45, 270), (35, 35, 35), 3)
    for index, color in enumerate(((150, 75, 180), (175, 95, 205), (130, 85, 170))):
        cv2.rectangle(image, (95 + index * 115, 100), (155 + index * 115, 268), color, -1)
    _, tissue_mask = _stain_invariant_morphology(image)

    assert _looks_like_pathology(image, tissue_mask) is False
