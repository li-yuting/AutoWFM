# -*- coding: utf-8 -*-
"""shift 子项目测试工具：统一 sys.path 注入与内存 Schedule 构造。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "shift"))

from models import DailyDemand, Employee, Schedule, ShiftCell  # noqa: E402
from scheduler import SchedulerConfig  # noqa: E402
from utils import SHIFT_ORDER  # noqa: E402


def make_schedule(rows, history_days=0, lock_values=True):
    """构造内存 Schedule。

    rows: [(姓名, 班组, 系数, 人员类型, [班次值, ...])]，值 None 表示空格。
    lock_values=True 时非空格视为锁定（模拟用户已填数据）；传 False 便于测可动格。
    """
    employees = []
    n_days = len(rows[0][4])
    for i, (name, group, coeff, ptype, values) in enumerate(rows):
        cells = [
            ShiftCell(
                value=v,
                original_value=v,
                is_locked=(v is not None and lock_values),
                is_historical=idx < history_days,
                row=i + 2,
                column=5 + idx,
            )
            for idx, v in enumerate(values)
        ]
        employees.append(
            Employee(name=name, group=group, coefficient=coeff,
                     is_phase3="三期" in ptype, schedule=cells,
                     row_index=i + 2, person_type=ptype)
        )
    dates = list(range(n_days))
    demands = [DailyDemand(date=d, demand={s: 0.0 for s in SHIFT_ORDER + ("OFF",)})
               for d in dates]
    return Schedule(employees=employees, dates=dates, demands=demands,
                    workbook_path="", history_days=history_days)


def make_config(**kw):
    return SchedulerConfig(**kw)
