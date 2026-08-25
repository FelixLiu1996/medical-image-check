from __future__ import annotations

from enum import StrEnum


class ImageAnalysisMode(StrEnum):
    AUTO = "auto"
    GENERIC = "generic"
    WESTERN_BLOT = "western_blot"
    DOT_BLOT = "dot_blot"
    FLUORESCENCE = "fluorescence"
    PATHOLOGY = "pathology"


IMAGE_ANALYSIS_MODE_LABELS = {
    ImageAnalysisMode.AUTO: "自动识别（推荐）",
    ImageAnalysisMode.GENERIC: "仅通用图片",
    ImageAnalysisMode.WESTERN_BLOT: "Western blot",
    ImageAnalysisMode.DOT_BLOT: "Dot blot / 斑点阵列",
    ImageAnalysisMode.FLUORESCENCE: "荧光图",
    ImageAnalysisMode.PATHOLOGY: "病理切片",
}
