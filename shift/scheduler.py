from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from models import AdjustedDemand, Employee, Schedule, Warning
from utils import (
    A_CLASS_SHIFTS,
    COMFORT_SHIFTS,
    HIGH_BALANCE_SHIFTS,
    HIGH_LIMIT_SHIFTS,
    HIGH_SHIFTS,
    REST_SHIFT,
    SECONDARY_BALANCE_SHIFTS,
    SHIFT_ORDER,
    WORK_SHIFTS,
    almost_met,
    date_label,
)


@dataclass(frozen=True)
class SchedulerConfig:
    preset_rest_days: int = 6
    max_consecutive_work_normal: int = 6
    max_consecutive_work_phase3: int = 5
    max_consecutive_rest: int = 2
    min_work_days_between_rest_blocks: int = 3
    max_high_consecutive: int = 2
    balance_threshold: int = 2
    demand_tolerance: float = 0.35


def run_scheduler(schedule: Schedule, config: SchedulerConfig) -> Schedule:
    precompute(schedule, config)
    arrange_rests(schedule, config)
    optimize_double_rests(schedule, config)
    arrange_shifts(schedule, config)
    repair_generated_streaks(schedule, config)
    redistribute_rest_excess(schedule, config)
    repair_rest_excess(schedule, config)
    return schedule


def precompute(schedule: Schedule, config: SchedulerConfig) -> None:
    adjusted = [
        AdjustedDemand(date=d.date, original=dict(d.demand), adjusted=dict(d.demand))
        for d in schedule.demands
    ]
    schedule.adjusted_demands = adjusted

    indexes = list(schedule.active_indexes)
    if not indexes:
        return

    off_total = sum(adjusted[i].get(REST_SHIFT) for i in indexes)
    target_total = len(schedule.employees) * config.preset_rest_days
    delta = off_total - target_total
    if abs(delta) < 0.01:
        return

    if delta > 0:
        converted = _convert_evenly(adjusted, indexes, source=REST_SHIFT, dest="A3", amount=delta)
        if converted + 0.01 < delta:
            schedule.warnings.append(
                Warning("11", "WARN", f"OFF 需求超出 {delta:.2f}，仅能转为 A3 {converted:.2f}")
            )
    else:
        needed = abs(delta)
        converted = _convert_evenly(adjusted, indexes, source="A3", dest=REST_SHIFT, amount=needed)
        if converted + 0.01 < needed:
            schedule.warnings.append(
                Warning("11", "WARN", f"OFF 需求不足 {needed:.2f}，仅能由 A3 转入 {converted:.2f}")
            )


def arrange_rests(schedule: Schedule, config: SchedulerConfig) -> None:
    for day_index in schedule.active_indexes:
        forced = [
            employee
            for employee in schedule.employees
            if _work_streak_before(employee, day_index) >= _max_work_days(employee, config)
            and _can_assign_rest(schedule, employee, day_index, config)
        ]
        for employee in sorted(forced, key=lambda e: _rest_key(schedule, e, day_index, config)):
            _assign(schedule, employee, day_index, REST_SHIFT)

        target = schedule.adjusted_demands[day_index].get(REST_SHIFT)
        while not almost_met(_actual(schedule, day_index, REST_SHIFT), target, config.demand_tolerance):
            candidates = [
                employee
                for employee in schedule.employees
                if _can_assign_rest(schedule, employee, day_index, config)
            ]
            if not candidates:
                schedule.warnings.append(
                    Warning(
                        "03",
                        "WARN",
                        f"{date_label(schedule.dates[day_index])} OFF 无可用候选人，需求 {target:.2f}，实际 {_actual(schedule, day_index, REST_SHIFT):.2f}",
                        date=schedule.dates[day_index],
                    )
                )
                break
            employee = min(candidates, key=lambda e: _rest_key(schedule, e, day_index, config))
            _assign(schedule, employee, day_index, REST_SHIFT)

            next_index = day_index + 1
            if (
                _work_streak_before(employee, day_index) >= _max_work_days(employee, config)
                and next_index in schedule.active_indexes
                and _actual(schedule, next_index, REST_SHIFT)
                < schedule.adjusted_demands[next_index].get(REST_SHIFT)
                and _can_assign_rest(schedule, employee, next_index, config)
            ):
                _assign(schedule, employee, next_index, REST_SHIFT)


