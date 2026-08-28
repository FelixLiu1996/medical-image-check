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
