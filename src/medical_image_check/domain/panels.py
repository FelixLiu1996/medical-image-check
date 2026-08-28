from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PanelSelection:
    """One user-reviewable crop on an original image page."""

    source_path: str
    page: int
    panel_index: int
    x: int
    y: int
    width: int
    height: int
    selected: bool = True

    def __post_init__(self) -> None:
        if self.page < 1 or self.panel_index < 1:
            raise ValueError("子面板页码和序号必须从 1 开始")
        if min(self.x, self.y) < 0 or min(self.width, self.height) < 1:
            raise ValueError("子面板区域无效")

    @property
    def normalized_source_path(self) -> str:
        return str(Path(self.source_path).expanduser().resolve())

    @property
    def stable_key(self) -> str:
        raw = (
            f"{self.normalized_source_path}|{self.page}|"
            f"{self.x},{self.y},{self.width},{self.height}"
        )
        return sha256(raw.encode()).hexdigest()[:20]

    @property
    def display_name(self) -> str:
        return f"{Path(self.source_path).name} · 第 {self.page} 页 · 子面板 {self.panel_index}"
