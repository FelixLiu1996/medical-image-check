from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from openpyxl.formula import Tokenizer
from openpyxl.utils.cell import range_boundaries

from medical_image_check.infrastructure.spreadsheets import NumericCell

_DERIVED_HEADER_PATTERN = re.compile(
    r"(?:rescal(?:e|ed|ing)|normalis(?:e|ed|ation)|normaliz(?:e|ed|ation)|"
    r"standardis(?:e|ed|ation)|standardiz(?:e|ed|ation)|fold[\s_-]*change|"
    r"relative(?:\s+(?:value|level|expression))?|percentage|percent|ratio|"
    r"归一化|标准化|相对值|相对表达|百分比)",
    re.IGNORECASE,
)
_INDEX_HEADERS = frozenset(
    {
        "#",
        "index",
        "no",
        "no.",
        "number",
        "row",
        "序号",
        "编号",
        "行号",
    }
)


@dataclass(frozen=True, slots=True)
class DerivedColumnRelation:
    derived_side: str
    reason: str
    first_header: str | None
    second_header: str | None


def series_segment_key(cell: NumericCell) -> tuple[str, str, int, int | None]:
    """Keep vertically separated table blocks from becoming one synthetic series."""

    return (cell.source_path, cell.sheet, cell.column, cell.header_row)


def filter_low_information_cells(cells: list[NumericCell]) -> list[NumericCell]:
    """Remove cells that only belong to obvious row-index segments.

    Constant segments remain available to exact-value and configured single-cell
    operation rules, but the sequence/region rules reject them through their
    shared information gate. This preserves deliberate duplicate-value checks
    while keeping low-information columns out of the default review queue.
    """

    segments: dict[tuple[str, str, int, int | None], list[NumericCell]] = defaultdict(list)
    for cell in cells:
        segments[series_segment_key(cell)].append(cell)

    excluded: set[tuple[str, str, int, int | None]] = set()
    for key, segment in segments.items():
        header = series_header(tuple(segment))
        if _is_index_header(header) and _looks_like_index(tuple(segment)):
            excluded.add(key)
    return [cell for cell in cells if series_segment_key(cell) not in excluded]


def series_header(cells: tuple[NumericCell, ...]) -> str | None:
    headers = Counter(cell.column_header for cell in cells if cell.column_header)
    if not headers:
        return None
    return min(headers, key=lambda header: (-headers[header], header.casefold()))


def is_informative_values(values: tuple[Decimal, ...]) -> bool:
    return len(values) >= 2 and len(set(values)) >= 2 and any(value != 0 for value in values)


def is_informative_canonical_values(values: list[str] | tuple[str, ...]) -> bool:
    return len(values) >= 2 and len(set(values)) >= 2 and any(value != "0" for value in values)


def describe_derived_relation(
    first: tuple[NumericCell, ...],
    second: tuple[NumericCell, ...],
) -> DerivedColumnRelation | None:
    """Recognize an expected raw-to-derived column relation without evaluating formulas."""

    if len(first) < 3 or len(first) != len(second):
        return None
    if first[0].source_path != second[0].source_path or first[0].sheet != second[0].sheet:
        return None
    if any(left.row != right.row for left, right in zip(first, second, strict=True)):
        return None

    first_header = series_header(first)
    second_header = series_header(second)
    required_formula_matches = max(3, math.ceil(len(first) * 0.8))
    second_references_first = sum(
        bool(right.formula and _formula_references(right.formula, left.coordinate))
        for left, right in zip(first, second, strict=True)
    )
    if second_references_first >= required_formula_matches:
        return DerivedColumnRelation(
            "second",
            "同排公式引用第一列",
            first_header,
            second_header,
        )
    first_references_second = sum(
        bool(left.formula and _formula_references(left.formula, right.coordinate))
        for left, right in zip(first, second, strict=True)
    )
    if first_references_second >= required_formula_matches:
        return DerivedColumnRelation(
            "first",
            "同排公式引用第二列",
            first_header,
            second_header,
        )

    adjacent = abs(first[0].column - second[0].column) == 1
    first_is_derived = _is_derived_header(first_header)
    second_is_derived = _is_derived_header(second_header)
    if adjacent and second_is_derived and not first_is_derived:
        return DerivedColumnRelation(
            "second",
            "相邻列的派生含义表头",
            first_header,
            second_header,
        )
    if adjacent and first_is_derived and not second_is_derived:
        return DerivedColumnRelation(
            "first",
            "相邻列的派生含义表头",
            first_header,
            second_header,
        )
    return None


def _is_index_header(header: str | None) -> bool:
    if not header:
        return False
    normalized = " ".join(header.casefold().split()).strip("：:()[]")
    return normalized in _INDEX_HEADERS


def _looks_like_index(cells: tuple[NumericCell, ...]) -> bool:
    if len(cells) < 2:
        return False
    try:
        values = [int(cell.canonical_value) for cell in cells]
    except ValueError:
        return False
    return all(current - previous == 1 for previous, current in pairwise(values))


def _is_derived_header(header: str | None) -> bool:
    return bool(header and _DERIVED_HEADER_PATTERN.search(header))


def _formula_references(formula: str, coordinate: str) -> bool:
    try:
        tokens = Tokenizer(formula).items
    except Exception:  # noqa: BLE001 - malformed workbook formulas remain non-fatal metadata.
        return False
    for token in tokens:
        if token.type != "OPERAND" or token.subtype != "RANGE":
            continue
        reference = token.value.rsplit("!", 1)[-1].replace("$", "")
        try:
            minimum_column, minimum_row, maximum_column, maximum_row = range_boundaries(reference)
            target_column, target_row, _, _ = range_boundaries(coordinate)
        except ValueError:
            continue
        if (
            minimum_column <= target_column <= maximum_column
            and minimum_row <= target_row <= maximum_row
        ):
            return True
    return False
