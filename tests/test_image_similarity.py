from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np

from medical_image_check.engines.image_similarity import ImageDuplicateDetector


def _synthetic_image() -> np.ndarray:
    random = np.random.default_rng(20260825)
    image = random.integers(20, 80, size=(128, 192, 3), dtype=np.uint8)
    cv2.rectangle(image, (18, 20), (82, 92), (220, 80, 30), -1)
    cv2.circle(image, (142, 70), 30, (40, 230, 180), -1)
    cv2.line(image, (5, 118), (185, 8), (245, 245, 245), 4)
    return image


def test_detector_finds_same_decoded_pixels_across_formats(tmp_path: Path) -> None:
    image = _synthetic_image()
    png = tmp_path / "source.png"
    bmp = tmp_path / "encoded-differently.bmp"
    assert cv2.imwrite(str(png), image)
    assert cv2.imwrite(str(bmp), image)

    findings, issues = ImageDuplicateDetector().scan([png, bmp])

    assert issues == []
    assert {finding.rule_id for finding in findings} == {"image.pixel.sha256"}
    assert png.read_bytes() != bmp.read_bytes()


def test_detector_finds_rotated_and_compressed_whole_image(tmp_path: Path) -> None:
    image = _synthetic_image()
    source = tmp_path / "source.png"
    transformed = tmp_path / "rotated.jpg"
    assert cv2.imwrite(str(source), image)
    assert cv2.imwrite(
        str(transformed),
        np.rot90(image, 1),
        [cv2.IMWRITE_JPEG_QUALITY, 94],
    )

    findings, issues = ImageDuplicateDetector().scan([source, transformed])

    assert issues == []
    perceptual = [item for item in findings if item.rule_id == "image.global.perceptual"]
    assert len(perceptual) == 1
    assert perceptual[0].details["transform_second_to_first"] in {
        "rotate_90",
        "rotate_270",
    }
    assert perceptual[0].details["normalized_similarity"] >= 0.92


def test_detector_scans_each_tiff_page_and_preserves_source(tmp_path: Path) -> None:
    image = _synthetic_image()
    tiff = tmp_path / "pages.tiff"
    original = tmp_path / "original.png"
    assert cv2.imwritemulti(str(tiff), [image, image])
    assert cv2.imwrite(str(original), image)
    before = sha256(tiff.read_bytes()).hexdigest()

    findings, issues = ImageDuplicateDetector().scan([tiff, original])

    assert issues == []
    pixel_findings = [item for item in findings if item.rule_id == "image.pixel.sha256"]
    assert len(pixel_findings) == 1
    assert any("第 2 页" in location.display_text for location in pixel_findings[0].locations)
    assert sha256(tiff.read_bytes()).hexdigest() == before


def test_detector_reports_corrupt_image_without_aborting(tmp_path: Path) -> None:
    corrupt = tmp_path / "broken.png"
    valid = tmp_path / "valid.png"
    corrupt.write_bytes(b"not-an-image")
    assert cv2.imwrite(str(valid), _synthetic_image())

    findings, issues = ImageDuplicateDetector().scan([corrupt, valid])

    assert findings == []
    assert len(issues) == 1
    assert issues[0].source_path == str(corrupt)
