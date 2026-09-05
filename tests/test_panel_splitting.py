from pathlib import Path

import cv2
import numpy as np

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
)
from medical_image_check.domain.panels import PanelSelection
from medical_image_check.services.panel_splitting import (
    PanelMaterialization,
    detect_panel_regions,
    detect_panel_selections,
    prioritize_panel_findings,
    remap_panel_findings,
)


def test_detect_panel_regions_finds_visual_islands_and_keeps_single_image_whole() -> None:
    composite = np.full((600, 800, 3), 255, dtype=np.uint8)
    for x, y in ((50, 50), (440, 50), (50, 340), (440, 340)):
        cv2.rectangle(composite, (x, y), (x + 300, y + 200), (70, 120, 180), -1)
        cv2.circle(composite, (x + 150, y + 100), 50, (0, 0, 0), -1)

    regions = detect_panel_regions(composite)

    assert len(regions) == 4
    assert regions == tuple(sorted(regions, key=lambda box: (box[1], box[0])))

    single = np.full((300, 500, 3), 255, dtype=np.uint8)
    cv2.circle(single, (250, 150), 80, (0, 0, 0), -1)
    assert detect_panel_regions(single) == ((0, 0, 500, 300),)


def test_detect_panel_regions_splits_repeated_blot_strips_inside_columns() -> None:
    composite = np.full((420, 480, 3), 255, dtype=np.uint8)
    for left in (35, 190, 345):
        cv2.line(composite, (left, 35), (left, 374), (80, 80, 80), 2)
        cv2.line(composite, (left + 100, 35), (left + 100, 374), (80, 80, 80), 2)
        for row in range(8):
            top = 42 + row * 41
            cv2.rectangle(composite, (left + 5, top), (left + 95, top + 25), (45, 45, 45), -1)

    regions = detect_panel_regions(composite)

    assert len(regions) == 24
    assert all(width >= 90 and height <= 36 for _, _, width, height in regions)


def test_detect_panel_regions_splits_repeated_microscopy_tiles_inside_row() -> None:
    composite = np.full((360, 760, 3), 255, dtype=np.uint8)
    cv2.line(composite, (35, 80), (724, 80), (90, 90, 90), 2)
    cv2.line(composite, (35, 200), (724, 200), (90, 90, 90), 2)
    for column in range(6):
        left = 42 + column * 114
        cv2.rectangle(composite, (left, 85), (left + 96, 195), (120, 120, 120), -1)
        cv2.circle(composite, (left + 48, 140), 22, (10, 10, 10), -1)
    cv2.rectangle(composite, (80, 265), (300, 325), (80, 80, 80), -1)
    cv2.rectangle(composite, (460, 265), (680, 325), (80, 80, 80), -1)

    regions = detect_panel_regions(composite)

    top_regions = [box for box in regions if box[1] < 230]
    assert len(top_regions) == 6
    assert all(width <= 112 and height >= 110 for _, _, width, height in top_regions)


def test_detect_panel_regions_splits_regular_two_dimensional_visual_grid() -> None:
    composite = np.full((430, 700, 3), 255, dtype=np.uint8)
    cv2.rectangle(composite, (38, 38), (530, 335), (80, 80, 80), 2)
    colors = ((80, 105, 185), (165, 85, 110), (75, 150, 95))
    for row in range(2):
        for column in range(3):
            left = 50 + column * 158
            top = 50 + row * 138
            cv2.rectangle(
                composite,
                (left, top),
                (left + 145, top + 125),
                colors[(row + column) % len(colors)],
                -1,
            )
            cv2.circle(composite, (left + 72, top + 62), 22, (20, 20, 20), -1)
    cv2.rectangle(composite, (570, 80), (670, 320), (90, 90, 90), -1)

    regions = detect_panel_regions(composite)

    grid_regions = [box for box in regions if box[0] < 550]
    assert len(grid_regions) == 6
    assert all(width < 155 and height < 135 for _, _, width, height in grid_regions)


def test_detect_panel_regions_recovers_detached_small_blot_rows_as_stack() -> None:
    composite = np.full((420, 680, 3), 255, dtype=np.uint8)
    for left in (55, 260):
        for row in range(5):
            top = 45 + row * 34
            cv2.rectangle(composite, (left, top), (left + 150, top + 13), (85, 85, 85), -1)
            cv2.ellipse(
                composite,
                (left + 75, top + 7),
                (22, 5),
                0,
                0,
                360,
                (20, 20, 20),
                -1,
            )
    cv2.rectangle(composite, (485, 60), (640, 350), (90, 110, 130), -1)

    regions = detect_panel_regions(composite)

    blot_regions = [box for box in regions if box[0] < 450]
    assert len(blot_regions) == 10
    assert all(width >= 150 and height <= 25 for _, _, width, height in blot_regions)


