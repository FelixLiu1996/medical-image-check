from pathlib import Path

import cv2
import numpy as np

from medical_image_check.engines.image_similarity import ImageDuplicateDetector
from medical_image_check.engines.western_blot import WesternBlotDuplicateDetector


def _synthetic_blot(
    seed: int,
    band_centers: tuple[int, ...] = (56, 146, 241, 336),
) -> np.ndarray:
    random = np.random.default_rng(seed)
    height, width = 220, 420
    y_grid, x_grid = np.mgrid[:height, :width]
    background = (
        205
        + 8 * np.sin(x_grid / 57)
        + 5 * np.cos(y_grid / 31)
        + random.normal(0, 3.5, (height, width))
    )
    image = np.clip(background, 0, 255).astype(np.uint8)
    widths = (44, 36, 52, 40)
    heights = (12, 15, 10, 14)
    offsets = (0, 3, -4, 2)
    for index, center_x in enumerate(band_centers):
        overlay = image.astype(np.float32)
        cv2.ellipse(
            overlay,
            (center_x, 106 + offsets[index]),
            (widths[index] // 2, heights[index] // 2),
            0,
            0,
            360,
            55 + 7 * index,
            -1,
        )
        image = np.clip(cv2.GaussianBlur(overlay, (5, 5), 0.8), 0, 255).astype(np.uint8)
    return image


def _synthetic_single_band(seed: int) -> np.ndarray:
    random = np.random.default_rng(seed)
    image = np.clip(210 + random.normal(0, 4, (160, 240)), 0, 255).astype(np.uint8)
    overlay = image.astype(np.float32)
    cv2.ellipse(overlay, (120, 80), (28, 8), 0, 0, 360, 45, -1)
    return np.clip(cv2.GaussianBlur(overlay, (5, 5), 0.8), 0, 255).astype(np.uint8)


def test_detector_finds_flipped_exposure_changed_blot_panel(tmp_path: Path) -> None:
    original = _synthetic_blot(11)
    reused = np.clip(np.fliplr(original).astype(np.float32) * 0.82 + 28, 0, 255).astype(np.uint8)
    unrelated = _synthetic_blot(33, (70, 175, 280, 370))
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    third = tmp_path / "unrelated.png"
    assert cv2.imwrite(str(first), original)
    assert cv2.imwrite(str(second), reused)
    assert cv2.imwrite(str(third), unrelated)

    findings, issues = WesternBlotDuplicateDetector().scan([first, second, third])

    assert issues == []
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "image.western_blot.panel_reuse"
    assert finding.risk == "high"
    assert finding.details["transform_second_to_first"] == "flip_horizontal"
    assert finding.details["matched_band_count"] == 4
    assert finding.details["structure_similarity"] >= 0.94
    assert finding.details["background_similarity"] >= 0.82
    assert finding.details["first_region_width"] > 0


def test_detector_finds_copy_move_between_panels_in_one_image(tmp_path: Path) -> None:
    random = np.random.default_rng(77)
    image = np.clip(210 + random.normal(0, 4, (260, 420)), 0, 255).astype(np.uint8)
    for center_x, width in zip((70, 170, 275, 365), (40, 50, 36, 44), strict=True):
        cv2.ellipse(image, (center_x, 70), (width // 2, 7), 0, 0, 360, 50, -1)
    image[155:225] = image[35:105]
    path = tmp_path / "copy-move.png"
    assert cv2.imwrite(str(path), image)

    findings, issues = WesternBlotDuplicateDetector().scan([path])

    assert issues == []
    assert len(findings) == 1
    assert findings[0].rule_id == "image.western_blot.panel_reuse"
    assert findings[0].risk == "medium"
    assert findings[0].locations[0].source_path == findings[0].locations[1].source_path
    assert findings[0].details["matched_band_count"] == 4


def test_detector_matches_dark_and_light_blot_polarities(tmp_path: Path) -> None:
    original = _synthetic_blot(44)
    first = tmp_path / "dark-bands.png"
    second = tmp_path / "light-bands.png"
    assert cv2.imwrite(str(first), original)
    assert cv2.imwrite(str(second), 255 - original)

    findings, issues = WesternBlotDuplicateDetector().scan([first, second])

    assert issues == []
    assert len(findings) == 1
    assert {
        findings[0].details["first_polarity"],
        findings[0].details["second_polarity"],
    } == {"dark", "light"}
    assert findings[0].details["structure_similarity"] == 1.0


def test_single_band_detection_is_opt_in_and_stays_low_risk(tmp_path: Path) -> None:
    original = _synthetic_single_band(8)
    reused = np.clip(np.fliplr(original).astype(np.float32) * 0.85 + 25, 0, 255).astype(np.uint8)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    assert cv2.imwrite(str(first), original)
    assert cv2.imwrite(str(second), reused)

    default_findings, _ = WesternBlotDuplicateDetector().scan([first, second])
    sensitive_findings, issues = WesternBlotDuplicateDetector(True).scan([first, second])

    assert default_findings == []
    assert issues == []
    assert len(sensitive_findings) == 1
    assert sensitive_findings[0].rule_id == "image.western_blot.single_band"
    assert sensitive_findings[0].risk == "low"
    assert sensitive_findings[0].details["single_band_mode"] is True


def test_western_blot_detector_is_integrated_with_general_image_scan(tmp_path: Path) -> None:
    original = _synthetic_blot(91)
    reused = np.clip(original.astype(np.float32) * 0.78 + 35, 0, 255).astype(np.uint8)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    assert cv2.imwrite(str(first), original)
    assert cv2.imwrite(str(second), reused)

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert issues == []
    assert any(item.rule_id == "image.western_blot.panel_reuse" for item in findings)


def test_integrated_scan_does_not_repeat_exact_duplicate_as_western_result(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    assert cv2.imwrite(str(first), _synthetic_blot(19))
    second.write_bytes(first.read_bytes())

    findings, issues = ImageDuplicateDetector().scan([first, second])

    assert issues == []
    assert any(item.rule_id == "image.file.sha256" for item in findings)
    assert not any(item.rule_id.startswith("image.western_blot.") for item in findings)
