# -*- coding: utf-8 -*-
"""shift 校验器测试（随任务扩充）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "shift"))

from shift_test_utils import make_config, make_schedule  # noqa: E402
from validators import _check_b_predecessor, _check_balance, _check_c_placement, _check_high_streaks, _check_z_blocks  # noqa: E402


def test_check_balance_z_group_non_phase3():
    s = make_schedule([
        ("甲", "A", 1.0, "正式", ["Z", "Z", "Z"] + ["A2"] * 7),
        ("乙", "A", 1.0, "正式", ["A2"] * 10),
        ("丙", "A", 1.0, "三期", ["A2"] * 10),
    ])
    warns = [w for w in _check_balance(s, make_config(balance_threshold=2)) if "Z/Z1" in w.message]
    assert warns, "Z/Z1 计数差 3 > 2 应报警"


def test_check_b_predecessor():
    s = make_schedule([("甲", "A", 1.0, "正式", ["D", "B", "A2"])], lock_values=False)
    ws = _check_b_predecessor(s)
    assert len(ws) == 1 and ws[0].severity == "ERROR" and ws[0].check_id == "16"

    s2 = make_schedule([("甲", "A", 1.0, "正式", ["D", "B", "A2"])])  # 默认锁定 → WARN
    ws2 = _check_b_predecessor(s2)
    assert ws2 and ws2[0].severity == "WARN"

    s3 = make_schedule([("甲", "A", 1.0, "正式", ["Z1", "B", "A2"])], lock_values=False)
    assert _check_b_predecessor(s3) == []  # Z1 后可 B


def test_check_c_placement():
    s = make_schedule([("甲", "A", 1.0, "正式", ["A2", "C", "A2"])], lock_values=False)
    ws = _check_c_placement(s)
    assert len(ws) == 1 and ws[0].severity == "ERROR" and ws[0].check_id == "17"

    s2 = make_schedule([("甲", "A", 1.0, "正式", ["Z1", "C", "A2"])], lock_values=False)
    assert _check_c_placement(s2) == []  # Z1 后可 C


def test_check_z_blocks_over_max():
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["Z", "Z", "Z", "Z", "A2", "A2", "A2", "A2", "OFF", "A2"])], lock_values=False)
    msgs = [w.message for w in _check_z_blocks(s, make_config())]
    assert any("超过上限" in m for m in msgs)


def test_check_z_blocks_short():
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "Z", "A2", "A2", "A2", "A2", "A2", "A2", "OFF", "A2"])], lock_values=False)
    msgs = [w.message for w in _check_z_blocks(s, make_config())]
    assert any("少于下限" in m for m in msgs)


def test_check_z_blocks_multi_run():
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "Z", "Z", "A2", "A2", "Z", "Z", "A2", "OFF", "A2"])], lock_values=False)
    msgs = [w.message for w in _check_z_blocks(s, make_config())]
    assert any("多个 Z/Z1 块" in m for m in msgs)


def test_check_high_streaks_d_only():
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["Z", "Z", "Z", "A2", "A2", "A2", "A2", "A2", "A2", "A2"])], lock_values=False)
    assert _check_high_streaks(s, make_config()) == []  # Z 3 连不再触发 08

    s2 = make_schedule([("甲", "A", 1.0, "正式",
                         ["D", "D", "D", "A2", "A2", "A2", "A2", "A2", "A2", "A2"])], lock_values=False)
    assert any("D/D1" in w.message for w in _check_high_streaks(s2, make_config()))


if __name__ == "__main__":
    test_check_balance_z_group_non_phase3()
    test_check_b_predecessor()
    test_check_c_placement()
    test_check_z_blocks_over_max()
    test_check_z_blocks_short()
    test_check_z_blocks_multi_run()
    test_check_high_streaks_d_only()
    print("test_shift_validators OK")
