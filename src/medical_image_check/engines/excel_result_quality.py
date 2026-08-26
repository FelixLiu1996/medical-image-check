from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from medical_image_check.domain.models import Finding, FindingType, RiskLevel

ATTENTION_PRIMARY = "primary"
ATTENTION_SECONDARY = "secondary"
ATTENTION_NORMAL = "normal"

MAX_PRIMARY_FINDINGS = 50
MAX_PRIMARY_PER_WORKBOOK = 10
MAX_PRIMARY_PER_SHEET = 3
MAX_PRIMARY_PER_RULE = 16
MAX_PRIMARY_DIGIT_FRAGMENTS = 12

_RULE_STRENGTH = {
    "excel.region.exact": 100,
    "excel.row.exact": 98,
    "excel.series.exact": 96,
    "excel.series.fragment_exact": 94,
    "excel.series.shuffled": 90,
    "excel.series.near_duplicate": 88,
    "excel.series.target_sum": 86,
    "excel.series.target_product": 86,
    "excel.series.target_difference": 84,
    "excel.series.target_quotient": 84,
    "excel.series.scale": 82,
    "excel.series.offset": 82,
    "excel.series.linear": 80,
    "excel.digit_fragment": 76,
    "excel.series.statistics": 30,
    "excel.value.exact": 20,
    "excel.value.approximate": 10,
    "excel.cell.target_operation": 8,
}


def improve_excel_result_quality(findings: list[Finding]) -> list[Finding]:
    """Collapse duplicate explanations and tag a bounded default review queue.

    Secondary clues remain in the scan result and reports. The attention tier is
    a presentation policy, not an assertion that omitted clues are false.
    """

    consolidated = _consolidate_series_explanations(findings)
    return _assign_attention_tiers(consolidated)


def is_primary_excel_finding(finding: Finding) -> bool:
    tier = finding.details.get("attention_tier")
    if tier is not None:
        return tier == ATTENTION_PRIMARY
    return finding.finding_type != FindingType.NORMAL_RELATION and finding.risk in {
        RiskLevel.HIGH,
        RiskLevel.MEDIUM,
    }


def count_primary_excel_findings(findings: tuple[Finding, ...] | list[Finding]) -> int:
    return sum(is_primary_excel_finding(finding) for finding in findings)


def _consolidate_series_explanations(findings: list[Finding]) -> list[Finding]:
    grouped: dict[tuple[object, ...], list[Finding]] = defaultdict(list)
    standalone: list[Finding] = []
    for finding in findings:
        key = _series_relation_key(finding)
        if key is None:
            standalone.append(finding)
        else:
            grouped[key].append(finding)

    consolidated = list(standalone)
    for group in grouped.values():
        ordered = sorted(group, key=_explanation_sort_key)
        related_rules = [
            {
                "rule_id": item.rule_id,
                "title": item.title,
                "parameter": str(item.details.get("parameter", "")),
            }
            for item in ordered
        ]
        for index, finding in enumerate(ordered):
            details = dict(finding.details)
            details["relation_group_primary"] = index == 0
            details["merged_rule_count"] = len(ordered)
            details["related_rules"] = related_rules
            description = finding.description
            if index == 0 and len(ordered) > 1:
                description = (
                    f"{description} 同一列关系还命中 {len(ordered) - 1} 条等价或从属规则，"
                    "重点列表仅显示本项。"
                )
            consolidated.append(replace(finding, description=description, details=details))
    return consolidated


def _series_relation_key(finding: Finding) -> tuple[object, ...] | None:
    if not finding.rule_id.startswith("excel.series."):
        return None
    first = _series_identity(finding.details.get("first_series"))
    second = _series_identity(finding.details.get("second_series"))
    if first is None or second is None:
        return None
    return ("series", *sorted((first, second)))


def _series_identity(value: object) -> tuple[str, str, str, str, str] | None:
    if not isinstance(value, dict):
        return None
    source = value.get("source_path")
    sheet = value.get("sheet")
    column = value.get("column")
    coordinates = value.get("coordinates")
    if not isinstance(source, str) or not isinstance(sheet, str) or not isinstance(column, str):
        return None
    if not isinstance(coordinates, list) or not coordinates:
        return None
    return source, sheet, column, str(coordinates[0]), str(coordinates[-1])


def _explanation_sort_key(finding: Finding) -> tuple[object, ...]:
    normal = finding.finding_type == FindingType.NORMAL_RELATION
    return (
        0 if normal else 1,
        -_RULE_STRENGTH.get(finding.rule_id, 50),
        -_matched_count(finding),
        -finding.confidence,
        finding.finding_id,
    )


def _assign_attention_tiers(findings: list[Finding]) -> list[Finding]:
    eligible = [
        finding
        for finding in findings
        if finding.finding_type != FindingType.NORMAL_RELATION
        and finding.risk in {RiskLevel.HIGH, RiskLevel.MEDIUM}
        and finding.details.get("relation_group_primary", True) is not False
    ]
    eligible.sort(key=_attention_sort_key)

    primary_ids: set[str] = set()
    per_workbook: Counter[str] = Counter()
    per_sheet: Counter[tuple[str, str]] = Counter()
    per_rule: Counter[str] = Counter()
    for finding in eligible:
        workbooks, sheets = _finding_scopes(finding)
        rule_limit = (
            MAX_PRIMARY_DIGIT_FRAGMENTS
            if finding.rule_id == "excel.digit_fragment"
            else MAX_PRIMARY_PER_RULE
        )
        if per_rule[finding.rule_id] >= rule_limit:
            continue
        if workbooks and any(per_workbook[item] >= MAX_PRIMARY_PER_WORKBOOK for item in workbooks):
            continue
        if sheets and any(per_sheet[item] >= MAX_PRIMARY_PER_SHEET for item in sheets):
            continue
        primary_ids.add(finding.finding_id)
        per_workbook.update(workbooks)
        per_sheet.update(sheets)
        per_rule[finding.rule_id] += 1
        if len(primary_ids) >= MAX_PRIMARY_FINDINGS:
            break

    improved: list[Finding] = []
    for finding in findings:
        if finding.finding_type == FindingType.NORMAL_RELATION:
            tier = ATTENTION_NORMAL
        elif finding.finding_id in primary_ids:
            tier = ATTENTION_PRIMARY
        else:
            tier = ATTENTION_SECONDARY
        details = dict(finding.details)
        details["attention_tier"] = tier
        improved.append(replace(finding, details=details))
    return improved


def _attention_sort_key(finding: Finding) -> tuple[object, ...]:
    return (
        {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2}[finding.risk],
        -_RULE_STRENGTH.get(finding.rule_id, 50),
        -_matched_count(finding),
        -_distinct_value_count(finding),
        -finding.confidence,
        finding.finding_id,
    )


def _matched_count(finding: Finding) -> int:
    value = finding.details.get("matched_count", finding.details.get("maximum_length", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _distinct_value_count(finding: Finding) -> int:
    value = finding.details.get("distinct_value_count")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    values: set[str] = set()
    paired = finding.details.get("paired_values", [])
    if isinstance(paired, list):
        for pair in paired:
            if not isinstance(pair, dict):
                continue
            for key in ("first_value", "second_value"):
                if pair.get(key) is not None:
                    values.add(str(pair[key]))
    return len(values)


def _finding_scopes(finding: Finding) -> tuple[set[str], set[tuple[str, str]]]:
    workbooks = {str(Path(location.source_path)) for location in finding.locations}
    sheets = {
        (str(Path(location.source_path)), location.sheet or "") for location in finding.locations
    }
    return workbooks, sheets
