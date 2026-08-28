from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np
from numpy.typing import NDArray

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    ScanIssue,
    deterministic_finding_id,
)
from medical_image_check.domain.panels import PanelSelection
from medical_image_check.infrastructure.images import canonical_pixels, decode_image_pages

MAX_DETECTION_DIMENSION = 1600
MAX_PANELS_PER_PAGE = 96


@dataclass(frozen=True, slots=True)
class MaterializedPanel:
    temporary_path: str
    selection: PanelSelection


class PanelMaterialization:
    """Owns temporary lossless crops for one scan."""

    def __init__(self, images: Iterable[Path], selections: Iterable[PanelSelection]) -> None:
        self._images = tuple(images)
        self._selections = tuple(selection for selection in selections if selection.selected)
        self._temporary_directory: TemporaryDirectory[str] | None = None
        self.panels: tuple[MaterializedPanel, ...] = ()
        self.issues: tuple[ScanIssue, ...] = ()
        self._by_path: dict[str, PanelSelection] = {}

    def __enter__(self) -> PanelMaterialization:
        self._temporary_directory = TemporaryDirectory(prefix="medical-image-check-panels-")
        destination = Path(self._temporary_directory.name)
        allowed = {str(path.expanduser().resolve()) for path in self._images}
        materialized: list[MaterializedPanel] = []
        issues: list[ScanIssue] = []
        current_source: str | None = None
        current_pages: tuple[NDArray, ...] = ()
        ordered_selections = sorted(
            self._selections,
            key=lambda selection: (
                selection.normalized_source_path,
                selection.page,
                selection.panel_index,
            ),
        )
        for index, selection in enumerate(ordered_selections, start=1):
            source_path = selection.normalized_source_path
            if source_path not in allowed:
                continue
            if source_path != current_source:
                try:
                    current_pages = decode_image_pages(source_path)
                except (OSError, ValueError) as exc:
                    current_pages = ()
                    issues.append(ScanIssue(source_path, f"复合图拆分无法读取图片：{exc}", "error"))
                current_source = source_path
            if selection.page > len(current_pages):
                continue
            image = canonical_pixels(current_pages[selection.page - 1])
            height, width = image.shape[:2]
            x = min(selection.x, max(0, width - 1))
            y = min(selection.y, max(0, height - 1))
            crop_width = min(selection.width, width - x)
            crop_height = min(selection.height, height - y)
            if crop_width < 1 or crop_height < 1:
                continue
            crop = image[y : y + crop_height, x : x + crop_width]
            temporary_path = destination / f"panel-{index:06d}.png"
            success, encoded = cv2.imencode(".png", crop)
            if not success:
                continue
            temporary_path.write_bytes(encoded.tobytes())
            materialized.append(MaterializedPanel(str(temporary_path), selection))
        self.panels = tuple(materialized)
        self.issues = tuple(issues)
        self._by_path = {
            str(Path(panel.temporary_path).resolve()): panel.selection for panel in self.panels
        }
        return self

    def __exit__(self, *args: object) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(Path(panel.temporary_path) for panel in self.panels)

    def original_for(self, temporary_path: str | Path) -> PanelSelection | None:
        return self._by_path.get(str(Path(temporary_path).resolve()))


def detect_panel_selections(
    images: Iterable[str | Path],
    progress: Callable[[int, int, str], bool | None] | None = None,
) -> tuple[PanelSelection, ...]:
    paths = tuple(Path(path).expanduser().resolve() for path in images)
    selections: list[PanelSelection] = []
    for file_index, path in enumerate(paths, start=1):
        try:
            pages = decode_image_pages(path)
        except (OSError, ValueError):
            pages = ()
            selections.append(PanelSelection(str(path), 1, 1, 0, 0, 1, 1))
        for page_index, page in enumerate(pages, start=1):
            canonical = canonical_pixels(page)
            regions = detect_panel_regions(canonical)
            for panel_index, (x, y, width, height) in enumerate(regions, start=1):
                selections.append(
                    PanelSelection(
                        source_path=str(path),
                        page=page_index,
                        panel_index=panel_index,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                    )
                )
        if progress and progress(file_index, len(paths), path.name) is False:
            break
    return tuple(selections)


