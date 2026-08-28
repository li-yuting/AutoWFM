# -*- coding: utf-8 -*-
"""shift 校验器测试（随任务扩充）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "shift"))

from shift_test_utils import make_config, make_schedule  # noqa: E402
from validators import _check_balance  # noqa: E402


def test_check_balance_z_group_non_phase3():
    s = make_schedule([
        ("甲", "A", 1.0, "正式", ["Z", "Z", "Z"] + ["A2"] * 7),
        ("乙", "A", 1.0, "正式", ["A2"] * 10),
        ("丙", "A", 1.0, "三期", ["A2"] * 10),
    ])
    warns = [w for w in _check_balance(s, make_config(balance_threshold=2)) if "Z/Z1" in w.message]
    assert warns, "Z/Z1 计数差 3 > 2 应报警"


if __name__ == "__main__":
    test_check_balance_z_group_non_phase3()
    print("test_shift_validators OK")