def optimize_double_rests(schedule: Schedule, config: SchedulerConfig) -> None:
    changed = True
    while changed:
        changed = False
        for day_index in schedule.active_indexes:
            next_index = day_index + 1
            if next_index >= len(schedule.dates):
                continue
            for employee in schedule.employees:
                if not _is_rest(employee, day_index) or _is_rest(employee, next_index):
                    continue
                if not _can_assign_rest(schedule, employee, next_index, config):
                    continue
                donor = _find_rest_donor(schedule, employee, next_index, config)
                if donor is None:
                    continue
                donor.schedule[next_index].value = None
                _assign(schedule, employee, next_index, REST_SHIFT)
                changed = True
                break
            if changed:
                break


def arrange_shifts(schedule: Schedule, config: SchedulerConfig) -> None:
    for day_index in schedule.active_indexes:
        for shift in SHIFT_ORDER:
            target = schedule.adjusted_demands[day_index].get(shift)
            while not almost_met(_actual(schedule, day_index, shift), target, config.demand_tolerance):
                candidates = [
                    employee
                    for employee in schedule.employees
                    if _can_assign_shift(schedule, employee, day_index, shift, config)
                ]
                if not candidates:
                    schedule.warnings.append(
                        Warning(
                            "03",
                            "WARN",
                            f"{date_label(schedule.dates[day_index])} {shift} 无可用候选人，需求 {target:.2f}，实际 {_actual(schedule, day_index, shift):.2f}",
                            date=schedule.dates[day_index],
                        )
                    )
                    break
                employee = min(candidates, key=lambda e: _shift_key(schedule, e, day_index, shift))
                _assign(schedule, employee, day_index, shift)

        for employee in schedule.employees:
            if employee.schedule[day_index].is_blank:
                shift = _fallback_shift(schedule, employee, day_index, config)
                _assign(schedule, employee, day_index, shift)


def repair_generated_streaks(schedule: Schedule, config: SchedulerConfig) -> None:
    for employee in schedule.employees:
        changed = True
        attempts = 0
        while changed and attempts < len(employee.schedule) * 2:
            attempts += 1
            changed = False
            for start, end, length in _group_streaks(employee, WORK_SHIFTS):
                if length <= _max_work_days(employee, config):
                    continue
                for idx in range(end, start - 1, -1):
                    cell = employee.schedule[idx]
                    if cell.is_locked or cell.is_historical or cell.base_shift not in WORK_SHIFTS:
                        continue
                    if _move_rest_to_day(schedule, employee, idx, config):
                        changed = True
                        break
                if changed:
                    break


def redistribute_rest_excess(schedule: Schedule, config: SchedulerConfig) -> None:
    changed = True
    attempts = 0
    max_attempts = len(schedule.employees) * max(1, len(schedule.dates))
    while changed and attempts < max_attempts:
        attempts += 1
        changed = False
        over_days = _off_over_days(schedule, config)
        if not over_days:
            return

        for over_idx in over_days:
            target = schedule.adjusted_demands[over_idx].get(REST_SHIFT)
            over_excess = _actual(schedule, over_idx, REST_SHIFT) - target
            if over_excess <= config.demand_tolerance:
                continue
            for employee in _generated_rest_employees(schedule, over_idx):
                receiver_days = _rest_receiver_days(schedule, over_idx, employee.coefficient)
                for under_idx in receiver_days:
                    if _try_swap_rest_to_under_day(schedule, employee, over_idx, under_idx, over_excess, config):
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break


def repair_rest_excess(schedule: Schedule, config: SchedulerConfig) -> None:
    for day_index in schedule.active_indexes:
        changed = True
        while changed:
            changed = False
            target = schedule.adjusted_demands[day_index].get(REST_SHIFT)
            actual = _actual(schedule, day_index, REST_SHIFT)
            if actual <= target + config.demand_tolerance:
                break

            candidates = [
                employee
                for employee in schedule.employees
                if _can_convert_rest_to_work(schedule, employee, day_index, target, config)
            ]
            if not candidates:
                break

            best: tuple[float, str, str, Employee] | None = None
            for employee in candidates:
                shift = _best_underfilled_shift(schedule, employee, day_index, config)
                if not shift:
                    continue
                gap = schedule.adjusted_demands[day_index].get(shift) - _actual(schedule, day_index, shift)
                option = (gap, shift, employee.name, employee)
                if best is None or option > best:
                    best = option

            if best is None:
                break
            _, shift, _, employee = best
            _assign(schedule, employee, day_index, shift)
            changed = True


