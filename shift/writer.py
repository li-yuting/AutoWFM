from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from models import Schedule
from scheduler import _group_streaks, _rest_blocks
from utils import (
    A_BALANCE_SHIFTS,
    D_FAMILY,
    REST_SHIFT,
    SHIFT_ORDER,
    WORK_SHIFTS,
    Z_FAMILY,
    date_label,
)
from validators import _actual_by_shift


def write_schedule(schedule: Schedule, output_path: str | Path) -> None:
    wb = load_workbook(schedule.workbook_path)
    ws = wb[schedule.schedule_sheet_name]

    for employee in schedule.employees:
        for cell in employee.schedule:
            ws.cell(employee.row_index, cell.column).value = cell.value

    _highlight_schedule(ws, schedule)

    if "统计" in wb.sheetnames:
        del wb["统计"]
    stats = wb.create_sheet("统计")
    _write_stats(stats, schedule)
    wb.save(output_path)


# 级别 -> 单元格填充色（浅色，不遮文字）；同格多级命中时取 ERROR > WARN > INFO
# 配色方案 B：Excel 经典条件格式（浅红 / 黄 / 蓝）
_SEVERITY_FILL = {
    "ERROR": PatternFill("solid", fgColor="FFC7CE"),
    "WARN": PatternFill("solid", fgColor="FFEB9C"),
    "INFO": PatternFill("solid", fgColor="BDD7EE"),
}
_SEVERITY_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}


def _highlight_schedule(ws, schedule: Schedule) -> None:
    """按验证警告为班表 sheet 的班次单元格填充高亮色。"""
    date_cols = {d: col for d, col in zip(schedule.dates, schedule.date_columns)}
    cells: dict[tuple[int, int], str] = {}
    for w in schedule.warnings:
        if not w.employee or w.date is None or w.severity not in _SEVERITY_FILL:
            continue
        emp = next((e for e in schedule.employees if e.name == w.employee), None)
        if emp is None or w.date not in date_cols:
            continue
        idx = schedule.dates.index(w.date)
        col = date_cols[w.date]
        if w.check_id in ("04", "05", "08", "18"):
            start, end = _containing_streak(emp, idx, _streak_shifts(w.check_id))
        elif w.check_id == "14":
            start, end = _rest_gap_range(emp, idx)
        else:
            start, end = idx, idx
        for i in range(start, end + 1):
            key = (emp.row_index, date_cols[schedule.dates[i]])
            if key not in cells or _SEVERITY_ORDER[w.severity] < _SEVERITY_ORDER[cells[key]]:
                cells[key] = w.severity
    for (row, col), sev in cells.items():
        ws.cell(row, col).fill = _SEVERITY_FILL[sev]


def _streak_shifts(check_id: str) -> set[str]:
    if check_id == "05":
        return {REST_SHIFT}
    if check_id == "08":
        return D_FAMILY
    if check_id == "18":
        return Z_FAMILY
    return WORK_SHIFTS


def _containing_streak(employee, idx: int, shifts: set[str]) -> tuple[int, int]:
    for start, end, _ in _group_streaks(employee, shifts):
        if start <= idx <= end:
            return start, end
    return idx, idx


def _rest_gap_range(employee, idx: int) -> tuple[int, int]:
    """休息间隔警告(14)：高亮 前一休息块 → 上班段 → 本次休息块 的整段。"""
    blocks = _rest_blocks(employee)
    pos = next((i for i, (s, e) in enumerate(blocks) if s <= idx <= e), None)
    if pos is None:
        return idx, idx
    prev_start = blocks[pos - 1][0] if pos > 0 else idx
    return prev_start, blocks[pos][1]


def _write_stats(ws, schedule: Schedule) -> None:
    ws.sheet_view.showGridLines = False
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True, name="Microsoft YaHei")

    _section(ws, "每日满足情况", header_fill, header_font)
    ws.append(["日期", "班次", "需求", "实际", "差异"])
    _style_header(ws[ws.max_row])
    for day_index, demand in enumerate(schedule.adjusted_demands or []):
        actual = _actual_by_shift(schedule, day_index)
        for shift in SHIFT_ORDER + ("OFF",):
            target = demand.get(shift)
            value = actual.get(shift, 0.0)
            ws.append([date_label(schedule.dates[day_index]), shift, round(target, 2), round(value, 2), round(value - target, 2)])

    ws.append([])
    ws.append([])
    _section(ws, "员工统计", header_fill, header_font)
    ws.append(["姓名", "班组", "D/D1 均衡", "Z/Z1 均衡", "A1/A4 均衡", "休息天数", "连续双休次数"])
    _style_header(ws[ws.max_row])
    for employee in schedule.employees:
        d_count = 0
        z_count = 0
        a_count = 0
        rest_days = 0
        double_rests = 0
        prev_rest = False
        for idx in schedule.active_indexes:
            base = employee.schedule[idx].base_shift
            if base in D_FAMILY:
                d_count += 1
            if base in Z_FAMILY:
                z_count += 1
            if base in A_BALANCE_SHIFTS:
                a_count += 1
            is_rest = base == "OFF"
            if is_rest:
                rest_days += 1
            if is_rest and prev_rest:
                double_rests += 1
            prev_rest = is_rest
        ws.append([employee.name, employee.group, d_count, z_count, a_count, rest_days, double_rests])

    ws.append([])
    ws.append([])
    _section(ws, "OFF/A3 调整", header_fill, header_font)
    ws.append(["日期", "OFF转A3", "A3转OFF"])
    _style_header(ws[ws.max_row])
    for demand in schedule.adjusted_demands:
        if demand.off_to_a3 or demand.a3_to_off:
            ws.append([date_label(demand.date), round(demand.off_to_a3, 2), round(demand.a3_to_off, 2)])

    ws.append([])
    ws.append([])
    _section(ws, "警告信息", header_fill, header_font)
    ws.append(["编号", "级别", "员工", "日期", "描述"])
    _style_header(ws[ws.max_row])
    for warning in schedule.warnings:
        ws.append([warning.check_id, warning.severity, warning.employee, date_label(warning.date), warning.message])

    for col in range(1, ws.max_column + 1):
        ws.column_dimensions[ws.cell(1, col).column_letter].width = 18
    for cells in ws.iter_rows():
        for cell in cells:
            if cell.font:
                font = copy(cell.font)
                font.name = "Microsoft YaHei"
                cell.font = font
            cell.alignment = Alignment(horizontal="center", vertical="center")


def _section(ws, title: str, fill, font) -> None:
    ws.append([title])
    ws.cell(ws.max_row, 1).fill = fill
    ws.cell(ws.max_row, 1).font = font


def _style_header(cells) -> None:
    fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in cells:
        cell.fill = fill
        cell.font = Font(bold=True, name="Microsoft YaHei")
