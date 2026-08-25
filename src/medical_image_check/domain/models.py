from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256


class RiskLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingType(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    SUSPECTED_REUSE = "suspected_reuse"
    HIGH_SIMILARITY = "high_similarity"
    NORMAL_RELATION = "normal_relation"
    STATISTICAL_ANOMALY = "statistical_anomaly"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    NORMAL = "normal"
    FALSE_POSITIVE = "false_positive"


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    source_path: str
    sheet: str | None = None
    coordinate: str | None = None
    hidden_sheet: bool = False

    @property
    def stable_key(self) -> str:
        return "|".join(
            (
                self.source_path,
                self.sheet or "",
                self.coordinate or "",
                "hidden" if self.hidden_sheet else "visible",
            )
        )

    @property
    def display_text(self) -> str:
        parts = [self.source_path]
        if self.sheet:
            parts.append(self.sheet)
        if self.coordinate:
            parts.append(self.coordinate)
        return " / ".join(parts)


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    rule_id: str
    finding_type: FindingType
    risk: RiskLevel
    title: str
    description: str
    locations: tuple[EvidenceLocation, ...]
    confidence: float = 1.0
    details: dict[str, str | int | float] = field(default_factory=dict)
    review_status: ReviewStatus = ReviewStatus.PENDING


@dataclass(frozen=True, slots=True)
class ScanIssue:
    source_path: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class ScanResult:
    source_count: int
    image_count: int
    spreadsheet_count: int
    findings: tuple[Finding, ...]
    issues: tuple[ScanIssue, ...] = ()
    algorithm_version: str = "exact-baseline-1"
    completed_at: str | None = None


def deterministic_finding_id(rule_id: str, locations: tuple[EvidenceLocation, ...]) -> str:
    keys = "\n".join(sorted(location.stable_key for location in locations))
    return sha256(f"{rule_id}\n{keys}".encode()).hexdigest()[:20]