def _off_over_days(schedule: Schedule, config: SchedulerConfig) -> list[int]:
    return sorted(
        (
            idx
            for idx in schedule.active_indexes
            if _actual(schedule, idx, REST_SHIFT)
            > schedule.adjusted_demands[idx].get(REST_SHIFT) + config.demand_tolerance
        ),
        key=lambda idx: _actual(schedule, idx, REST_SHIFT)
        - schedule.adjusted_demands[idx].get(REST_SHIFT),
        reverse=True,
    )


def _rest_receiver_days(schedule: Schedule, over_idx: int, coefficient: float) -> list[int]:
    over_excess = _actual(schedule, over_idx, REST_SHIFT) - schedule.adjusted_demands[over_idx].get(REST_SHIFT)
    return sorted(
        (
            idx
            for idx in schedule.active_indexes
            if idx != over_idx
            and _actual(schedule, idx, REST_SHIFT)
            - schedule.adjusted_demands[idx].get(REST_SHIFT)
            + coefficient
            < over_excess - 0.01
        ),
        key=lambda idx: _actual(schedule, idx, REST_SHIFT)
        - schedule.adjusted_demands[idx].get(REST_SHIFT),
    )


def _generated_rest_employees(schedule: Schedule, day_index: int) -> list[Employee]:
    return sorted(
        (
            employee
            for employee in schedule.employees
            if employee.schedule[day_index].base_shift == REST_SHIFT
            and not employee.schedule[day_index].is_locked
            and not employee.schedule[day_index].is_historical
        ),
        key=lambda employee: (employee.coefficient, employee.group, employee.name),
    )


def _try_swap_rest_to_under_day(
    schedule: Schedule,
    employee: Employee,
    over_idx: int,
    under_idx: int,
    over_excess: float,
    config: SchedulerConfig,
) -> bool:
    over_cell = employee.schedule[over_idx]
    under_cell = employee.schedule[under_idx]
    under_shift = under_cell.base_shift
    if under_cell.is_locked or under_cell.is_historical or under_shift not in WORK_SHIFTS:
        return False
    if schedule.adjusted_demands[over_idx].get(under_shift) - _actual(schedule, over_idx, under_shift) <= config.demand_tolerance:
        return False

    over_target = schedule.adjusted_demands[over_idx].get(REST_SHIFT)
    under_target = schedule.adjusted_demands[under_idx].get(REST_SHIFT)
    if _actual(schedule, over_idx, REST_SHIFT) - employee.coefficient < over_target - config.demand_tolerance:
        return False
    if _actual(schedule, under_idx, REST_SHIFT) + employee.coefficient - under_target >= over_excess - 0.01:
        return False

    old_over = over_cell.value
    old_under = under_cell.value
    over_cell.value = under_shift
    under_cell.value = None
    try:
        if not _can_assign_rest(schedule, employee, under_idx, config):
            return False
        under_cell.value = REST_SHIFT
        over_cell.value = None
        if not _can_assign_shift(schedule, employee, over_idx, under_shift, config):
            return False
        over_cell.value = old_under
        return True
    finally:
        if not (over_cell.value == old_under and under_cell.value == REST_SHIFT):
            over_cell.value = old_over
            under_cell.value = old_under


def _convert_evenly(
    adjusted: list[AdjustedDemand],
    indexes: list[int],
    source: str,
    dest: str,
    amount: float,
) -> float:
    remaining = amount
    converted = 0.0
    for pos, idx in enumerate(indexes):
        days_left = len(indexes) - pos
        wanted = remaining / days_left
        available = max(0.0, adjusted[idx].adjusted.get(source, 0.0))
        change = min(available, wanted)
        adjusted[idx].adjusted[source] = adjusted[idx].adjusted.get(source, 0.0) - change
        adjusted[idx].adjusted[dest] = adjusted[idx].adjusted.get(dest, 0.0) + change
        if source == REST_SHIFT and dest == "A3":
            adjusted[idx].off_to_a3 += change
        elif source == "A3" and dest == REST_SHIFT:
            adjusted[idx].a3_to_off += change
        remaining -= change
        converted += change
        if remaining <= 0.01:
            break
    return converted


def _actual(schedule: Schedule, day_index: int, shift: str) -> float:
    return sum(
        employee.coefficient
        for employee in schedule.employees
        if employee.schedule[day_index].base_shift == shift
    )