def detect_panel_regions(image: NDArray) -> tuple[tuple[int, int, int, int], ...]:
    """Conservatively find large visual islands separated by background gutters."""

    canonical = canonical_pixels(image)
    original_height, original_width = canonical.shape[:2]
    scale = min(1.0, MAX_DETECTION_DIMENSION / max(original_height, original_width))
    if scale < 1.0:
        processing = cv2.resize(
            canonical,
            (round(original_width * scale), round(original_height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    else:
        processing = canonical
    height, width = processing.shape[:2]
    border = np.concatenate(
        (
            processing[0, :, :3],
            processing[-1, :, :3],
            processing[:, 0, :3],
            processing[:, -1, :3],
        )
    )
    background = np.median(border.astype(np.float32), axis=0)
    difference = np.max(np.abs(processing[:, :, :3].astype(np.float32) - background), axis=2)
    mask = (difference >= 16).astype(np.uint8) * 255
    close_x = max(3, round(width * 0.006)) | 1
    close_y = max(3, round(height * 0.006)) | 1
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (close_x, close_y)),
    )
    dilate_x = max(3, round(width * 0.004))
    dilate_y = max(3, round(height * 0.004))
    mask = cv2.dilate(
        mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_x, dilate_y)),
        iterations=1,
    )
    contour_result = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = contour_result[-2]
    minimum_width = max(24, round(width * 0.08))
    minimum_height = max(20, round(height * 0.055))
    minimum_area = max(400, round(width * height * 0.003))
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width < minimum_width or box_height < minimum_height:
            continue
        if box_width * box_height < minimum_area:
            continue
        padding_x = max(2, round(width * 0.006))
        padding_y = max(2, round(height * 0.006))
        left = max(0, x - padding_x)
        top = max(0, y - padding_y)
        right = min(width, x + box_width + padding_x)
        bottom = min(height, y + box_height + padding_y)
        boxes.append((left, top, right - left, bottom - top))
    boxes = _remove_contained_boxes(boxes)
    page_area = width * height
    useful = [box for box in boxes if box[2] * box[3] < page_area * 0.92]
    if not 2 <= len(useful) <= MAX_PANELS_PER_PAGE:
        return ((0, 0, original_width, original_height),)
    restored = tuple(
        sorted(
            (
                max(0, round(x / scale)),
                max(0, round(y / scale)),
                min(original_width - round(x / scale), max(1, round(box_width / scale))),
                min(original_height - round(y / scale), max(1, round(box_height / scale))),
            )
            for x, y, box_width, box_height in useful
        )
    )
    return tuple(sorted(restored, key=lambda box: (box[1], box[0])))


def remap_panel_findings(
    findings: Iterable[Finding], materialization: PanelMaterialization
) -> list[Finding]:
    return [_remap_finding(finding, materialization) for finding in findings]


def remap_panel_issues(
    issues: Iterable[ScanIssue], materialization: PanelMaterialization
) -> list[ScanIssue]:
    remapped: list[ScanIssue] = []
    for issue in issues:
        selection = materialization.original_for(issue.source_path)
        if selection is None:
            remapped.append(issue)
            continue
        remapped.append(
            ScanIssue(
                selection.normalized_source_path,
                f"第 {selection.page} 页子面板 {selection.panel_index}：{issue.message}",
                issue.severity,
            )
        )
    return remapped


def _remap_finding(finding: Finding, materialization: PanelMaterialization) -> Finding:
    selections = [
        materialization.original_for(location.source_path) for location in finding.locations
    ]
    locations: list[EvidenceLocation] = []
    for location, selection in zip(finding.locations, selections, strict=True):
        if selection is None:
            locations.append(location)
            continue
        panel_text = f"第 {selection.page} 页；子面板 {selection.panel_index}"
        coordinate = f"{panel_text}；{location.coordinate}" if location.coordinate else panel_text
        locations.append(
            EvidenceLocation(
                selection.normalized_source_path,
                sheet=location.sheet,
                coordinate=coordinate,
                hidden_sheet=location.hidden_sheet,
            )
        )
    details = dict(finding.details)
    for index, prefix in enumerate(("first", "second")):
        if index >= len(selections) or selections[index] is None:
            continue
        selection = selections[index]
        assert selection is not None
        details[f"{prefix}_page"] = selection.page
        x_key = f"{prefix}_region_x"
        y_key = f"{prefix}_region_y"
        width_key = f"{prefix}_region_width"
        height_key = f"{prefix}_region_height"
        if all(
            isinstance(details.get(key), int | float)
            for key in (x_key, y_key, width_key, height_key)
        ):
            details[x_key] = int(details[x_key]) + selection.x
            details[y_key] = int(details[y_key]) + selection.y
        else:
            details[x_key] = selection.x
            details[y_key] = selection.y
            details[width_key] = selection.width
            details[height_key] = selection.height
        details[f"{prefix}_panel_index"] = selection.panel_index
    location_tuple = tuple(locations)
    rule_id = finding.rule_id
    title = finding.title
    description = finding.description
    if finding.rule_id == "image.file.sha256":
        rule_id = "image.panel.pixel_exact"
        title = "子面板像素完全一致"
        description = f"{len(location_tuple)} 个已选子面板具有完全一致的无损像素内容。"
    return replace(
        finding,
        finding_id=deterministic_finding_id(rule_id, location_tuple),
        rule_id=rule_id,
        title=title,
        description=description,
        locations=location_tuple,
        details=details,
    )


def _remove_contained_boxes(
    boxes: Iterable[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    ordered = sorted(boxes, key=lambda box: box[2] * box[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for box in ordered:
        x, y, width, height = box
        area = width * height
        contained = False
        for other_x, other_y, other_width, other_height in kept:
            intersection_width = max(0, min(x + width, other_x + other_width) - max(x, other_x))
            intersection_height = max(0, min(y + height, other_y + other_height) - max(y, other_y))
            if intersection_width * intersection_height >= area * 0.9:
                contained = True
                break
        if not contained:
            kept.append(box)
    return kept
