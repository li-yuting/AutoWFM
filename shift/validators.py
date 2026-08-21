from __future__ import annotations

from collections import defaultdict

from models import Employee, Schedule, Warning
from scheduler import SchedulerConfig
from utils import (
    A_CLASS_SHIFTS,
    ALL_SHIFTS,
    HIGH_BALANCE_SHIFTS,
    HIGH_LIMIT_SHIFTS,
    HIGH_SHIFTS,
    REST_SHIFT,
    SECONDARY_BALANCE_SHIFTS,
    WORK_SHIFTS,
    date_label,
)


def validate_schedule(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings: list[Warning] = []
    warnings.extend(_check_blanks(schedule))
    warnings.extend(_check_locked_cells(schedule))
    warnings.extend(_check_unknown_shifts(schedule))
    warnings.extend(_check_daily_demand(schedule, config))
    warnings.extend(_check_work_streaks(schedule, config))
    warnings.extend(_check_rest_streaks(schedule, config))
    warnings.extend(_check_rest_block_spacing(schedule, config))
    warnings.extend(_check_phase3(schedule))
    warnings.extend(_check_newbie_high(schedule))
    warnings.extend(_check_high_streaks(schedule, config))
    warnings.extend(_check_sandwich(schedule))
    warnings.extend(_check_balance(schedule, config))
    warnings.extend(_check_conversion(schedule))
    warnings.extend(_check_employee_rest_excess(schedule, config))
    _append_summary(warnings)
    schedule.warnings.extend(warnings)
    return warnings


def _check_blanks(schedule: Schedule) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        for idx in schedule.active_indexes:
            if employee.schedule[idx].is_blank:
                warnings.append(
                    Warning("01", "ERROR", "排班单元格为空", employee.name, schedule.dates[idx])
                )
    return warnings


def _check_locked_cells(schedule: Schedule) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        for cell in employee.schedule:
            if cell.is_locked and cell.value != cell.original_value:
                warnings.append(
                    Warning("02", "ERROR", "锁定单元格与输入不一致", employee.name)
                )
    return warnings


def _check_unknown_shifts(schedule: Schedule) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        for idx in schedule.active_indexes:
            base = employee.schedule[idx].base_shift
            if base and base not in ALL_SHIFTS:
                warnings.append(
                    Warning("13", "WARN", f"未知班次值: {employee.schedule[idx].value}", employee.name, schedule.dates[idx])
                )
    return warnings


def _check_daily_demand(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    for idx in schedule.active_indexes:
        actual = _actual_by_shift(schedule, idx)
        demand = schedule.adjusted_demands[idx]
        for shift in sorted(ALL_SHIFTS):
            diff = actual.get(shift, 0.0) - demand.get(shift)
            if abs(diff) > max(1.0, config.demand_tolerance * 2):
                warnings.append(
                    Warning(
                        "03",
                        "WARN",
                        f"{shift} 需求 {demand.get(shift):.2f}，实际 {actual.get(shift, 0.0):.2f}，差异 {diff:.2f}",
                        date=schedule.dates[idx],
                    )
                )
    return warnings


def _check_work_streaks(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        max_days = config.max_consecutive_work_phase3 if employee.is_phase3 else config.max_consecutive_work_normal
        for start, end, streak in _streaks(employee, WORK_SHIFTS):
            if streak > max_days:
                severity = _streak_severity(employee, start, end)
                warnings.append(
                    Warning("04", severity, f"连续上班 {streak} 天，超过 {max_days} 天", employee.name, schedule.dates[end])
                )
    return warnings


def _check_rest_streaks(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        for start, end, streak in _streaks(employee, {REST_SHIFT}):
            if streak > config.max_consecutive_rest:
                severity = _streak_severity(employee, start, end)
                warnings.append(
                    Warning("05", severity, f"连续休息 {streak} 天，超过 {config.max_consecutive_rest} 天", employee.name, schedule.dates[end])
                )
    return warnings


def _check_rest_block_spacing(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        blocks = _rest_blocks(employee)
        for prev_block, next_block in zip(blocks, blocks[1:]):
            gap = next_block[0] - prev_block[1] - 1
            if gap >= config.min_work_days_between_rest_blocks:
                continue
            if next_block[1] < schedule.work_start_index:
                continue
            severity = _multi_block_severity(employee, prev_block, next_block)
            warnings.append(
                Warning(
                    "14",
                    severity,
                    f"两次休息之间仅上班 {gap} 天，少于 {config.min_work_days_between_rest_blocks} 天",
                    employee.name,
                    schedule.dates[next_block[0]],
                )
            )
    return warnings


def _check_phase3(schedule: Schedule) -> list[Warning]:
    warnings = []
    allowed = {"A2", "A3", REST_SHIFT}
    for employee in schedule.employees:
        if not employee.is_phase3:
            continue
        for idx in schedule.active_indexes:
            base = employee.schedule[idx].base_shift
            if base and base not in allowed:
                warnings.append(
                    Warning("06", "ERROR", f"三期员工出现不允许班次 {base}", employee.name, schedule.dates[idx])
                )
    return warnings


def _check_newbie_high(schedule: Schedule) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        if not employee.is_newbie:
            continue
        for idx in schedule.active_indexes:
            cell = employee.schedule[idx]
            if not cell.is_locked and cell.base_shift in HIGH_SHIFTS:
                warnings.append(
                    Warning("07", "ERROR", f"新人新安排高强班次 {cell.base_shift}", employee.name, schedule.dates[idx])
                )
    return warnings


def _check_high_streaks(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        for start, end, streak in _streaks(employee, HIGH_LIMIT_SHIFTS):
            if streak > config.max_high_consecutive:
                severity = _streak_severity(employee, start, end)
                warnings.append(
                    Warning("08", severity, f"D/D1/Z/Z1 连续 {streak} 天，超过 {config.max_high_consecutive} 天", employee.name, schedule.dates[end])
                )
    return warnings


def _check_sandwich(schedule: Schedule) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        for idx in schedule.active_indexes:
            if idx <= 0 or idx >= len(employee.schedule) - 1:
                continue
            prev_base = employee.schedule[idx - 1].base_shift
            base = employee.schedule[idx].base_shift
            next_base = employee.schedule[idx + 1].base_shift
            if prev_base in HIGH_LIMIT_SHIFTS and base in A_CLASS_SHIFTS and next_base in HIGH_LIMIT_SHIFTS:
                warnings.append(
                    Warning("09", "ERROR", "存在高强-A类-高强夹心组合", employee.name, schedule.dates[idx])
                )
    return warnings


def _check_balance(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    high_counts = [_count_any(employee, HIGH_BALANCE_SHIFTS, schedule) for employee in schedule.employees]
    secondary_counts = [_count_any(employee, SECONDARY_BALANCE_SHIFTS, schedule) for employee in schedule.employees]
    if high_counts and max(high_counts) - min(high_counts) > config.balance_threshold:
        warnings.append(
            Warning("10", "WARN", f"高强均衡组 max-min={max(high_counts) - min(high_counts)}，超过 {config.balance_threshold}")
        )
    if secondary_counts and max(secondary_counts) - min(secondary_counts) > config.balance_threshold:
        warnings.append(
            Warning("10", "WARN", f"次高强均衡组 max-min={max(secondary_counts) - min(secondary_counts)}，超过 {config.balance_threshold}")
        )
    return warnings


def _check_conversion(schedule: Schedule) -> list[Warning]:
    off_to_a3 = sum(d.off_to_a3 for d in schedule.adjusted_demands)
    a3_to_off = sum(d.a3_to_off for d in schedule.adjusted_demands)
    if off_to_a3 or a3_to_off:
        return [
            Warning("11", "INFO", f"OFF转A3 {off_to_a3:.2f}，A3转OFF {a3_to_off:.2f}")
        ]
    return []


def _check_employee_rest_excess(schedule: Schedule, config: SchedulerConfig) -> list[Warning]:
    warnings = []
    for employee in schedule.employees:
        rest_count = sum(
            1
            for idx in schedule.active_indexes
            if employee.schedule[idx].base_shift == REST_SHIFT
        )
        if rest_count > config.preset_rest_days:
            warnings.append(
                Warning(
                    "15",
                    "WARN",
                    f"休息天数 {rest_count}，超过设定 {config.preset_rest_days} 天",
                    employee.name,
                )
            )
        elif rest_count < config.preset_rest_days:
            warnings.append(
                Warning(
                    "15",
                    "WARN",
                    f"休息天数 {rest_count}，少于设定 {config.preset_rest_days} 天",
                    employee.name,
                )
            )
    return warnings


def _append_summary(warnings: list[Warning]) -> None:
    errors = sum(1 for warning in warnings if warning.severity == "ERROR")
    warns = sum(1 for warning in warnings if warning.severity == "WARN")
    warnings.append(Warning("12", "INFO", f"验证完成：ERROR {errors} 个，WARN {warns} 个"))


def _actual_by_shift(schedule: Schedule, day_index: int) -> dict[str, float]:
    totals = defaultdict(float)
    for employee in schedule.employees:
        base = employee.schedule[day_index].base_shift
        if base:
            totals[base] += employee.coefficient
    return dict(totals)


def _streaks(employee: Employee, shifts: set[str]) -> list[tuple[int, int, int]]:
    result = []
    start = None
    streak = 0
    for idx, cell in enumerate(employee.schedule):
        if cell.base_shift in shifts:
            if start is None:
                start = idx
            streak += 1
        else:
            if streak:
                result.append((start or 0, idx - 1, streak))
            streak = 0
            start = None
    if streak:
        result.append((start or 0, len(employee.schedule) - 1, streak))
    return result


def _streak_severity(employee: Employee, start: int, end: int) -> str:
    for idx in range(start, end + 1):
        cell = employee.schedule[idx]
        if not cell.is_locked and not cell.is_historical:
            return "ERROR"
    return "WARN"


def _multi_block_severity(
    employee: Employee, first_block: tuple[int, int], second_block: tuple[int, int]
) -> str:
    for start, end in (first_block, second_block):
        for idx in range(start, end + 1):
            cell = employee.schedule[idx]
            if not cell.is_locked and not cell.is_historical:
                return "ERROR"
    return "WARN"


def _rest_blocks(employee: Employee) -> list[tuple[int, int]]:
    blocks = []
    start = None
    for idx, cell in enumerate(employee.schedule):
        if cell.base_shift == REST_SHIFT:
            if start is None:
                start = idx
        elif start is not None:
            blocks.append((start, idx - 1))
            start = None
    if start is not None:
        blocks.append((start, len(employee.schedule) - 1))
    return blocks


def _count_any(employee: Employee, shifts: set[str], schedule: Schedule) -> int:
    return sum(1 for idx in schedule.active_indexes if employee.schedule[idx].base_shift in shifts)
