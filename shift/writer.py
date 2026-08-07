from __future__ import annotations

from collections import defaultdict
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from models import Schedule
from utils import (
    HIGH_BALANCE_SHIFTS,
    SECONDARY_BALANCE_SHIFTS,
    SHIFT_ORDER,
    date_label,
)


def write_schedule(schedule: Schedule, output_path: str | Path) -> None:
    wb = load_workbook(schedule.workbook_path)
    ws = wb[schedule.schedule_sheet_name]

    for employee in schedule.employees:
        for cell in employee.schedule:
            ws.cell(employee.row_index, cell.column).value = cell.value

    if "统计" in wb.sheetnames:
        del wb["统计"]
    stats = wb.create_sheet("统计")
    _write_stats(stats, schedule)
    wb.save(output_path)


def _write_stats(ws, schedule: Schedule) -> None:
    ws.sheet_view.showGridLines = False
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True, name="Microsoft YaHei")
    normal_font = Font(name="Microsoft YaHei")

    row = 1
    row = _section(ws, row, "每日满足情况", header_fill, header_font)
    ws.append(["日期", "班次", "需求", "实际", "差异"])
    _style_header(ws[row])
    row += 1
    for day_index, demand in enumerate(schedule.adjusted_demands or []):
        actual = _actual_by_shift(schedule, day_index)
        for shift in SHIFT_ORDER + ("OFF",):
            target = demand.get(shift)
            value = actual.get(shift, 0.0)
            ws.append([date_label(schedule.dates[day_index]), shift, round(target, 2), round(value, 2), round(value - target, 2)])
            row += 1

    row += 2
    row = _section(ws, row, "员工统计", header_fill, header_font)
    ws.append(["姓名", "班组", "高强均衡(D/D1/A1)", "次高强均衡(Z/Z1/A4)", "休息天数", "连续双休次数"])
    _style_header(ws[row])
    row += 1
    for employee in schedule.employees:
        high_balance = 0
        secondary_balance = 0
        rest_days = 0
        double_rests = 0
        prev_rest = False
        for idx in schedule.active_indexes:
            base = employee.schedule[idx].base_shift
            if base in HIGH_BALANCE_SHIFTS:
                high_balance += 1
            if base in SECONDARY_BALANCE_SHIFTS:
                secondary_balance += 1
            is_rest = base == "OFF"
            if is_rest:
                rest_days += 1
            if is_rest and prev_rest:
                double_rests += 1
            prev_rest = is_rest
        ws.append([employee.name, employee.group, high_balance, secondary_balance, rest_days, double_rests])
        row += 1

    row += 2
    row = _section(ws, row, "OFF/A3 调整", header_fill, header_font)
    ws.append(["日期", "OFF转A3", "A3转OFF"])
    _style_header(ws[row])
    row += 1
    for demand in schedule.adjusted_demands:
        if demand.off_to_a3 or demand.a3_to_off:
            ws.append([date_label(demand.date), round(demand.off_to_a3, 2), round(demand.a3_to_off, 2)])
            row += 1

    row += 2
    row = _section(ws, row, "警告信息", header_fill, header_font)
    ws.append(["编号", "级别", "员工", "日期", "描述"])
    _style_header(ws[row])
    row += 1
    for warning in schedule.warnings:
        ws.append([warning.check_id, warning.severity, warning.employee, date_label(warning.date), warning.message])
        row += 1

    for col in range(1, ws.max_column + 1):
        ws.column_dimensions[ws.cell(1, col).column_letter].width = 18
    for cells in ws.iter_rows():
        for cell in cells:
            if cell.font:
                font = copy(cell.font)
                font.name = "Microsoft YaHei"
                cell.font = font
            else:
                cell.font = normal_font
            cell.alignment = Alignment(horizontal="center", vertical="center")


def _actual_by_shift(schedule: Schedule, day_index: int) -> dict[str, float]:
    totals = defaultdict(float)
    for employee in schedule.employees:
        base = employee.schedule[day_index].base_shift
        if base:
            totals[base] += employee.coefficient
    return dict(totals)


def _section(ws, row: int, title: str, fill, font) -> int:
    ws.cell(row, 1).value = title
    ws.cell(row, 1).fill = fill
    ws.cell(row, 1).font = font
    return row + 1


def _style_header(cells) -> None:
    fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in cells:
        cell.fill = fill
        cell.font = Font(bold=True, name="Microsoft YaHei")
