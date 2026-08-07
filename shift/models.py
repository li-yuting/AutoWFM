from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils import normalize_shift


@dataclass
class ShiftCell:
    value: Any = None
    original_value: Any = None
    is_locked: bool = False
    is_historical: bool = False
    row: int = 0
    column: int = 0

    @property
    def base_shift(self) -> str:
        return normalize_shift(self.value)

    @property
    def is_blank(self) -> bool:
        return self.value is None or str(self.value).strip() == ""

    def set_value(self, value: str) -> None:
        if not self.is_locked:
            self.value = value


@dataclass
class Employee:
    name: str
    group: str
    coefficient: float
    is_phase3: bool
    schedule: list[ShiftCell]
    row_index: int
    person_type: str = ""

    @property
    def is_newbie(self) -> bool:
        return self.coefficient < 1.0


@dataclass
class DailyDemand:
    date: Any
    demand: dict[str, float]

    def get(self, shift: str) -> float:
        return self.demand.get(shift, 0.0)


@dataclass
class AdjustedDemand:
    date: Any
    original: dict[str, float]
    adjusted: dict[str, float]
    off_to_a3: float = 0.0
    a3_to_off: float = 0.0

    def get(self, shift: str) -> float:
        return self.adjusted.get(shift, 0.0)


@dataclass
class Warning:
    check_id: str
    severity: str
    message: str
    employee: str = ""
    date: Any = None


@dataclass
class Schedule:
    employees: list[Employee]
    dates: list[Any]
    demands: list[DailyDemand]
    workbook_path: str
    history_days: int = 6
    schedule_sheet_name: str = "班表"
    demand_sheet_name: str = "需求"
    date_columns: list[int] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    adjusted_demands: list[AdjustedDemand] = field(default_factory=list)

    @property
    def work_start_index(self) -> int:
        return min(self.history_days, len(self.dates))

    @property
    def active_indexes(self) -> range:
        return range(self.work_start_index, len(self.dates))

    def cell(self, employee: Employee, day_index: int) -> ShiftCell:
        return employee.schedule[day_index]
