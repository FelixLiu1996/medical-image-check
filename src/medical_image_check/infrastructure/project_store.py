from __future__ import annotations

import json
from pathlib import Path

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    ReviewStatus,
    RiskLevel,
    ScanIssue,
    ScanResult,
)
from medical_image_check.domain.project import PROJECT_SCHEMA_VERSION, Project


class ProjectStore:
    def save(self, project: Project, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_project_to_dict(project), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)

    def load(self, path: str | Path) -> Project:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        schema_version = payload.get("schema_version")
        if schema_version not in set(range(1, PROJECT_SCHEMA_VERSION + 1)):
            raise ValueError(
                f"不支持的项目版本：{schema_version!r}，当前版本为 {PROJECT_SCHEMA_VERSION}"
            )
        source_paths = payload.get("source_paths")
        if not isinstance(source_paths, list):
            raise ValueError("项目文件中的 source_paths 无效")
        report_paths = payload.get("report_paths", [])
        if not isinstance(report_paths, list):
            raise ValueError("项目文件中的 report_paths 无效")
        minimum_digit_run = int(payload.get("minimum_digit_run", 4))
        if minimum_digit_run not in range(3, 13):
            raise ValueError("项目文件中的 minimum_digit_run 必须在 3 到 12 之间")
        scan_payload = payload.get("last_scan_result")
        return Project(
            project_id=str(payload["project_id"]),
            name=str(payload["name"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            source_paths=tuple(str(item) for item in source_paths),
            minimum_digit_run=minimum_digit_run,
            last_scan_result=_scan_result_from_dict(scan_payload) if scan_payload else None,
            report_paths=tuple(str(item) for item in report_paths),
            schema_version=PROJECT_SCHEMA_VERSION,
        )


def _project_to_dict(project: Project) -> dict[str, object]:
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project.project_id,
        "name": project.name,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "source_paths": list(project.source_paths),
        "minimum_digit_run": project.minimum_digit_run,
        "last_scan_result": (
            _scan_result_to_dict(project.last_scan_result) if project.last_scan_result else None
        ),
        "report_paths": list(project.report_paths),
    }


def _scan_result_to_dict(result: ScanResult) -> dict[str, object]:
    return {
        "source_count": result.source_count,
        "image_count": result.image_count,
        "spreadsheet_count": result.spreadsheet_count,
        "findings": [_finding_to_dict(finding) for finding in result.findings],
        "issues": [
            {
                "source_path": issue.source_path,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in result.issues
        ],
        "algorithm_version": result.algorithm_version,
        "completed_at": result.completed_at,
    }


def _finding_to_dict(finding: Finding) -> dict[str, object]:
    return {
        "finding_id": finding.finding_id,
        "rule_id": finding.rule_id,
        "finding_type": finding.finding_type.value,
        "risk": finding.risk.value,
        "title": finding.title,
        "description": finding.description,
        "locations": [
            {
                "source_path": location.source_path,
                "sheet": location.sheet,
                "coordinate": location.coordinate,
                "hidden_sheet": location.hidden_sheet,
            }
            for location in finding.locations
        ],
        "confidence": finding.confidence,
        "details": finding.details,
        "review_status": finding.review_status.value,
    }


def _scan_result_from_dict(payload: object) -> ScanResult:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的 last_scan_result 无效")
    findings_payload = payload.get("findings", [])
    issues_payload = payload.get("issues", [])
    if not isinstance(findings_payload, list) or not isinstance(issues_payload, list):
        raise ValueError("项目文件中的扫描结果列表无效")
    return ScanResult(
        source_count=int(payload["source_count"]),
        image_count=int(payload["image_count"]),
        spreadsheet_count=int(payload["spreadsheet_count"]),
        findings=tuple(_finding_from_dict(item) for item in findings_payload),
        issues=tuple(_issue_from_dict(item) for item in issues_payload),
        algorithm_version=str(payload.get("algorithm_version", "exact-baseline-1")),
        completed_at=(str(payload["completed_at"]) if payload.get("completed_at") else None),
    )


def _finding_from_dict(payload: object) -> Finding:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的查重结果无效")
    locations_payload = payload.get("locations", [])
    details = payload.get("details", {})
    if not isinstance(locations_payload, list) or not isinstance(details, dict):
        raise ValueError("项目文件中的结果证据无效")
    return Finding(
        finding_id=str(payload["finding_id"]),
        rule_id=str(payload["rule_id"]),
        finding_type=FindingType(str(payload["finding_type"])),
        risk=RiskLevel(str(payload["risk"])),
        title=str(payload["title"]),
        description=str(payload["description"]),
        locations=tuple(_location_from_dict(item) for item in locations_payload),
        confidence=float(payload.get("confidence", 1.0)),
        details={str(key): value for key, value in details.items()},
        review_status=ReviewStatus(str(payload.get("review_status", ReviewStatus.PENDING))),
    )


def _location_from_dict(payload: object) -> EvidenceLocation:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的证据位置无效")
    return EvidenceLocation(
        source_path=str(payload["source_path"]),
        sheet=str(payload["sheet"]) if payload.get("sheet") is not None else None,
        coordinate=(str(payload["coordinate"]) if payload.get("coordinate") is not None else None),
        hidden_sheet=bool(payload.get("hidden_sheet", False)),
    )


def _issue_from_dict(payload: object) -> ScanIssue:
    if not isinstance(payload, dict):
        raise ValueError("项目文件中的扫描提示无效")
    return ScanIssue(
        source_path=str(payload["source_path"]),
        message=str(payload["message"]),
        severity=str(payload.get("severity", "warning")),
    )
