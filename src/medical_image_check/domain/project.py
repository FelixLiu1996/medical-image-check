from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    created_at: str
    updated_at: str
    source_paths: tuple[str, ...]
    schema_version: int = PROJECT_SCHEMA_VERSION

    @classmethod
    def create(cls, name: str) -> Project:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("项目名称不能为空")
        now = _now_iso()
        return cls(
            project_id=str(uuid4()),
            name=normalized_name,
            created_at=now,
            updated_at=now,
            source_paths=(),
        )

    def with_sources(self, paths: list[str | Path]) -> Project:
        combined = {str(Path(path).expanduser().resolve()) for path in self.source_paths}
        combined.update(str(Path(path).expanduser().resolve()) for path in paths)
        return replace(self, source_paths=tuple(sorted(combined)), updated_at=_now_iso())
