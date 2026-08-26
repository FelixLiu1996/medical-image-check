from __future__ import annotations

from dataclasses import replace

from medical_image_check.domain.models import Finding, ReviewStatus, ScanResult


def update_finding_review_status(
    result: ScanResult,
    finding_id: str,
    status: ReviewStatus | str,
) -> ScanResult:
    normalized = ReviewStatus(status)
    found = False
    changed = False
    findings = []
    for finding in result.findings:
        if finding.finding_id != finding_id:
            findings.append(finding)
            continue
        found = True
        changed = finding.review_status != normalized
        findings.append(replace(finding, review_status=normalized))
    if not found:
        raise ValueError(f"找不到结果：{finding_id}")
    return replace(result, findings=tuple(findings)) if changed else result


def carry_forward_review_status(previous: ScanResult | None, current: ScanResult) -> ScanResult:
    if previous is None or previous.algorithm_version != current.algorithm_version:
        return current
    statuses = {
        finding.finding_id: finding.review_status
        for finding in previous.findings
        if finding.review_status != ReviewStatus.PENDING
    }
    if not statuses:
        return current
    findings = tuple(
        replace(finding, review_status=statuses.get(finding.finding_id, finding.review_status))
        for finding in current.findings
    )
    return replace(current, findings=findings)


def marked_findings(result: ScanResult) -> tuple[Finding, ...]:
    return tuple(
        finding for finding in result.findings if finding.review_status != ReviewStatus.PENDING
    )
