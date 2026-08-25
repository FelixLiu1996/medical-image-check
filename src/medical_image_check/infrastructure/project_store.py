from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from medical_image_check.domain.project import PROJECT_SCHEMA_VERSION, Project


class ProjectStore:
    def save(self, project: Project, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(project), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)

    def load(self, path: str | Path) -> Project:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        schema_version = payload.get("schema_version")
        if schema_version != PROJECT_SCHEMA_VERSION:
            raise ValueError(
                f"不支持的项目版本：{schema_version!r}，当前版本为 {PROJECT_SCHEMA_VERSION}"
            )
        source_paths = payload.get("source_paths")
        if not isinstance(source_paths, list):
            raise ValueError("项目文件中的 source_paths 无效")
        return Project(
            project_id=str(payload["project_id"]),
            name=str(payload["name"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            source_paths=tuple(str(item) for item in source_paths),
            schema_version=schema_version,
        )
