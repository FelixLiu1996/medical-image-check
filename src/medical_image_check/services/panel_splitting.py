from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np
from numpy.typing import NDArray

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    ScanIssue,
    deterministic_finding_id,
)
from medical_image_check.domain.panels import PanelSelection
from medical_image_check.infrastructure.images import canonical_pixels, decode_image_pages

MAX_DETECTION_DIMENSION = 1600
MAX_PANELS_PER_PAGE = 96
MAX_GRID_EXPANSION_PER_PAGE = 72
MAX_REPEATED_STRIP_EXPANSION_PER_PAGE = 48
MAX_PANEL_FINDINGS_PER_SOURCE_PAIR_RULE = 3


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

    @property
    def candidate_source_groups(self) -> dict[str, str]:
        """Map temporary Panel files to original Figures for panel-aware selection."""

        return {
            str(Path(panel.temporary_path).resolve()): panel.selection.normalized_source_path
            for panel in self.panels
        }

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
    raw_mask = (difference >= 16).astype(np.uint8) * 255
    raw_contour_result = cv2.findContours(
        raw_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    raw_contours = raw_contour_result[-2]
    mask = raw_mask.copy()
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
    useful = _split_regular_grid_boxes(processing, raw_mask, useful)
    useful = _split_repeated_strip_boxes(processing, raw_mask, useful)
    useful = _split_periodic_stack_boxes(processing, raw_mask, useful)
    recovered = _recover_repeated_small_strips(
        processing,
        raw_mask,
        raw_contours,
        useful,
    )
    if (
        len(recovered) <= MAX_REPEATED_STRIP_EXPANSION_PER_PAGE
        and len(useful) + len(recovered) <= MAX_PANELS_PER_PAGE
    ):
        useful.extend(recovered)
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


def _split_regular_grid_boxes(
    image: NDArray,
    raw_mask: NDArray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Split image-dense visual grids along strong background gutters."""

    proposals = [_split_regular_grid_box(image, raw_mask, box) for box in boxes]
    expansion = sum(len(children) - 1 for children in proposals if children)
    if expansion > MAX_GRID_EXPANSION_PER_PAGE:
        return boxes

    refined: list[tuple[int, int, int, int]] = []
    for index, (box, children) in enumerate(zip(boxes, proposals, strict=True)):
        remaining_parent_count = len(boxes) - index - 1
        if (
            children
            and len(refined) + len(children) + remaining_parent_count <= MAX_PANELS_PER_PAGE
        ):
            refined.extend(children)
        else:
            refined.append(box)
    return refined


def _split_regular_grid_box(
    image: NDArray,
    raw_mask: NDArray,
    box: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    x, y, box_width, box_height = box
    if min(box_width, box_height) < 24:
        return []
    crop = raw_mask[y : y + box_height, x : x + box_width] > 0
    column_runs = _regular_projection_runs(crop.mean(axis=0))
    row_runs = _regular_projection_runs(crop.mean(axis=1))
    if not column_runs or not row_runs:
        return []

    cell_count = len(column_runs) * len(row_runs)
    if not 4 <= cell_count <= 30:
        return []
    if (len(column_runs) == 1 or len(row_runs) == 1) and max(len(column_runs), len(row_runs)) < 4:
        return []
    if sum(end - start for start, end in column_runs) < box_width * 0.55:
        return []
    if sum(end - start for start, end in row_runs) < box_height * 0.55:
        return []

    children: list[tuple[int, int, int, int]] = []
    densities: list[float] = []
    colorfulness: list[float] = []
    padding = 1
    for row_start, row_end in row_runs:
        for column_start, column_end in column_runs:
            cell_mask = crop[row_start:row_end, column_start:column_end]
            densities.append(float(cell_mask.mean()))
            cell = image[
                y + row_start : y + row_end,
                x + column_start : x + column_end,
                :3,
            ].astype(np.float32)
            colorfulness.append(float(np.mean(np.max(cell, axis=2) - np.min(cell, axis=2))))
            left = max(0, x + column_start - padding)
            top = max(0, y + row_start - padding)
            right = min(raw_mask.shape[1], x + column_end + padding)
            bottom = min(raw_mask.shape[0], y + row_end + padding)
            children.append((left, top, right - left, bottom - top))

    if float(np.mean(np.asarray(densities) >= 0.18)) < 0.9:
        return []
    child_aspects = np.asarray(
        [
            max(child_width, child_height) / max(min(child_width, child_height), 1)
            for _, _, child_width, child_height in children
        ]
    )
    if len(column_runs) == 1 or len(row_runs) == 1:
        if float(np.median(child_aspects)) > 2.2:
            return []
    elif float(np.median(child_aspects)) > 2.4 and float(np.median(colorfulness)) > 18.0:
        return []
    return children


def _regular_projection_runs(projection: NDArray) -> list[tuple[int, int]]:
    if projection.size == 0:
        return []
    threshold = max(0.08, min(0.4, float(np.quantile(projection, 0.75)) * 0.3))
    runs = _true_runs(projection > threshold)
    if not runs:
        return []
    run_lengths = np.asarray([end - start for start, end in runs], dtype=np.float32)
    anchor = float(np.quantile(run_lengths, 0.75))
    minimum_length = max(6.0, anchor * 0.55)
    runs = [(start, end) for start, end in runs if end - start >= minimum_length]
    if not runs:
        return []
    median_length = float(np.median([end - start for start, end in runs]))
    regular = [
        (start, end)
        for start, end in runs
        if median_length * 0.65 <= end - start <= median_length * 1.5
    ]
    return regular if len(regular) <= 12 else []


def _recover_repeated_small_strips(
    image: NDArray,
    raw_mask: NDArray,
    contours: Iterable[NDArray],
    existing_boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Recover small, detached blot rows only when they form a regular local stack."""

    height, width = raw_mask.shape[:2]
    padding_x = max(2, round(width * 0.006))
    padding_y = max(2, round(height * 0.006))
    candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width < max(24, round(width * 0.055)):
            continue
        if box_height < max(8, round(height * 0.012)) or box_height > height * 0.075:
            continue
        if box_width / max(box_height, 1) < 3.0:
            continue
        component_mask = raw_mask[y : y + box_height, x : x + box_width] > 0
        if float(component_mask.mean()) < 0.55:
            continue
        component = image[y : y + box_height, x : x + box_width, :3].astype(np.float32)
        colorfulness = float(np.median(np.max(component, axis=2) - np.min(component, axis=2)))
        if colorfulness > 18.0:
            continue
        left = max(0, x - padding_x)
        top = max(0, y - padding_y)
        right = min(width, x + box_width + padding_x)
        bottom = min(height, y + box_height + padding_y)
        candidate = (left, top, right - left, bottom - top)
        if max((_box_iou(candidate, box) for box in existing_boxes), default=0.0) >= 0.45:
            continue
        candidates.append(candidate)

    adjacency = [set() for _ in candidates]
    for index, first in enumerate(candidates):
        first_x, first_y, first_width, first_height = first
        for other_index in range(index + 1, len(candidates)):
            other_x, other_y, other_width, other_height = candidates[other_index]
            horizontal_overlap = max(
                0,
                min(first_x + first_width, other_x + other_width) - max(first_x, other_x),
            )
            if horizontal_overlap / min(first_width, other_width) < 0.72:
                continue
            if min(first_width, other_width) / max(first_width, other_width) < 0.62:
                continue
            first_center = first_y + first_height / 2
            other_center = other_y + other_height / 2
            if abs(first_center - other_center) > 4.5 * max(first_height, other_height):
                continue
            adjacency[index].add(other_index)
            adjacency[other_index].add(index)

    recovered: list[tuple[int, int, int, int]] = []
    visited: set[int] = set()
    for index in range(len(candidates)):
        if index in visited:
            continue
        pending = [index]
        component: list[int] = []
        visited.add(index)
        while pending:
            current = pending.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    pending.append(neighbour)
        if len(component) < 3:
            continue
        vertical_centres = sorted(
            candidates[item][1] + candidates[item][3] / 2 for item in component
        )
        gaps = np.diff(vertical_centres)
        median_gap = float(np.median(gaps))
        if median_gap <= 0:
            continue
        regular_gaps = (gaps >= median_gap * 0.35) & (gaps <= median_gap * 2.2)
        if float(np.mean(regular_gaps)) < 0.7:
            continue
        recovered.extend(candidates[item] for item in component)
    return recovered


def _box_iou(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    intersection_width = max(
        0,
        min(first_x + first_width, second_x + second_width) - max(first_x, second_x),
    )
    intersection_height = max(
        0,
        min(first_y + first_height, second_y + second_height) - max(first_y, second_y),
    )
    intersection = intersection_width * intersection_height
    if intersection == 0:
        return 0.0
    union = first_width * first_height + second_width * second_height - intersection
    return intersection / union


def _split_periodic_stack_boxes(
    image: NDArray,
    raw_mask: NDArray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Split tall grayscale stacks whose adjacent blot rows have no blank gutter."""

    proposals = [_split_periodic_stack_box(image, raw_mask, box) for box in boxes]
    refined: list[tuple[int, int, int, int]] = []
    for index, (box, children) in enumerate(zip(boxes, proposals, strict=True)):
        remaining_parent_count = len(boxes) - index - 1
        if (
            children
            and len(refined) + len(children) + remaining_parent_count <= MAX_PANELS_PER_PAGE
        ):
            refined.extend(children)
        else:
            refined.append(box)
    return refined


def _split_periodic_stack_box(
    image: NDArray,
    raw_mask: NDArray,
    box: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    x, y, box_width, box_height = box
    crop = raw_mask[y : y + box_height, x : x + box_width] > 0
    column_runs = _regular_projection_runs(crop.mean(axis=0))
    if not 1 <= len(column_runs) <= 3:
        return []
    content_widths = [end - start for start, end in column_runs]
    if sum(content_widths) < box_width * 0.55:
        return []
    if box_height < float(np.median(content_widths)) * 1.4:
        return []

    content_mask = np.concatenate(
        [crop[:, start:end] for start, end in column_runs],
        axis=1,
    )
    active_runs = _true_runs(content_mask.mean(axis=1) > 0.3)
    if not active_runs:
        return []
    content_start, content_end = max(active_runs, key=lambda run: run[1] - run[0])
    if content_end - content_start < box_height * 0.45:
        return []

    content_pixels = np.concatenate(
        [image[y : y + box_height, x + start : x + end, :3] for start, end in column_runs],
        axis=1,
    ).astype(np.uint8)
    colorfulness = np.max(content_pixels, axis=2) - np.min(content_pixels, axis=2)
    if float(np.median(colorfulness)) > 18.0:
        return []
    grayscale = cv2.cvtColor(content_pixels, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gradient = np.mean(np.abs(np.diff(grayscale, axis=0)), axis=1)
    active_gradient = gradient[content_start : max(content_start + 1, content_end - 1)]
    minimum_peak = max(10.0, float(np.quantile(active_gradient, 0.85)))

    proposals: list[tuple[float, list[int]]] = []
    maximum_segments = min(12, (content_end - content_start) // 8)
    for segment_count in range(3, maximum_segments + 1):
        spacing = (content_end - content_start) / segment_count
        if float(np.median([width / spacing for width in content_widths])) < 2.5:
            continue
        tolerance = max(2, round(spacing * 0.16))
        cuts: list[int] = []
        strengths: list[float] = []
        for segment_index in range(1, segment_count):
            expected = content_start + segment_index * spacing
            left = max(content_start + 2, round(expected) - tolerance)
            right = min(content_end - 2, round(expected) + tolerance)
            if right <= left:
                break
            peak = left + int(np.argmax(gradient[left : right + 1]))
            strength = float(gradient[peak])
            if strength < minimum_peak:
                break
            cuts.append(peak + 1)
            strengths.append(strength)
        if len(cuts) != segment_count - 1 or len(set(cuts)) != len(cuts):
            continue
        boundaries = [content_start, *cuts, content_end]
        segment_lengths = np.diff(boundaries)
        if float(np.min(segment_lengths)) < spacing * 0.65:
            continue
        if float(np.max(segment_lengths)) > spacing * 1.35:
            continue
        densities = [
            float(crop[top:bottom, left:right].mean())
            for top, bottom in pairwise(boundaries)
            for left, right in column_runs
        ]
        if float(np.mean(np.asarray(densities) >= 0.5)) < 0.9:
            continue
        score = (
            float(np.mean(strengths)) / minimum_peak
            - float(np.std(segment_lengths)) / spacing
            + segment_count * 0.01
        )
        proposals.append((score, boundaries))
    if not proposals:
        return []

    _, boundaries = max(proposals, key=lambda proposal: proposal[0])
    children: list[tuple[int, int, int, int]] = []
    padding = 1
    for top, bottom in pairwise(boundaries):
        for left, right in column_runs:
            child_left = max(0, x + left - padding)
            child_top = max(0, y + top - padding)
            child_right = min(raw_mask.shape[1], x + right + padding)
            child_bottom = min(raw_mask.shape[0], y + bottom + padding)
            children.append(
                (
                    child_left,
                    child_top,
                    child_right - child_left,
                    child_bottom - child_top,
                )
            )
    return children if 3 <= len(children) <= 30 else []


def _split_repeated_strip_boxes(
    image: NDArray,
    raw_mask: NDArray,
    boxes: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Split confident rows/columns of repeated image tiles inside a large island.

    Figure labels and thin borders commonly connect otherwise independent microscopy
    tiles or blot strips into one contour.  Only highly regular, image-dense runs are
    split here; irregular charts and sparse text remain a single panel.
    """

    proposals = [_split_repeated_strip_box(image, raw_mask, box) for box in boxes]
    strip_expansion = sum(
        len(children) - 1
        for box, children in zip(boxes, proposals, strict=True)
        if children and box[3] > box[2]
    )
    if strip_expansion > MAX_REPEATED_STRIP_EXPANSION_PER_PAGE:
        proposals = [
            [] if children and box[3] > box[2] else children
            for box, children in zip(boxes, proposals, strict=True)
        ]

    refined: list[tuple[int, int, int, int]] = []
    for index, (box, children) in enumerate(zip(boxes, proposals, strict=True)):
        remaining_parent_count = len(boxes) - index - 1
        if (
            children
            and len(refined) + len(children) + remaining_parent_count <= MAX_PANELS_PER_PAGE
        ):
            refined.extend(children)
        else:
            refined.append(box)
    return refined


def _split_repeated_strip_box(
    image: NDArray,
    raw_mask: NDArray,
    box: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    _, _, box_width, box_height = box
    if min(box_width, box_height) < 18:
        return []
    for split_rows in (True, False):
        children = _split_repeated_strip_box_axis(
            image,
            raw_mask,
            box,
            split_rows=split_rows,
        )
        if children:
            return children
    return []


def _split_repeated_strip_box_axis(
    image: NDArray,
    raw_mask: NDArray,
    box: tuple[int, int, int, int],
    *,
    split_rows: bool,
) -> list[tuple[int, int, int, int]]:
    x, y, box_width, box_height = box

    crop = raw_mask[y : y + box_height, x : x + box_width] > 0
    cross_offset = 0
    analysis_crop = crop
    if split_rows:
        column_runs = _regular_projection_runs(crop.mean(axis=0))
        if len(column_runs) != 1:
            return []
        column_start, column_end = column_runs[0]
        cross_offset = column_start
        analysis_crop = crop[:, column_start:column_end]
        runs = _regular_projection_runs(analysis_crop.mean(axis=1))
    else:
        projection = analysis_crop.mean(axis=0)
        runs = _true_runs(projection > 0.06)
    if not 3 <= len(runs) <= 16:
        return []

    axis_length = box_height if split_rows else box_width
    minimum_run = max(8, round(axis_length * 0.025))
    run_lengths = np.asarray([end - start for start, end in runs], dtype=np.float32)
    if np.any(run_lengths < minimum_run):
        return []
    median_length = float(np.median(run_lengths))
    regular = (run_lengths >= median_length * 0.55) & (run_lengths <= median_length * 1.8)
    if float(np.mean(regular)) < 0.8:
        return []

    children: list[tuple[int, int, int, int]] = []
    cross_coverages: list[float] = []
    densities: list[float] = []
    padding = 2
    for start, end in runs:
        segment = analysis_crop[start:end, :] if split_rows else analysis_crop[:, start:end]
        points = np.argwhere(segment)
        if points.size == 0:
            return []
        segment_top, segment_left = points.min(axis=0)
        segment_bottom, segment_right = points.max(axis=0) + 1
        cross_extent = segment_right - segment_left if split_rows else segment_bottom - segment_top
        cross_length = box_width if split_rows else box_height
        cross_coverages.append(cross_extent / cross_length)
        densities.append(float(segment.mean()))

        if split_rows:
            left = max(0, x + cross_offset + segment_left - padding)
            top = max(0, y + start + segment_top - padding)
            right = min(
                raw_mask.shape[1],
                x + cross_offset + segment_right + padding,
            )
            bottom = min(raw_mask.shape[0], y + start + segment_bottom + padding)
        else:
            left = max(0, x + start + segment_left - padding)
            top = max(0, y + segment_top - padding)
            right = min(raw_mask.shape[1], x + start + segment_right + padding)
            bottom = min(raw_mask.shape[0], y + segment_bottom + padding)
        children.append((left, top, right - left, bottom - top))

    if float(np.mean(np.asarray(cross_coverages) >= 0.65)) < 0.8:
        return []
    if float(np.median(densities)) < 0.16:
        return []
    child_aspects = [
        max(child_width, child_height) / max(min(child_width, child_height), 1)
        for _, _, child_width, child_height in children
    ]
    elongated_strips = split_rows and float(np.mean(np.asarray(child_aspects) >= 2.5)) >= 0.8
    microscopy_tile_row = (
        not split_rows and 4 <= len(children) <= 6 and float(np.median(child_aspects)) <= 2.0
    )
    if not elongated_strips and not microscopy_tile_row:
        return []
    child_colorfulness: list[float] = []
    for left, top, child_width, child_height in children:
        child = image[top : top + child_height, left : left + child_width, :3].astype(np.float32)
        child_colorfulness.append(float(np.mean(np.max(child, axis=2) - np.min(child, axis=2))))
    if float(np.median(child_colorfulness)) > 18.0:
        return []
    return children


def _true_runs(values: NDArray) -> list[tuple[int, int]]:
    padded = np.pad(values.astype(np.int8), (1, 1))
    transitions = np.diff(padded)
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def remap_panel_findings(
    findings: Iterable[Finding], materialization: PanelMaterialization
) -> list[Finding]:
    return [_remap_finding(finding, materialization) for finding in findings]


def prioritize_panel_findings(findings: Iterable[Finding]) -> tuple[list[Finding], int]:
    """Keep dense panel scans reviewable without starving independent source pairs."""

    items = list(findings)
    groups: dict[tuple[str, tuple[str, ...]], list[int]] = {}
    limited_types = {FindingType.SUSPECTED_REUSE, FindingType.HIGH_SIMILARITY}
    for index, finding in enumerate(items):
        if finding.finding_type not in limited_types or len(finding.locations) < 2:
            continue
        source_pair = tuple(sorted(location.source_path for location in finding.locations))
        groups.setdefault((finding.rule_id, source_pair), []).append(index)

    retained = set(range(len(items)))
    suppressed = 0
    risk_rank = {"high": 2, "medium": 1, "low": 0}
    for indices in groups.values():
        if len(indices) <= MAX_PANEL_FINDINGS_PER_SOURCE_PAIR_RULE:
            continue
        ordered = sorted(
            indices,
            key=lambda index: (
                risk_rank.get(str(items[index].risk), -1),
                items[index].confidence,
                items[index].finding_id,
            ),
            reverse=True,
        )
        group_size = len(ordered)
        for rank, index in enumerate(ordered, start=1):
            if rank > MAX_PANEL_FINDINGS_PER_SOURCE_PAIR_RULE:
                retained.discard(index)
                suppressed += 1
                continue
            details = dict(items[index].details)
            details.update(
                {
                    "panel_candidate_group_size": group_size,
                    "panel_candidate_group_rank": rank,
                    "panel_candidate_group_limit": MAX_PANEL_FINDINGS_PER_SOURCE_PAIR_RULE,
                    "panel_candidate_group_suppressed": (
                        group_size - MAX_PANEL_FINDINGS_PER_SOURCE_PAIR_RULE
                    ),
                }
            )
            items[index] = replace(items[index], details=details)
    return [item for index, item in enumerate(items) if index in retained], suppressed


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
