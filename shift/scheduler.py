from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from models import AdjustedDemand, Employee, Schedule, Warning
from utils import (
    A_CLASS_SHIFTS,
    BALANCE_GROUPS,
    COMFORT_SHIFTS,
    D_FAMILY,
    HIGH_LIMIT_SHIFTS,
    HIGH_SHIFTS,
    REST_SHIFT,
    SHIFT_ORDER,
    WORK_SHIFTS,
    Z_FAMILY,
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
    z_min_consecutive: int = 2
    z_max_consecutive: int = 3


def run_scheduler(schedule: Schedule, config: SchedulerConfig) -> Schedule:
    precompute(schedule, config)
    arrange_rests(schedule, config)
    optimize_double_rests(schedule, config)
    arrange_shifts(schedule, config)
    repair_generated_streaks(schedule, config)
    redistribute_rest_excess(schedule, config)
    repair_rest_excess(schedule, config)
    repair_employee_rest_count(schedule, config)
    shape_z_runs(schedule, config)
    cluster_high_pairs(schedule, config)
    redistribute_balance(schedule, config)
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
    all_shifts = set(SHIFT_ORDER) | {REST_SHIFT}
    actuals: dict[tuple[int, str], float] = {}
    for idx in schedule.active_indexes:
        for shift in all_shifts:
            actuals[(idx, shift)] = _actual(schedule, idx, shift)

    changed = True
    attempts = 0
    max_attempts = len(schedule.employees) * max(1, len(schedule.dates))
    while changed and attempts < max_attempts:
        changed = False
        over_days = sorted(
            (
                idx
                for idx in schedule.active_indexes
                if actuals[(idx, REST_SHIFT)] > schedule.adjusted_demands[idx].get(REST_SHIFT) + config.demand_tolerance
            ),
            key=lambda idx: actuals[(idx, REST_SHIFT)] - schedule.adjusted_demands[idx].get(REST_SHIFT),
            reverse=True,
        )
        if not over_days:
            return

        for over_idx in over_days:
            target = schedule.adjusted_demands[over_idx].get(REST_SHIFT)
            while attempts < max_attempts:
                over_excess = actuals[(over_idx, REST_SHIFT)] - target
                if over_excess <= config.demand_tolerance:
                    break
                swapped = False
                for employee in _generated_rest_employees(schedule, over_idx):
                    receiver_days = sorted(
                        (
                            idx
                            for idx in schedule.active_indexes
                            if idx != over_idx
                            and actuals[(idx, REST_SHIFT)] - schedule.adjusted_demands[idx].get(REST_SHIFT) + employee.coefficient < over_excess - 0.01
                        ),
                        key=lambda idx: actuals[(idx, REST_SHIFT)] - schedule.adjusted_demands[idx].get(REST_SHIFT),
                    )
                    for under_idx in receiver_days:
                        under_cell = employee.schedule[under_idx]
                        under_shift = under_cell.base_shift
                        if under_cell.is_locked or under_cell.is_historical or under_shift not in WORK_SHIFTS:
                            continue
                        if schedule.adjusted_demands[over_idx].get(under_shift) - actuals[(over_idx, under_shift)] <= config.demand_tolerance:
                            continue
                        over_target = schedule.adjusted_demands[over_idx].get(REST_SHIFT)
                        under_target = schedule.adjusted_demands[under_idx].get(REST_SHIFT)
                        if actuals[(over_idx, REST_SHIFT)] - employee.coefficient < over_target - config.demand_tolerance:
                            continue
                        if actuals[(under_idx, REST_SHIFT)] + employee.coefficient - under_target >= over_excess - 0.01:
                            continue

                        over_cell = employee.schedule[over_idx]
                        old_over = over_cell.value
                        old_under = under_cell.value
                        over_cell.value = under_shift
                        under_cell.value = None
                        swap_ok = False
                        try:
                            if _can_assign_rest(schedule, employee, under_idx, config):
                                under_cell.value = REST_SHIFT
                                over_cell.value = None
                                if _can_assign_shift(schedule, employee, over_idx, under_shift, config):
                                    over_cell.value = old_under
                                    if _neighbors_valid(schedule, employee, over_idx, config) and _neighbors_valid(
                                        schedule, employee, under_idx, config
                                    ):
                                        swap_ok = True
                        finally:
                            if not swap_ok:
                                over_cell.value = old_over
                                under_cell.value = old_under
                        if swap_ok:
                            actuals[(over_idx, REST_SHIFT)] -= employee.coefficient
                            actuals[(over_idx, under_shift)] += employee.coefficient
                            actuals[(under_idx, under_shift)] -= employee.coefficient
                            actuals[(under_idx, REST_SHIFT)] += employee.coefficient
                            changed = True
                            swapped = True
                            attempts += 1
                            break
                    if swapped:
                        break
                if not swapped:
                    break


def repair_rest_excess(schedule: Schedule, config: SchedulerConfig) -> None:
    all_shifts = set(SHIFT_ORDER) | {REST_SHIFT}
    for day_index in schedule.active_indexes:
        day_actuals = {shift: _actual(schedule, day_index, shift) for shift in all_shifts}
        changed = True
        while changed:
            changed = False
            target = schedule.adjusted_demands[day_index].get(REST_SHIFT)
            if day_actuals[REST_SHIFT] <= target + config.demand_tolerance:
                break

            candidates = [
                employee
                for employee in schedule.employees
                if _can_convert_rest_to_work(schedule, employee, day_index, target, config, day_actuals)
            ]
            if not candidates:
                break

            best: tuple[float, str, str, Employee] | None = None
            for employee in candidates:
                shift = _best_underfilled_shift(schedule, employee, day_index, config, day_actuals)
                if not shift:
                    continue
                gap = schedule.adjusted_demands[day_index].get(shift) - day_actuals[shift]
                option = (gap, shift, employee.name, employee)
                if best is None or option > best:
                    best = option

            if best is None:
                break
            _, shift, _, employee = best
            _assign(schedule, employee, day_index, shift)
            if not _neighbors_valid(schedule, employee, day_index, config):
                # 邻格规则被破坏（如 D 后次日变 OFF）：回退本格，不计入 changed
                _assign(schedule, employee, day_index, REST_SHIFT)
                continue
            day_actuals[REST_SHIFT] -= employee.coefficient
            day_actuals[shift] += employee.coefficient
            changed = True


def repair_employee_rest_count(schedule: Schedule, config: SchedulerConfig) -> None:
    for employee in schedule.employees:
        rest_count = _active_count(employee, REST_SHIFT, schedule)
        target = config.preset_rest_days

        if rest_count > target:
            excess = rest_count - target
            candidates = [
                idx
                for idx in schedule.active_indexes
                if employee.schedule[idx].base_shift == REST_SHIFT
                and not employee.schedule[idx].is_locked
                and not employee.schedule[idx].is_historical
            ]
            candidates.sort(
                key=lambda idx: (
                    # 优先从 OFF 超额的日子转出，保护 OFF 已短缺的日子
                    _actual(schedule, idx, REST_SHIFT)
                    - schedule.adjusted_demands[idx].get(REST_SHIFT),
                    schedule.adjusted_demands[idx].get("A3")
                    - _actual(schedule, idx, "A3"),
                ),
                reverse=True,
            )
            for idx in candidates:
                if excess <= 0:
                    break
                # 不从 OFF 已短缺(移除后跌破容差)的日子拿走休息，避免加剧当日 OFF 缺口
                if not _demand_ok_removing(schedule, idx, REST_SHIFT, employee.coefficient, config):
                    continue
                if _try_convert_off_to_shift(schedule, employee, idx, config):
                    excess -= 1
            if excess > 0:
                # 第二遍：配额优先 —— 仍超出时从「OFF 缺口最小」的短缺日开始转出，
                # 最缺的日子(如需求尖峰日)排到最后才碰，把冗余分散而非集中。
                remaining = [
                    idx
                    for idx in schedule.active_indexes
                    if employee.schedule[idx].base_shift == REST_SHIFT
                    and not employee.schedule[idx].is_locked
                    and not employee.schedule[idx].is_historical
                ]
                remaining.sort(
                    key=lambda idx: _actual(schedule, idx, REST_SHIFT)
                    - schedule.adjusted_demands[idx].get(REST_SHIFT),
                    reverse=True,
                )
                for idx in remaining:
                    if excess <= 0:
                        break
                    if _try_convert_off_to_shift(schedule, employee, idx, config):
                        excess -= 1
            if excess > 0:
                schedule.warnings.append(
                    Warning(
                        "15",
                        "WARN",
                        f"休息天数仍超出 {excess} 天，无法找到合适位置转为 A3/A2",
                        employee.name,
                    )
                )

        elif rest_count < target:
            deficit = target - rest_count
            candidates = [
                idx
                for idx in schedule.active_indexes
                if not employee.schedule[idx].is_locked
                and not employee.schedule[idx].is_historical
                and not employee.schedule[idx].is_blank
                and employee.schedule[idx].base_shift in WORK_SHIFTS
            ]
            candidates.sort(
                key=lambda idx: (
                    schedule.adjusted_demands[idx].get(REST_SHIFT)
                    - _actual(schedule, idx, REST_SHIFT),
                    _shift_priority(employee.schedule[idx].base_shift),
                ),
                reverse=True,
            )
            for idx in candidates:
                if deficit <= 0:
                    break
                if _try_convert_work_to_rest(schedule, employee, idx, config):
                    deficit -= 1
            if deficit > 0:
                schedule.warnings.append(
                    Warning(
                        "15",
                        "WARN",
                        f"休息天数仍缺少 {deficit} 天，无法找到合适位置转为 OFF",
                        employee.name,
                    )
                )


def shape_z_runs(schedule: Schedule, config: SchedulerConfig) -> None:
    """Z/Z1 块整型（软约束）：短块扩长、同工作块内合并、块尽量贴前一个 OFF。

    行内改动均受需求容差与 `_can_assign_shift` 硬约束保护。
    """
    for employee in schedule.employees:
        _extend_short_z_blocks(schedule, employee, config)
        _merge_z_blocks(schedule, employee, config)
        _pull_z_blocks_to_off(schedule, employee, config)


def _z_blocks(employee: Employee) -> list[tuple[int, int]]:
    return [(start, end) for start, end, _ in _group_streaks(employee, Z_FAMILY)]


def _extend_short_z_blocks(schedule: Schedule, employee: Employee, config: SchedulerConfig) -> None:
    for start, end in list(_z_blocks(employee)):
        if end - start + 1 >= config.z_min_consecutive:
            continue
        for day_to in (start - 1, end + 1):
            if _convert_to_z(schedule, employee, day_to, config):
                break


def _merge_z_blocks(schedule: Schedule, employee: Employee, config: SchedulerConfig) -> None:
    for wb_start, wb_end in list(_work_blocks_between_rests(employee)):
        runs = [r for r in _z_blocks(employee) if wb_start <= r[0] and r[1] <= wb_end]
        if len(runs) < 2:
            continue
        gap = list(range(runs[0][1] + 1, runs[1][0]))
        new_len = runs[1][1] - runs[0][0] + 1  # 合并后总长 = 首块起点..末块终点跨度（已含间隔日）
        if new_len > config.z_max_consecutive or not gap:
            continue
        if all(_can_place_z(schedule, employee, i, config) for i in gap):
            for i in gap:
                _convert_to_z(schedule, employee, i, config)


def _pull_z_blocks_to_off(schedule: Schedule, employee: Employee, config: SchedulerConfig) -> None:
    for wb_start, wb_end in _work_blocks_between_rests(employee):
        for z_start, _z_end in _z_blocks(employee):
            if wb_start < z_start <= wb_end:
                _swap_pair(schedule, employee, z_start - 1, z_start, config)
                break  # 每个工作块只尝试第一个 Z 块


def _can_place_z(schedule: Schedule, employee: Employee, day_index: int, config: SchedulerConfig) -> bool:
    """day_index 能否改为 Z（不动格，试空后校验）。"""
    cell = employee.schedule[day_index]
    if cell.is_locked or cell.is_historical or cell.base_shift not in WORK_SHIFTS or cell.base_shift in Z_FAMILY:
        return False
    if not _demand_ok_removing(schedule, day_index, cell.base_shift, employee.coefficient, config):
        return False
    old = cell.value
    cell.value = None
    try:
        return _can_assign_shift(schedule, employee, day_index, "Z", config)
    finally:
        cell.value = old


def _convert_to_z(schedule: Schedule, employee: Employee, day_index: int, config: SchedulerConfig) -> bool:
    if not (0 <= day_index < len(employee.schedule)):
        return False
    if not _can_place_z(schedule, employee, day_index, config):
        return False
    old = employee.schedule[day_index].value
    employee.schedule[day_index].value = "Z"
    if not _neighbors_valid(schedule, employee, day_index, config):
        # 幸存邻格被破坏（如 B 前一日变 Z）：回退
        employee.schedule[day_index].value = old
        return False
    return True


def _swap_pair(schedule: Schedule, employee: Employee, i: int, j: int, config: SchedulerConfig) -> bool:
    """行内对调 i/j 两格：双方可动、移除不破需求容差、互换后硬约束成立。"""
    ci, cj = employee.schedule[i], employee.schedule[j]
    if ci.is_locked or ci.is_historical or cj.is_locked or cj.is_historical:
        return False
    si, sj = ci.base_shift, cj.base_shift
    if si not in WORK_SHIFTS or sj not in WORK_SHIFTS or si == sj:
        return False
    if not _demand_ok_removing(schedule, i, si, employee.coefficient, config):
        return False
    if not _demand_ok_removing(schedule, j, sj, employee.coefficient, config):
        return False
    old_i, old_j = ci.value, cj.value
    ci.value, cj.value = None, None
    if _can_assign_shift(schedule, employee, i, sj, config) and _can_assign_shift(
        schedule, employee, j, si, config
    ):
        ci.value, cj.value = sj, si
        if _neighbors_valid(schedule, employee, i, config) and _neighbors_valid(
            schedule, employee, j, config
        ):
            return True
    ci.value = old_i
    cj.value = old_j
    return False


def cluster_high_pairs(schedule: Schedule, config: SchedulerConfig) -> None:
    """两次休息之间若有恰好 2 个 D 族班(D/D1)且不连续，调成连续并尽量贴前段休息。

    只用「同日交换」：每个工作日的班次多元集合不变。Z/Z1 块由 shape_z_runs 负责，不参与计数。
    """
    for employee in schedule.employees:
        for start, end in _work_blocks_between_rests(employee):
            highs = [
                idx
                for idx in range(start, end + 1)
                if employee.schedule[idx].base_shift in D_FAMILY
            ]
            if len(highs) != 2 or highs[1] == highs[0] + 1:
                continue
            h1, h2 = highs
            # 目标槽 start、start+1 需可动（非 locked/historical）
            if not _cells_free(employee, (start, start + 1)):
                continue
            # 优先级1：把高强班放到前段休息(OFF)后，即块首 start。
            # 放宽：允许同一员工行内直接移动（仅需当日相应班次仍在容差内），以真正提升贴 OFF 数。
            placed = employee.schedule[start].base_shift in D_FAMILY
            if not placed:
                placed = _move_high_in_row(schedule, employee, h2, start, config) or _move_high_in_row(
                    schedule, employee, h1, start, config
                )
            if not placed:
                continue
            # 优先级2：把另一个高强班聚到 start+1（贴 OFF 且相邻）
            for other in (
                i
                for i in range(start, end + 1)
                if i != start and employee.schedule[i].base_shift in D_FAMILY
            ):
                if other != start + 1:
                    # 也放宽为行内移动，避免跨员工 swap 带来的连带损耗
                    _move_high_in_row(schedule, employee, other, start + 1, config)
                break


def _cells_free(employee: Employee, indexes: Iterable[int]) -> bool:
    return all(
        not employee.schedule[i].is_locked and not employee.schedule[i].is_historical
        for i in indexes
    )


def _work_blocks_between_rests(employee: Employee) -> list[tuple[int, int]]:
    """两次休息之间、两端都贴休息的连续工作日块 [s, e]（s-1 与 e+1 均为 OFF）。"""
    blocks = []
    n = len(employee.schedule)
    i = 0
    while i < n:
        if employee.schedule[i].base_shift in WORK_SHIFTS:
            s = i
            while i < n and employee.schedule[i].base_shift in WORK_SHIFTS:
                i += 1
            e = i - 1
            if s > 0 and e < n - 1 and _is_rest(employee, s - 1) and _is_rest(employee, e + 1):
                blocks.append((s, e))
        else:
            i += 1
    return blocks


def _move_high_in_row(
    schedule: Schedule, employee: Employee, day_from: int, day_to: int, config: SchedulerConfig
) -> bool:
    """同一员工行内把 D 族班从 day_from 移到 day_to(OFF 后)。放宽不变式：允许小幅日计改变，但需在容差内。

    把 D 族班移到 day_to、day_to 的原工作班次挪回 day_from；仅当两个受影响班次的移除都不致跌破需求容差、且双方约束成立时接受。
    """
    if day_from == day_to:
        return False
    src = employee.schedule[day_from]
    dst = employee.schedule[day_to]
    if src.is_locked or src.is_historical or dst.is_locked or dst.is_historical:
        return False
    high = src.base_shift
    x = dst.base_shift
    if high not in D_FAMILY or x not in WORK_SHIFTS:
        return False
    if not _demand_ok_removing(schedule, day_from, high, employee.coefficient, config):
        return False
    if not _demand_ok_removing(schedule, day_to, x, employee.coefficient, config):
        return False
    old_src, old_dst = src.value, dst.value
    src.value = x
    dst.value = high
    if (
        _can_hold_shift(schedule, employee, day_from, x, config)
        and _can_hold_shift(schedule, employee, day_to, high, config)
        and _neighbors_valid(schedule, employee, day_from, config)
        and _neighbors_valid(schedule, employee, day_to, config)
    ):
        return True
    src.value, dst.value = old_src, old_dst
    return False


def _demand_ok_removing(
    schedule: Schedule, day_index: int, shift: str, coeff: float, config: SchedulerConfig
) -> bool:
    """day_index 移除一格 shift 后，该班次实际数是否仍在需求容差内。

    用 2× 标准容差：接受该高强贴 OFF 步骤造成的小幅日波动，但仍阻止无边的拉大缺口。
    """
    target = schedule.adjusted_demands[day_index].get(shift)
    actual = _actual(schedule, day_index, shift)
    return actual - coeff >= target - 2 * config.demand_tolerance


def _can_hold_shift(
    schedule: Schedule, employee: Employee, day_index: int, shift: str, config: SchedulerConfig
) -> bool:
    """校验某格在该位置能否承接 shift（先置空再查 `_can_assign_shift`，适用于重排已排班格）。"""
    cell = employee.schedule[day_index]
    value = cell.value
    cell.value = None
    try:
        return _can_assign_shift(schedule, employee, day_index, shift, config)
    finally:
        cell.value = value


def redistribute_balance(schedule: Schedule, config: SchedulerConfig) -> None:
    """均匀 D/D1、Z/Z1、A1/A4 三组班在合格(非三期)员工间的分布。

    balance_threshold 表示允许的计数差：差 > 阈值才触发再分布。用同系数「同日交换」，每日计数不变。
    """
    for group in BALANCE_GROUPS:
        counts = [_active_count_any(e, group, schedule) for e in schedule.employees]
        changes = True
        attempts = 0
        cap = len(schedule.employees) * max(1, len(schedule.active_indexes))
        while changes and attempts < cap:
            changes = False
            carriers = [i for i in range(len(schedule.employees)) if counts[i] > 0]
            if not carriers:
                break
            over = max(carriers, key=lambda i: counts[i])
            receivers = [
                i
                for i in range(len(schedule.employees))
                if not schedule.employees[i].is_phase3
            ]  # 只有非三期能承接这三组班次
            if not receivers:
                break
            under = min(receivers, key=lambda i: counts[i])
            if counts[over] - counts[under] <= config.balance_threshold:
                break
            emp_over = schedule.employees[over]
            emp_under = schedule.employees[under]
            if abs(emp_over.coefficient - emp_under.coefficient) > 0.01:
                break
            found = False
            for idx in schedule.active_indexes:
                ocell = emp_over.schedule[idx]
                ucell = emp_under.schedule[idx]
                if ocell.is_locked or ocell.is_historical:
                    continue
                if ucell.is_locked or ucell.is_historical:
                    continue
                g = ocell.base_shift
                if g not in group:
                    continue
                u = ucell.base_shift
                if u not in WORK_SHIFTS:
                    continue
                if not _can_hold_shift(schedule, emp_under, idx, g, config):
                    continue
                if not _can_hold_shift(schedule, emp_over, idx, u, config):
                    continue
                ocell.value = u
                ucell.value = g
                if not (
                    _neighbors_valid(schedule, emp_over, idx, config)
                    and _neighbors_valid(schedule, emp_under, idx, config)
                ):
                    # 任一员工的邻格规则被破坏：整体回退
                    ocell.value = g
                    ucell.value = u
                    continue
                counts[over] -= 1
                counts[under] += 1
                attempts += 1
                changes = True
                found = True
                break
            if not found:
                break


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
    if _is_high_limited(employee, day_index - 1):
        # 高强班（D/D1/Z/Z1）次日不能是 OFF：排休息时不得紧贴前一日的自家高强班
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
    return _cell_rules_ok(schedule, employee, day_index, shift, config)


def _cell_rules_ok(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    shift: str,
    config: SchedulerConfig,
) -> bool:
    """day_index 为 shift 时本格的家族/前置规则是否成立（不含空格/锁定守卫）。"""
    if employee.is_phase3 and shift not in COMFORT_SHIFTS:
        return False
    if employee.is_newbie and shift in HIGH_SHIFTS:
        return False
    if _consecutive_work_count(employee, day_index, shift) > _max_work_days(employee, config):
        return False
    if shift in D_FAMILY:
        if _d_run_count(employee, day_index) > config.max_high_consecutive:
            return False
        if _is_rest(employee, day_index + 1):
            return False
        if _is_a_class(employee, day_index - 1) and _is_d_family(employee, day_index - 2):
            return False
        if _is_a_class(employee, day_index + 1) and _is_d_family(employee, day_index + 2):
            return False
    if shift in Z_FAMILY:
        if _is_rest(employee, day_index + 1):
            return False
        if _z_run_count(employee, day_index) > config.z_max_consecutive:
            return False
        if _z_sandwich(employee, day_index):
            return False
    if shift == "B":
        prev = employee.schedule[day_index - 1].base_shift if day_index >= 1 else ""
        if prev in ("D", "D1", "Z"):
            return False
    if shift == "C":
        prev = employee.schedule[day_index - 1].base_shift if day_index >= 1 else ""
        if prev not in HIGH_LIMIT_SHIFTS:
            return False
    if shift in A_CLASS_SHIFTS:
        if _is_high_limited(employee, day_index - 1) and _is_high_limited(employee, day_index + 1):
            return False
    return True


def _neighbors_valid(schedule: Schedule, employee: Employee, day_index: int, config: SchedulerConfig) -> bool:
    """day_index 变更后，其前/后一天的幸存格仍须满足各自的家族/前置规则。"""
    for idx in (day_index - 1, day_index + 1):
        if not (0 <= idx < len(employee.schedule)):
            continue
        cell = employee.schedule[idx]
        if cell.is_locked or cell.is_historical or cell.is_blank:
            continue
        if not _cell_rules_ok(schedule, employee, idx, cell.base_shift, config):
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
            if target <= 0:  # 需求≤0 的班次不凭空填（避免制造冗余 A1/D）
                continue
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


def _try_rest_with_high_conversion(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    config: SchedulerConfig,
) -> bool:
    """OFF 插入仅被前一日 D/Z 的「次日非 OFF」规则挡住时：把该 D/Z 原子转为当日缺口工作班再插 OFF。

    调用前提：employee.schedule[day_index] 已被置为 REST_SHIFT（由 _move_rest_to_day 负责，
    失败时由其恢复原值）。本函数只负责前一日格的转换与回退。
    """
    prev_idx = day_index - 1
    if prev_idx < 0:
        return False
    prev_cell = employee.schedule[prev_idx]
    if (
        prev_cell.is_locked
        or prev_cell.is_historical
        or prev_cell.base_shift not in HIGH_LIMIT_SHIFTS
    ):
        return False
    replacement = _best_underfilled_shift(schedule, employee, prev_idx, config)
    if replacement is None:
        return False
    old_prev = prev_cell.value
    prev_cell.value = replacement
    if (
        _rest_streak_at(employee, day_index) <= config.max_consecutive_rest
        and _rest_block_spacing_ok(employee, day_index, config.min_work_days_between_rest_blocks)
        and _neighbors_valid(schedule, employee, day_index, config)
        and _neighbors_valid(schedule, employee, prev_idx, config)
    ):
        return True
    prev_cell.value = old_prev
    return False


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
        and _neighbors_valid(schedule, employee, day_index, config)
    ):
        return True

    if _try_rest_with_high_conversion(schedule, employee, day_index, config):
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
                    and _neighbors_valid(schedule, employee, day_index, config)
                    and _neighbors_valid(schedule, employee, rest_idx, config)
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
    day_actuals: dict[str, float] | None = None,
) -> bool:
    cell = employee.schedule[day_index]
    if cell.is_locked or cell.is_historical or cell.base_shift != REST_SHIFT:
        return False
    rest_actual = day_actuals[REST_SHIFT] if day_actuals is not None else _actual(schedule, day_index, REST_SHIFT)
    if rest_actual - employee.coefficient < target - config.demand_tolerance:
        return False
    return bool(_best_underfilled_shift(schedule, employee, day_index, config, day_actuals))