def _assign(schedule: Schedule, employee: Employee, day_index: int, shift: str) -> None:
    employee.schedule[day_index].set_value(shift)


def _can_assign_rest(
    schedule: Schedule, employee: Employee, day_index: int, config: SchedulerConfig
) -> bool:
    cell = employee.schedule[day_index]
    if cell.is_locked or cell.is_historical or not cell.is_blank:
        return False
    if _consecutive_count(employee, day_index, REST_SHIFT) > config.max_consecutive_rest:
        return False
    return _rest_block_spacing_ok(employee, day_index, config.min_work_days_between_rest_blocks)


def _can_assign_shift(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    shift: str,
    config: SchedulerConfig,
) -> bool:
    cell = employee.schedule[day_index]
    if cell.is_locked or cell.is_historical or not cell.is_blank:
        return False
    if employee.is_phase3 and shift not in COMFORT_SHIFTS:
        return False
    if employee.is_newbie and shift in HIGH_SHIFTS:
        return False
    if _consecutive_work_count(employee, day_index, shift) > _max_work_days(employee, config):
        return False
    if shift in HIGH_LIMIT_SHIFTS:
        if _high_limited_count(employee, day_index) > config.max_high_consecutive:
            return False
        if _is_rest(employee, day_index + 1):
            return False
        if _is_a_class(employee, day_index - 1) and _is_high_limited(employee, day_index - 2):
            return False
        if _is_a_class(employee, day_index + 1) and _is_high_limited(employee, day_index + 2):
            return False
    if shift in A_CLASS_SHIFTS:
        if _is_high_limited(employee, day_index - 1) and _is_high_limited(employee, day_index + 1):
            return False
    return True


def _fallback_shift(
    schedule: Schedule, employee: Employee, day_index: int, config: SchedulerConfig
) -> str:
    allowed = ("A2", "A3") if employee.is_phase3 else SHIFT_ORDER
    gaps = []
    for shift in allowed:
        if _can_assign_shift(schedule, employee, day_index, shift, config):
            target = schedule.adjusted_demands[day_index].get(shift)
            gaps.append((target - _actual(schedule, day_index, shift), shift))
    if gaps:
        return max(gaps)[1]
    if _can_assign_rest(schedule, employee, day_index, config):
        return REST_SHIFT
    for shift in ("A3", "A2", "A4", "A1", "Z1", "Z", "D1", "D"):
        if shift in allowed:
            return shift
    return "A3"


def _rest_block_spacing_ok(employee: Employee, day_index: int, min_work_days: int) -> bool:
    blocks = _rest_blocks(employee, assume_rest_index=day_index)
    assumed_block_index = None
    for idx, (start, end) in enumerate(blocks):
        if start <= day_index <= end:
            assumed_block_index = idx
            break
    if assumed_block_index is None:
        return True

    if assumed_block_index > 0:
        prev_end = blocks[assumed_block_index - 1][1]
        if blocks[assumed_block_index][0] - prev_end - 1 < min_work_days:
            return False
    if assumed_block_index + 1 < len(blocks):
        next_start = blocks[assumed_block_index + 1][0]
        if next_start - blocks[assumed_block_index][1] - 1 < min_work_days:
            return False
    return True


def _move_rest_to_day(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    config: SchedulerConfig,
) -> bool:
    cell = employee.schedule[day_index]
    old_value = cell.value
    cell.value = REST_SHIFT
    if (
        _rest_streak_at(employee, day_index) <= config.max_consecutive_rest
        and _rest_block_spacing_ok(employee, day_index, config.min_work_days_between_rest_blocks)
    ):
        return True

    start, end = _streak_bounds(employee, day_index, {REST_SHIFT})
    for rest_idx in sorted(
        (idx for idx in range(start, end + 1) if idx != day_index),
        key=lambda idx: abs(idx - day_index),
        reverse=True,
    ):
        rest_cell = employee.schedule[rest_idx]
        if rest_cell.is_locked or rest_cell.is_historical or rest_cell.base_shift != REST_SHIFT:
            continue
        rest_old = rest_cell.value
        for shift in ("A3", "A2", "A4", "A1"):
            if employee.is_phase3 and shift not in COMFORT_SHIFTS:
                continue
            rest_cell.value = None
            if _can_assign_shift(schedule, employee, rest_idx, shift, config):
                rest_cell.value = shift
                if (
                    _rest_streak_at(employee, day_index) <= config.max_consecutive_rest
                    and _rest_block_spacing_ok(employee, day_index, config.min_work_days_between_rest_blocks)
                ):
                    return True
            rest_cell.value = rest_old

    cell.value = old_value
    return False


