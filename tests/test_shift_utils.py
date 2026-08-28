# -*- coding: utf-8 -*-
"""shift 班次家族基础：B/C 班次与 D/Z 家族、三组均衡常量。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "shift"))

from utils import (  # noqa: E402
    A_CLASS_SHIFTS,
    BALANCE_GROUPS,
    COMFORT_SHIFTS,
    D_FAMILY,
    HIGH_LIMIT_SHIFTS,
    SHIFT_ORDER,
    Z_FAMILY,
    normalize_shift,
)


def test_shift_order_contains_bc():
    assert SHIFT_ORDER == ("D", "D1", "Z", "Z1", "C", "B", "A1", "A4", "A2", "A3")


def test_families():
    assert D_FAMILY == {"D", "D1"}
    assert Z_FAMILY == {"Z", "Z1"}
    assert HIGH_LIMIT_SHIFTS == {"D", "D1", "Z", "Z1"}


def test_bc_are_a_class_not_comfort():
    assert {"B", "C"} <= A_CLASS_SHIFTS
    assert "B" not in COMFORT_SHIFTS
    assert "C" not in COMFORT_SHIFTS


def test_balance_groups():
    assert BALANCE_GROUPS == ({"D", "D1"}, {"Z", "Z1"}, {"A1", "A4"})


def test_normalize_shift_bc():
    assert normalize_shift("b") == "B"
    assert normalize_shift("c") == "C"
    assert normalize_shift("B(8:30)") == "B"
    assert normalize_shift("a2") == "A2"
    assert normalize_shift("0") == "OFF"
    assert normalize_shift("奇怪的班") == "奇怪的班"


if __name__ == "__main__":
    test_shift_order_contains_bc()
    test_families()
    test_bc_are_a_class_not_comfort()
    test_balance_groups()
    test_normalize_shift_bc()
    print("test_shift_utils OK")