def test_detect_panel_regions_recovers_raw_rows_merged_by_morphology() -> None:
    composite = np.full((420, 680, 3), 255, dtype=np.uint8)
    for row in range(5):
        top = 45 + row * 16
        cv2.rectangle(composite, (55, top), (205, top + 12), (90, 90, 90), -1)
        cv2.ellipse(
            composite,
            (130, top + 6),
            (25, 4),
            0,
            0,
            360,
            (15, 15, 15),
            -1,
        )
    cv2.rectangle(composite, (430, 60), (640, 350), (90, 110, 130), -1)

    regions = detect_panel_regions(composite)

    blot_regions = [box for box in regions if box[0] < 300]
    assert len(blot_regions) == 5
    assert all(width >= 150 and height <= 22 for _, _, width, height in blot_regions)


def test_detect_panel_regions_splits_contiguous_periodic_blot_stack() -> None:
    composite = np.full((460, 700, 3), 255, dtype=np.uint8)
    left = 85
    top = 55
    for row in range(6):
        row_top = top + row * 42
        shade = 185 if row % 2 == 0 else 155
        cv2.rectangle(
            composite,
            (left, row_top),
            (left + 155, row_top + 41),
            (shade, shade, shade),
            -1,
        )
        cv2.ellipse(
            composite,
            (left + 78, row_top + 21),
            (28, 8),
            0,
            0,
            360,
            (25, 25, 25),
            -1,
        )
    cv2.rectangle(composite, (430, 70), (650, 390), (90, 120, 150), -1)

    regions = detect_panel_regions(composite)

    blot_regions = [box for box in regions if box[0] < 300]
    assert len(blot_regions) == 6
    assert all(width >= 150 and 35 <= height <= 48 for _, _, width, height in blot_regions)


def test_detect_panel_regions_does_not_recover_irregular_thin_text_like_rows() -> None:
    composite = np.full((420, 680, 3), 255, dtype=np.uint8)
    for left, top, width in (
        (45, 45, 130),
        (70, 81, 190),
        (42, 137, 85),
        (95, 175, 155),
        (50, 260, 210),
    ):
        cv2.rectangle(composite, (left, top), (left + width, top + 10), (60, 60, 60), -1)
    cv2.rectangle(composite, (390, 60), (640, 350), (90, 110, 130), -1)

    regions = detect_panel_regions(composite)

    assert len(regions) == 1
    assert regions[0] == (0, 0, 680, 420)


def test_detect_panel_regions_keeps_colored_heatmap_rows_together() -> None:
    composite = np.full((420, 620, 3), 255, dtype=np.uint8)
    colors = ((15, 15, 230), (230, 30, 30), (170, 30, 210), (25, 180, 240))
    for column, left in enumerate((45, 180, 315, 450)):
        cv2.line(composite, (left, 35), (left, 374), (80, 80, 80), 2)
        cv2.line(composite, (left + 105, 35), (left + 105, 374), (80, 80, 80), 2)
        for row in range(8):
            top = 42 + row * 41
            cv2.rectangle(
                composite,
                (left + 5, top),
                (left + 100, top + 25),
                colors[(column + row) % len(colors)],
                -1,
            )

    regions = detect_panel_regions(composite)

    assert len(regions) == 4
    assert all(height > 300 for _, _, _, height in regions)


def test_detect_panel_regions_caps_dense_strip_expansion() -> None:
    composite = np.full((420, 1260, 3), 255, dtype=np.uint8)
    for left in (35, 190, 345, 500, 655, 810, 965, 1120):
        cv2.line(composite, (left, 35), (left, 374), (80, 80, 80), 2)
        cv2.line(composite, (left + 100, 35), (left + 100, 374), (80, 80, 80), 2)
        for row in range(8):
            top = 42 + row * 41
            cv2.rectangle(composite, (left + 5, top), (left + 95, top + 25), (45, 45, 45), -1)

    regions = detect_panel_regions(composite)

    assert len(regions) == 8
    assert all(height > 300 for _, _, _, height in regions)


def test_detect_panel_regions_does_not_split_irregular_bar_chart() -> None:
    composite = np.full((360, 760, 3), 255, dtype=np.uint8)
    cv2.rectangle(composite, (40, 55), (320, 305), (80, 120, 160), -1)
    cv2.line(composite, (430, 300), (720, 300), (30, 30, 30), 4)
    for left, bar_height in zip((450, 500, 550, 600, 650), (45, 110, 185, 70, 225), strict=True):
        cv2.rectangle(
            composite,
            (left, 300 - bar_height),
            (left + 25, 300),
            (40, 40, 40),
            -1,
        )

    regions = detect_panel_regions(composite)

    assert len(regions) == 2