def _can_convert_rest_to_work(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    target: float,
    config: SchedulerConfig,
) -> bool:
    cell = employee.schedule[day_index]
    if cell.is_locked or cell.is_historical or cell.base_shift != REST_SHIFT:
        return False
    if _actual(schedule, day_index, REST_SHIFT) - employee.coefficient < target - config.demand_tolerance:
        return False
    return bool(_best_underfilled_shift(schedule, employee, day_index, config))


def _best_underfilled_shift(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    config: SchedulerConfig,
) -> str | None:
    cell = employee.schedule[day_index]
    old_value = cell.value
    cell.value = None
    try:
        options = []
        for shift in SHIFT_ORDER:
            gap = schedule.adjusted_demands[day_index].get(shift) - _actual(schedule, day_index, shift)
            if gap <= config.demand_tolerance:
                continue
            if _can_assign_shift(schedule, employee, day_index, shift, config):
                options.append((gap, -_shift_priority(shift), shift))
        if not options:
            return None
        return max(options)[2]
    finally:
        cell.value = old_value


def _shift_priority(shift: str) -> int:
    return SHIFT_ORDER.index(shift)


def _rest_key(
    schedule: Schedule, employee: Employee, day_index: int, config: SchedulerConfig
) -> tuple:
    work_streak = _work_streak_before(employee, day_index)
    next_rest = _is_rest(employee, day_index + 1)
    after_next_rest = _is_rest(employee, day_index + 2)
    prev_rest = _is_rest(employee, day_index - 1)
    prev_prev_rest = _is_rest(employee, day_index - 2)
    max_work = _max_work_days(employee, config)

    if work_streak >= max_work:
        priority = 0
    elif work_streak >= 4:
        priority = 1
    elif prev_rest and not prev_prev_rest:
        priority = 2
    elif next_rest:
        priority = 3
    elif after_next_rest:
        priority = 4
    elif work_streak >= 3:
        priority = 5
    else:
        priority = 6

    return (
        priority,
        _active_count(employee, REST_SHIFT, schedule),
        -work_streak,
        employee.group,
        employee.name,
    )


def _shift_key(schedule: Schedule, employee: Employee, day_index: int, shift: str) -> tuple:
    if shift in HIGH_BALANCE_SHIFTS:
        balance = _active_count_any(employee, HIGH_BALANCE_SHIFTS, schedule)
    elif shift in SECONDARY_BALANCE_SHIFTS:
        balance = _active_count_any(employee, SECONDARY_BALANCE_SHIFTS, schedule)
    else:
        balance = 0
    return (
        balance,
        _active_count(employee, shift, schedule),
        _active_count_any(employee, HIGH_LIMIT_SHIFTS, schedule),
        _work_streak_before(employee, day_index),
        employee.group,
        employee.name,
    )


def _find_rest_donor(
    schedule: Schedule,
    receiver: Employee,
    day_index: int,
    config: SchedulerConfig,
) -> Employee | None:
    for employee in sorted(schedule.employees, key=lambda e: _active_count(e, REST_SHIFT, schedule), reverse=True):
        if employee is receiver:
            continue
        cell = employee.schedule[day_index]
        if cell.is_locked or cell.is_historical or cell.base_shift != REST_SHIFT:
            continue
        if abs(employee.coefficient - receiver.coefficient) > 0.01:
            continue
        if _is_rest(employee, day_index - 1) or _is_rest(employee, day_index + 1):
            continue
        if _work_count_if_day_becomes_work(employee, day_index) > _max_work_days(employee, config):
            continue
        return employee
    return None


def _max_work_days(employee: Employee, config: SchedulerConfig) -> int:
    if employee.is_phase3:
        return config.max_consecutive_work_phase3
    return config.max_consecutive_work_normal


def _active_count(employee: Employee, shift: str, schedule: Schedule) -> int:
    return sum(1 for idx in schedule.active_indexes if employee.schedule[idx].base_shift == shift)


def _active_count_any(employee: Employee, shifts: Iterable[str], schedule: Schedule) -> int:
    shift_set = set(shifts)
    return sum(1 for idx in schedule.active_indexes if employee.schedule[idx].base_shift in shift_set)