def _best_underfilled_shift(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    config: SchedulerConfig,
    day_actuals: dict[str, float] | None = None,
) -> str | None:
    cell = employee.schedule[day_index]
    old_value = cell.value
    cell.value = None
    try:
        options = []
        for shift in SHIFT_ORDER:
            actual = day_actuals[shift] if day_actuals is not None else _actual(schedule, day_index, shift)
            gap = schedule.adjusted_demands[day_index].get(shift) - actual
            if gap <= config.demand_tolerance:
                continue
            if _can_assign_shift(schedule, employee, day_index, shift, config):
                options.append((gap, -_shift_priority(shift), shift))
        if not options:
            return None
        return max(options)[2]
    finally:
        cell.value = old_value


def _try_convert_off_to_shift(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    config: SchedulerConfig,
) -> str | None:
    cell = employee.schedule[day_index]
    old_value = cell.value
    cell.value = None
    result = None
    try:
        for shift in ("A3", "A2"):
            if _can_assign_shift(schedule, employee, day_index, shift, config):
                cell.value = shift
                if _neighbors_valid(schedule, employee, day_index, config):
                    result = shift
                    break
                cell.value = None
    finally:
        cell.value = result if result else old_value
    return result


def _try_convert_work_to_rest(
    schedule: Schedule,
    employee: Employee,
    day_index: int,
    config: SchedulerConfig,
) -> bool:
    cell = employee.schedule[day_index]
    old_value = cell.value
    cell.value = None
    result = False
    try:
        if _can_assign_rest(schedule, employee, day_index, config):
            cell.value = REST_SHIFT
            if _neighbors_valid(schedule, employee, day_index, config):
                result = True
    finally:
        cell.value = REST_SHIFT if result else old_value
    return result


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
    balance = 0
    for group in BALANCE_GROUPS:
        if shift in group:
            balance = _active_count_any(employee, group, schedule)
            break
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


