from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from hashlib import sha256
from pathlib import Path

from medical_image_check.domain.models import (
    EvidenceLocation,
    Finding,
    FindingType,
    RiskLevel,
    ScanIssue,
    deterministic_finding_id,
)

SUPPORTED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"})


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class ExactImageDuplicateDetector:
    rule_id = "image.file.sha256"

    def scan(
        self,
        paths: Iterable[Path],
        on_file: Callable[[Path], None] | None = None,
    ) -> tuple[list[Finding], list[ScanIssue]]:
        hashes: dict[str, list[Path]] = defaultdict(list)
        issues: list[ScanIssue] = []

        for path in paths:
            try:
                hashes[sha256_file(path)].append(path)
            except OSError as exc:
                issues.append(ScanIssue(str(path), f"无法读取图片：{exc}", "error"))
            finally:
                if on_file:
                    on_file(path)

        findings: list[Finding] = []
        for digest, duplicate_paths in hashes.items():
            if len(duplicate_paths) < 2:
                continue
            locations = tuple(
                EvidenceLocation(str(path)) for path in sorted(duplicate_paths, key=str)
            )
            findings.append(
                Finding(
                    finding_id=deterministic_finding_id(self.rule_id, locations),
                    rule_id=self.rule_id,
                    finding_type=FindingType.EXACT_DUPLICATE,
                    risk=RiskLevel.HIGH,
                    title="图片文件完全重复",
                    description=f"{len(locations)} 个图片文件具有相同的 SHA-256 指纹。",
                    locations=locations,
                    details={"sha256": digest, "count": len(locations)},
                )
            )

        return findings, issues