def _work_streak_before(employee: Employee, day_index: int) -> int:
    streak = 0
    for idx in range(day_index - 1, -1, -1):
        if employee.schedule[idx].base_shift in WORK_SHIFTS:
            streak += 1
        else:
            break
    return streak


def _consecutive_count(employee: Employee, day_index: int, shift: str) -> int:
    return 1 + _same_count(employee, day_index - 1, -1, shift) + _same_count(employee, day_index + 1, 1, shift)


def _consecutive_work_count(employee: Employee, day_index: int, shift: str) -> int:
    if shift not in WORK_SHIFTS:
        return 0
    count = 1
    for idx in range(day_index - 1, -1, -1):
        if employee.schedule[idx].base_shift in WORK_SHIFTS:
            count += 1
        else:
            break
    for idx in range(day_index + 1, len(employee.schedule)):
        if employee.schedule[idx].base_shift in WORK_SHIFTS:
            count += 1
        else:
            break
    return count


def _work_count_if_day_becomes_work(employee: Employee, day_index: int) -> int:
    count = 1
    for idx in range(day_index - 1, -1, -1):
        if employee.schedule[idx].base_shift in WORK_SHIFTS:
            count += 1
        else:
            break
    for idx in range(day_index + 1, len(employee.schedule)):
        if employee.schedule[idx].base_shift in WORK_SHIFTS:
            count += 1
        else:
            break
    return count


def _group_streaks(employee: Employee, shifts: set[str]) -> list[tuple[int, int, int]]:
    result = []
    start = None
    for idx, cell in enumerate(employee.schedule):
        if cell.base_shift in shifts:
            if start is None:
                start = idx
        elif start is not None:
            result.append((start, idx - 1, idx - start))
            start = None
    if start is not None:
        result.append((start, len(employee.schedule) - 1, len(employee.schedule) - start))
    return result


def _rest_blocks(employee: Employee, assume_rest_index: int | None = None) -> list[tuple[int, int]]:
    blocks = []
    start = None
    for idx, cell in enumerate(employee.schedule):
        is_rest = cell.base_shift == REST_SHIFT or idx == assume_rest_index
        if is_rest:
            if start is None:
                start = idx
        elif start is not None:
            blocks.append((start, idx - 1))
            start = None
    if start is not None:
        blocks.append((start, len(employee.schedule) - 1))
    return blocks


def _rest_streak_at(employee: Employee, day_index: int) -> int:
    start, end = _streak_bounds(employee, day_index, {REST_SHIFT})
    return end - start + 1


def _streak_bounds(employee: Employee, day_index: int, shifts: set[str]) -> tuple[int, int]:
    start = day_index
    while start > 0 and employee.schedule[start - 1].base_shift in shifts:
        start -= 1
    end = day_index
    while end + 1 < len(employee.schedule) and employee.schedule[end + 1].base_shift in shifts:
        end += 1
    return start, end


def _high_limited_count(employee: Employee, day_index: int) -> int:
    return 1 + _same_group_count(employee, day_index - 1, -1, HIGH_LIMIT_SHIFTS) + _same_group_count(employee, day_index + 1, 1, HIGH_LIMIT_SHIFTS)


def _same_count(employee: Employee, start: int, step: int, shift: str) -> int:
    count = 0
    idx = start
    while 0 <= idx < len(employee.schedule) and employee.schedule[idx].base_shift == shift:
        count += 1
        idx += step
    return count


def _same_group_count(employee: Employee, start: int, step: int, shifts: set[str]) -> int:
    count = 0
    idx = start
    while 0 <= idx < len(employee.schedule) and employee.schedule[idx].base_shift in shifts:
        count += 1
        idx += step
    return count


def _is_rest(employee: Employee, day_index: int) -> bool:
    return 0 <= day_index < len(employee.schedule) and employee.schedule[day_index].base_shift == REST_SHIFT


def _is_high_limited(employee: Employee, day_index: int) -> bool:
    return 0 <= day_index < len(employee.schedule) and employee.schedule[day_index].base_shift in HIGH_LIMIT_SHIFTS


def _is_a_class(employee: Employee, day_index: int) -> bool:
    return 0 <= day_index < len(employee.schedule) and employee.schedule[day_index].base_shift in A_CLASS_SHIFTS
