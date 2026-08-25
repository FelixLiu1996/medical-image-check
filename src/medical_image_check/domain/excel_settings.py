from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from medical_image_check.domain.models import RiskLevel

DEFAULT_EXCEL_ABSOLUTE_TOLERANCE = "0.000000000001"
DEFAULT_EXCEL_OPERATION_TARGETS = ("0", "1", "10", "100", "1000")
DEFAULT_EXCEL_MEDIUM_RUN_LENGTH = 3
DEFAULT_EXCEL_HIGH_RUN_LENGTH = 4
DEFAULT_TRANSFORM_RELATIVE_TOLERANCE = Decimal("1e-9")


@dataclass(frozen=True, slots=True)
class ExcelAnalysisSettings:
    custom_relative_tolerance_percent: Decimal = Decimal(0)
    absolute_tolerance: Decimal = Decimal(DEFAULT_EXCEL_ABSOLUTE_TOLERANCE)
    operation_targets: tuple[Decimal, ...] = tuple(
        Decimal(value) for value in DEFAULT_EXCEL_OPERATION_TARGETS
    )
    medium_run_length: int = DEFAULT_EXCEL_MEDIUM_RUN_LENGTH
    high_run_length: int = DEFAULT_EXCEL_HIGH_RUN_LENGTH

    def __post_init__(self) -> None:
        if not self.custom_relative_tolerance_percent.is_finite() or not (
            Decimal(0) <= self.custom_relative_tolerance_percent <= Decimal(100)
        ):
            raise ValueError("Excel 自定义相对容差必须在 0% 到 100% 之间")
        if not self.absolute_tolerance.is_finite() or self.absolute_tolerance <= 0:
            raise ValueError("Excel 绝对容差必须是大于 0 的有限数值")
        if not self.operation_targets:
            raise ValueError("Excel 运算目标至少需要一个数值")
        if len(self.operation_targets) > 20:
            raise ValueError("Excel 运算目标最多允许 20 个")
        if any(not value.is_finite() for value in self.operation_targets):
            raise ValueError("Excel 运算目标必须是有限数值")
        if self.medium_run_length < 2:
            raise ValueError("Excel 中风险连续次数至少为 2")
        if self.high_run_length <= self.medium_run_length:
            raise ValueError("Excel 高风险连续次数必须大于中风险连续次数")

    @classmethod
    def from_values(
        cls,
        custom_relative_tolerance_percent: str | float | Decimal = 0,
        absolute_tolerance: str | Decimal = DEFAULT_EXCEL_ABSOLUTE_TOLERANCE,
        operation_targets: Iterable[str | int | float | Decimal] = (
            DEFAULT_EXCEL_OPERATION_TARGETS
        ),
        medium_run_length: int = DEFAULT_EXCEL_MEDIUM_RUN_LENGTH,
        high_run_length: int = DEFAULT_EXCEL_HIGH_RUN_LENGTH,
    ) -> ExcelAnalysisSettings:
        try:
            relative = Decimal(str(custom_relative_tolerance_percent))
            absolute = Decimal(str(absolute_tolerance))
            targets = tuple(sorted({Decimal(str(value)) for value in operation_targets}))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Excel 容差和运算目标必须是有效数值") from exc
        return cls(relative, absolute, targets, medium_run_length, high_run_length)

    @property
    def custom_relative_tolerance(self) -> Decimal:
        return self.custom_relative_tolerance_percent / Decimal(100)

    @property
    def transform_relative_tolerance(self) -> Decimal:
        return max(DEFAULT_TRANSFORM_RELATIVE_TOLERANCE, self.custom_relative_tolerance)

    def close(self, first: Decimal, second: Decimal) -> bool:
        difference = abs(first - second)
        tolerance = self.absolute_tolerance + self.transform_relative_tolerance * max(
            abs(first), abs(second)
        )
        return difference <= tolerance

    def target_for(self, values: tuple[Decimal, ...]) -> Decimal | None:
        if not values:
            return None
        target = _median(values)
        integral = target.to_integral_value()
        candidates = (*self.operation_targets, integral)
        return next(
            (
                candidate
                for candidate in candidates
                if all(self.close(value, candidate) for value in values)
            ),
            None,
        )

    def risk_for_run(self, matched_count: int) -> RiskLevel:
        if matched_count >= self.high_run_length:
            return RiskLevel.HIGH
        if matched_count >= self.medium_run_length:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