def test_detect_panel_selections_uses_stable_page_and_panel_number(tmp_path: Path) -> None:
    image = np.full((300, 600, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 30), (270, 270), (0, 0, 0), -1)
    cv2.rectangle(image, (330, 30), (580, 270), (60, 60, 60), -1)
    source = tmp_path / "composite.png"
    assert cv2.imwrite(str(source), image)

    selections = detect_panel_selections([source])

    assert [selection.panel_index for selection in selections] == [1, 2]
    assert all(selection.page == 1 and selection.selected for selection in selections)
    assert len({selection.stable_key for selection in selections}) == 2


def test_detect_panel_selections_preserves_multi_page_tiff_page_numbers(tmp_path: Path) -> None:
    first = np.full((100, 160), 255, dtype=np.uint8)
    second = np.full((100, 160), 220, dtype=np.uint8)
    cv2.circle(first, (80, 50), 25, 0, -1)
    cv2.rectangle(second, (50, 20), (110, 80), 0, -1)
    source = tmp_path / "pages.tiff"
    assert cv2.imwritemulti(str(source), [first, second])

    selections = detect_panel_selections([source])

    assert [(selection.page, selection.panel_index) for selection in selections] == [(1, 1), (2, 1)]


def test_materialized_panel_findings_map_back_to_original_coordinates(tmp_path: Path) -> None:
    image = np.full((160, 240, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (50, 40), (149, 119), (0, 0, 0), -1)
    source = tmp_path / "original.png"
    assert cv2.imwrite(str(source), image)
    selection = PanelSelection(str(source), 1, 2, 50, 40, 100, 80)

    with PanelMaterialization((source,), (selection,)) as materialization:
        temporary = materialization.paths[0]
        assert materialization.candidate_source_groups[str(temporary.resolve())] == str(
            source.resolve()
        )
        finding = Finding(
            "temporary",
            "image.local.overlap",
            FindingType.SUSPECTED_REUSE,
            RiskLevel.MEDIUM,
            "局部重叠",
            "测试",
            (EvidenceLocation(str(temporary)), EvidenceLocation(str(temporary))),
            details={
                "first_region_x": 5,
                "first_region_y": 6,
                "first_region_width": 30,
                "first_region_height": 20,
                "second_region_x": 7,
                "second_region_y": 8,
                "second_region_width": 30,
                "second_region_height": 20,
            },
        )

        remapped = remap_panel_findings((finding,), materialization)[0]

        assert temporary.exists()
        assert all(location.source_path == str(source.resolve()) for location in remapped.locations)
        assert all("子面板 2" in (location.coordinate or "") for location in remapped.locations)
        assert remapped.details["first_region_x"] == 55
        assert remapped.details["first_region_y"] == 46
        assert remapped.details["second_region_x"] == 57
        assert remapped.details["second_region_y"] == 48
    assert not temporary.exists()


def test_prioritize_panel_findings_limits_each_source_pair_and_rule() -> None:
    locations = (EvidenceLocation("first.png"), EvidenceLocation("second.png"))
    dense_group = [
        Finding(
            f"candidate-{index}",
            "image.fluorescence.same_channel_reuse",
            FindingType.SUSPECTED_REUSE,
            RiskLevel.LOW,
            "候选",
            "测试",
            locations,
            confidence=0.80 + index * 0.01,
        )
        for index in range(5)
    ]
    normal_relation = Finding(
        "normal",
        "image.fluorescence.same_field_channels",
        FindingType.NORMAL_RELATION,
        RiskLevel.LOW,
        "正常关系",
        "测试",
        locations,
    )
    independent_pair = Finding(
        "independent",
        "image.fluorescence.same_channel_reuse",
        FindingType.SUSPECTED_REUSE,
        RiskLevel.LOW,
        "候选",
        "测试",
        (EvidenceLocation("first.png"), EvidenceLocation("third.png")),
        confidence=0.5,
    )

    prioritized, suppressed = prioritize_panel_findings(
        [*dense_group, normal_relation, independent_pair]
    )

    assert suppressed == 2
    assert {item.finding_id for item in prioritized} == {
        "candidate-2",
        "candidate-3",
        "candidate-4",
        "normal",
        "independent",
    }
    ranked = [item for item in prioritized if item.finding_id.startswith("candidate-")]
    assert {item.details["panel_candidate_group_rank"] for item in ranked} == {1, 2, 3}
    assert all(item.details["panel_candidate_group_size"] == 5 for item in ranked)
    assert all(item.details["panel_candidate_group_suppressed"] == 2 for item in ranked)
