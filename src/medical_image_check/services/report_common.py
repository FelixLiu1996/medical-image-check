from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from medical_image_check.domain.models import FindingType, ReviewStatus, RiskLevel
from medical_image_check.infrastructure.images import canonical_pixels, decode_image_pages

RISK_LABELS = {
    RiskLevel.HIGH: "高",
    RiskLevel.MEDIUM: "中",
    RiskLevel.LOW: "低",
}
TYPE_LABELS = {
    FindingType.EXACT_DUPLICATE: "确认重复",
    FindingType.SUSPECTED_REUSE: "疑似复用",
    FindingType.HIGH_SIMILARITY: "高度相似",
    FindingType.NORMAL_RELATION: "正常关联",
    FindingType.STATISTICAL_ANOMALY: "统计异常",
}
REVIEW_LABELS = {
    ReviewStatus.PENDING: "待复核",
    ReviewStatus.CONFIRMED: "准确",
    ReviewStatus.NORMAL: "正常关联",
    ReviewStatus.FALSE_POSITIVE: "误报",
}
EVIDENCE_KIND_LABELS = {
    "western_blot": "Western blot",
    "dot_blot": "Dot blot",
    "local_pattern": "局部结构",
    "fluorescence": "荧光",
    "pathology": "病理",
}
RELATIONSHIP_LABELS = {
    "normal_merge_component": "单通道与合并图正常关系",
    "normal_same_field_channels": "不同通道同视野正常关系",
    "suspected_same_channel_reuse": "同通道疑似复用",
    "normal_different_magnification": "不同倍率正常关系",
    "suspected_pathology_reuse": "组织区域疑似复用",
    "normal_derived_column": "原始列与派生列正常关系",
}
ATTENTION_LABELS = {
    "primary": "重点候选",
    "secondary": "次要线索",
    "normal": "正常关系",
}
CHANNEL_LABELS = {
    "blue": "蓝色通道",
    "green": "绿色通道",
    "red": "红色通道",
    "far_red": "远红通道",
    "gray": "灰度通道",
    "merge": "合并图",
    "unknown": "未识别",
}
REPORT_DISCLAIMER = "本报告仅提供科研数据复核候选证据，不自动判定学术不端。"


def attention_label(details: dict) -> str:
    return ATTENTION_LABELS.get(str(details.get("attention_tier", "")), "-")


def evidence_region(details: dict, prefix: str) -> tuple[int, int, int, int] | None:
    keys = tuple(f"{prefix}_region_{suffix}" for suffix in ("x", "y", "width", "height"))
    if not all(key in details for key in keys):
        return None
    try:
        values = [int(details[key]) for key in keys]
    except (TypeError, ValueError):
        return None
    if values[2] <= 0 or values[3] <= 0:
        return None
    return values[0], values[1], values[2], values[3]


def evidence_page(coordinate: str | None) -> int:
    match = re.search(r"第\s*(\d+)\s*页", coordinate or "")
    return max(1, int(match.group(1))) if match else 1


def image_preview_png(
    source_path: str,
    page: int,
    region: tuple[int, int, int, int] | None,
    max_width: int = 420,
    max_height: int = 260,
) -> bytes | None:
    return _cached_image_preview(source_path, page, region, max_width, max_height)


def clear_image_preview_cache() -> None:
    _cached_image_preview.cache_clear()


@lru_cache(maxsize=512)
def _cached_image_preview(
    source_path: str,
    page: int,
    region: tuple[int, int, int, int] | None,
    max_width: int,
    max_height: int,
) -> bytes | None:
    try:
        pages = decode_image_pages(Path(source_path))
        image = canonical_pixels(pages[min(max(page, 1), len(pages)) - 1])[:, :, :3]
    except (OSError, ValueError, IndexError):
        return None
    if image.dtype != np.uint8:
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    height, width = image.shape[:2]
    if region is not None:
        x, y, region_width, region_height = region
        padding_x = max(4, round(region_width * 0.12))
        padding_y = max(4, round(region_height * 0.12))
        left = max(0, x - padding_x)
        top = max(0, y - padding_y)
        right = min(width, x + region_width + padding_x)
        bottom = min(height, y + region_height + padding_y)
        if right > left and bottom > top:
            image = np.ascontiguousarray(image[top:bottom, left:right])
            cv2.rectangle(
                image,
                (max(0, x - left), max(0, y - top)),
                (
                    min(right - left - 1, x + region_width - left),
                    min(bottom - top - 1, y + region_height - top),
                ),
                (0, 176, 255),
                max(2, round(min(image.shape[:2]) / 120)),
            )
    preview_height, preview_width = image.shape[:2]
    scale = min(1.0, max_width / max(preview_width, 1), max_height / max(preview_height, 1))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, round(preview_width * scale)), max(1, round(preview_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    success, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    return encoded.tobytes() if success else None