def _d_run_count(employee: Employee, day_index: int) -> int:
    return 1 + _same_group_count(employee, day_index - 1, -1, D_FAMILY) + _same_group_count(employee, day_index + 1, 1, D_FAMILY)


def _z_run_count(employee: Employee, day_index: int) -> int:
    return 1 + _same_group_count(employee, day_index - 1, -1, Z_FAMILY) + _same_group_count(employee, day_index + 1, 1, Z_FAMILY)


def _is_d_family(employee: Employee, day_index: int) -> bool:
    return 0 <= day_index < len(employee.schedule) and employee.schedule[day_index].base_shift in D_FAMILY


def _is_z_family(employee: Employee, day_index: int) -> bool:
    return 0 <= day_index < len(employee.schedule) and employee.schedule[day_index].base_shift in Z_FAMILY


def _z_sandwich(employee: Employee, day_index: int) -> bool:
    """day_index 排 Z/Z1 是否形成 Z→A→Z：任一方向的连续 A 类段（含 B/C）另一端紧贴 Z 族。"""
    return _a_run_touches_z(employee, day_index - 1, -1) or _a_run_touches_z(employee, day_index + 1, +1)


def _a_run_touches_z(employee: Employee, start: int, step: int) -> bool:
    idx = start
    if not (0 <= idx < len(employee.schedule)) or employee.schedule[idx].base_shift not in A_CLASS_SHIFTS:
        return False
    while 0 <= idx + step < len(employee.schedule) and employee.schedule[idx + step].base_shift in A_CLASS_SHIFTS:
        idx += step
    beyond = idx + step
    return 0 <= beyond < len(employee.schedule) and employee.schedule[beyond].base_shift in Z_FAMILY


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
