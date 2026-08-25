from pathlib import Path

import cv2
import numpy as np

from medical_image_check.domain.models import FindingType
from medical_image_check.engines.fluorescence import FluorescenceDuplicateDetector
from medical_image_check.engines.image_similarity import ImageDuplicateDetector
from medical_image_check.infrastructure.images import decode_image_pages


def _cell_field(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.zeros((240, 280), dtype=np.uint8)
    for _ in range(38):
        center = (int(rng.integers(12, 268)), int(rng.integers(12, 228)))
        radius = int(rng.integers(3, 9))
        cv2.circle(image, center, radius, int(rng.integers(145, 245)), -1)
    return cv2.GaussianBlur(image, (0, 0), 1.1)


def _write(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image)


def test_fluorescence_channel_matches_merge_component_as_normal_relation(
    tmp_path: Path,
) -> None:
    nuclei = _cell_field()
    marker = _cell_field(23)
    dapi = np.zeros((240, 280, 3), dtype=np.uint8)
    dapi[:, :, 0] = nuclei
    merge = np.zeros_like(dapi)
    merge[:, :, 0] = nuclei
    merge[:, :, 1] = marker
    first = tmp_path / "sample_01_DAPI.png"
    second = tmp_path / "sample_01_merge.png"
    _write(first, dapi)
    _write(second, merge)

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert not issues
    relation = next(item for item in findings if item.rule_id.endswith("merge_component"))
    assert relation.finding_type == FindingType.NORMAL_RELATION
    assert relation.details["first_channel"] == "blue"
    assert relation.details["relationship_class"] == "normal_merge_component"


def test_fluorescence_different_channels_can_identify_same_field(tmp_path: Path) -> None:
    field = _cell_field()
    dapi = np.zeros((240, 280, 3), dtype=np.uint8)
    fitc = np.zeros_like(dapi)
    dapi[:, :, 0] = field
    fitc[:, :, 1] = np.clip(field.astype(np.float32) * 0.82 + 8, 0, 255).astype(np.uint8)
    first = tmp_path / "group_a_DAPI.tif"
    second = tmp_path / "group_a_FITC.tif"
    _write(first, dapi)
    _write(second, fitc)

    detector = FluorescenceDuplicateDetector()
    pages = [
        *detector.extract_from_pages(first, decode_image_pages(first)),
        *detector.extract_from_pages(second, decode_image_pages(second)),
    ]
    findings = detector.findings(pages)

    assert len(findings) == 1
    assert findings[0].rule_id.endswith("same_field_channels")
    assert findings[0].finding_type == FindingType.NORMAL_RELATION


def test_fluorescence_same_channel_high_match_is_suspected_reuse(tmp_path: Path) -> None:
    field = _cell_field()
    first_image = np.zeros((240, 280, 3), dtype=np.uint8)
    second_image = np.zeros_like(first_image)
    first_image[:, :, 1] = field
    second_image[:, :, 1] = np.clip(field.astype(np.float32) * 0.9 + 5, 0, 255).astype(np.uint8)
    first = tmp_path / "experiment_a_FITC.png"
    second = tmp_path / "experiment_b_FITC.png"
    _write(first, first_image)
    _write(second, second_image)

    findings, _ = ImageDuplicateDetector().scan([first, second])

    reuse = next(item for item in findings if item.rule_id.endswith("same_channel_reuse"))
    assert reuse.finding_type == FindingType.SUSPECTED_REUSE
    assert reuse.details["structure_similarity"] >= 0.88


def test_integrated_scan_does_not_repeat_exact_duplicate_as_fluorescence(
    tmp_path: Path,
) -> None:
    field = _cell_field()
    image = np.zeros((240, 280, 3), dtype=np.uint8)
    image[:, :, 0] = field
    first = tmp_path / "same_a_DAPI.png"
    second = tmp_path / "same_b_DAPI.png"
    _write(first, image)
    second.write_bytes(first.read_bytes())

    findings, _ = ImageDuplicateDetector().scan([first, second])

    assert any(item.rule_id == "image.file.sha256" for item in findings)
    assert not any(item.rule_id.startswith("image.fluorescence.") for item in findings)
