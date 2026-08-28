from __future__ import annotations

from datetime import datetime
from typing import Any

SHIFT_ORDER = ("D", "D1", "Z", "Z1", "C", "B", "A1", "A4", "A2", "A3")
ALL_SHIFTS = set(SHIFT_ORDER) | {"OFF"}
HIGH_SHIFTS = {"D", "D1"}
D_FAMILY = HIGH_SHIFTS
SECONDARY_HIGH_SHIFTS = {"Z", "Z1"}
Z_FAMILY = SECONDARY_HIGH_SHIFTS
HIGH_LIMIT_SHIFTS = D_FAMILY | Z_FAMILY
A_CLASS_SHIFTS = {"A1", "A2", "A3", "A4", "B", "C"}
COMFORT_SHIFTS = {"A2", "A3"}
D_BALANCE_SHIFTS = set(D_FAMILY)
Z_BALANCE_SHIFTS = set(Z_FAMILY)
A_BALANCE_SHIFTS = {"A1", "A4"}
BALANCE_GROUPS = (D_BALANCE_SHIFTS, Z_BALANCE_SHIFTS, A_BALANCE_SHIFTS)
# 旧均衡组（Task 4 删除；删除前 scheduler/validators/writer 仍在引用）
HIGH_BALANCE_SHIFTS = {"D", "D1", "A1"}
SECONDARY_BALANCE_SHIFTS = {"Z", "Z1", "A4"}
WORK_SHIFTS = set(SHIFT_ORDER)
REST_SHIFT = "OFF"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_shift(value: Any) -> str:
    text = clean_text(value).upper()
    if not text:
        return ""
    if text == "0":
        return REST_SHIFT
    for shift in sorted(ALL_SHIFTS, key=len, reverse=True):
        if text == shift or text.startswith(shift + "(") or text.startswith(shift + "（"):
            return shift
        if shift in {"A1", "A2", "A3", "A4"} and text.startswith(shift):
            return shift
    return text


def is_rest(value: Any) -> bool:
    return normalize_shift(value) == REST_SHIFT


def is_work(value: Any) -> bool:
    return normalize_shift(value) in WORK_SHIFTS


def is_high_limited(value: Any) -> bool:
    return normalize_shift(value) in HIGH_LIMIT_SHIFTS


def is_a_class(value: Any) -> bool:
    return normalize_shift(value) in A_CLASS_SHIFTS


def date_label(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return clean_text(value)


def number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def almost_met(actual: float, target: float, tolerance: float = 0.35) -> bool:
    return actual + tolerance >= target
