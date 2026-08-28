# -*- coding: utf-8 -*-
"""shift 排班器单元测试：配置、约束、整型步骤（随任务逐个扩充）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "shift"))

from scheduler import SchedulerConfig  # noqa: E402
from shift_test_utils import make_config, make_schedule  # noqa: E402
from scheduler import _can_assign_shift  # noqa: E402


def test_z_config_defaults():
    cfg = SchedulerConfig()
    assert cfg.z_min_consecutive == 2
    assert cfg.z_max_consecutive == 3


def test_scheduler_config_from_form():
    from app import _scheduler_config_from
    cfg, err = _scheduler_config_from({})
    assert err is None
    assert cfg.z_min_consecutive == 2 and cfg.z_max_consecutive == 3

    cfg, err = _scheduler_config_from({"z_min_consecutive": "0"})
    assert cfg is None and "无效" in err

    cfg, err = _scheduler_config_from({"z_min_consecutive": "3", "z_max_consecutive": "2"})
    assert cfg is None and "无效" in err

    cfg, err = _scheduler_config_from({"z_min_consecutive": "2", "z_max_consecutive": "4"})
    assert err is None and cfg.z_max_consecutive == 4


def test_b_forbidden_after_d():
    s = make_schedule([("甲", "A", 1.0, "正式", ["D", None, None])])
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 1, "B", make_config())
    assert _can_assign_shift(s, emp, 1, "A2", make_config())


def test_b_allowed_after_z1():
    s = make_schedule([("甲", "A", 1.0, "正式", ["Z1", None, None])])
    assert _can_assign_shift(s, s.employees[0], 1, "B", make_config())


def test_c_only_after_high():
    s = make_schedule([("甲", "A", 1.0, "正式", ["A2", "Z", None, None])])
    emp = s.employees[0]
    assert _can_assign_shift(s, emp, 2, "C", make_config())      # 前日 Z

    s2 = make_schedule([("甲", "A", 1.0, "正式", ["A2", None, None, None])])
    emp2 = s2.employees[0]
    assert not _can_assign_shift(s2, emp2, 1, "C", make_config())  # 前日 A2
    assert not _can_assign_shift(s2, emp2, 3, "C", make_config())  # 前日空


def test_z_max_consecutive():
    s = make_schedule([("甲", "A", 1.0, "正式", [None, "Z", "Z", "Z", None])])
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 4, "Z", make_config())  # 成 4 连
    assert not _can_assign_shift(s, emp, 0, "Z", make_config())  # 成 4 连（双向计数）
    s2 = make_schedule([("甲", "A", 1.0, "正式", [None, "Z", "Z", None, None])])
    assert _can_assign_shift(s2, s2.employees[0], 3, "Z", make_config())  # 恰 3 连


def test_z_next_day_not_off():
    s = make_schedule([("甲", "A", 1.0, "正式", ["Z", None, "OFF", None])])
    assert not _can_assign_shift(s, s.employees[0], 1, "Z", make_config())


def test_z_no_sandwich():
    # Z A2 ?：? 处排 Z 会形成 Z→A→Z
    s = make_schedule([("甲", "A", 1.0, "正式", ["Z", "A2", None, "A2", None])])
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 2, "Z", make_config())
    # 对称：? A2 Z
    s2 = make_schedule([("甲", "A", 1.0, "正式", [None, "A2", "A2", "Z", None])])
    assert not _can_assign_shift(s2, s2.employees[0], 0, "Z", make_config())


def test_phase3_cannot_bc():
    s = make_schedule([("甲", "A", 1.0, "三期", ["A2", None, None])])
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 1, "B", make_config())
    assert not _can_assign_shift(s, emp, 1, "C", make_config())
    assert _can_assign_shift(s, emp, 1, "A2", make_config())


def test_newbie_only_banned_from_d():
    s = make_schedule([("甲", "A", 0.8, "正式", ["A2", None, None])])
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 1, "D", make_config())
    assert _can_assign_shift(s, emp, 1, "Z", make_config())
    assert _can_assign_shift(s, emp, 1, "B", make_config())


def test_z_run_counts_history():
    # 历史段 Z 计入连排：历史 3 连 Z 后，活跃段再排 Z = 4 连，超上限
    s = make_schedule([("甲", "A", 1.0, "正式", ["Z", "Z", "Z", None, None])], history_days=3)
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 3, "Z", make_config())
    assert _can_assign_shift(s, emp, 3, "A2", make_config())


def test_fallback_never_bc_without_demand():
    from scheduler import _fallback_shift
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", None, "A2", "A2", "A2", "A2", "A2", "A2", "A2", "A2"])], lock_values=False)
    out = _fallback_shift(s, s.employees[0], 1, make_config())
    assert out not in ("B", "C"), "无需求缺口时兜底不得产出 B/C"


def test_b_rule_assign_time():
    s = make_schedule([("甲", "A", 1.0, "正式", ["D", None, "Z1", None])])
    emp = s.employees[0]
    assert not _can_assign_shift(s, emp, 1, "B", make_config())  # 前日 D
    assert _can_assign_shift(s, emp, 3, "B", make_config())      # 前日 Z1 允许


def test_z_sandwich_deep_gap():
    # Z A2 A2 ?：? 排 Z 会形成 Z→A→Z（跨 2 个 A 类日）
    s = make_schedule([("甲", "A", 1.0, "正式", ["Z", "A2", "A2", None, "A2"])])
    assert not _can_assign_shift(s, s.employees[0], 3, "Z", make_config())


def test_redistribute_balance_z_group():
    from scheduler import redistribute_balance
    # 与简报原 fixture 的差异：各插入 1 天 OFF。原 fixture 两行均 10 连勤，
    # _can_assign_shift 的连勤上限(6)使 _can_hold_shift 恒 False，永远无法交换。
    s = make_schedule([
        ("甲", "A", 1.0, "正式", ["Z", "Z", "Z", "OFF"] + ["A2"] * 6),
        ("乙", "A", 1.0, "正式", ["A2"] * 5 + ["OFF"] + ["A2"] * 4),
    ], lock_values=False)
    redistribute_balance(s, make_config(balance_threshold=2))
    bases0 = [c.base_shift for c in s.employees[0].schedule]
    bases1 = [c.base_shift for c in s.employees[1].schedule]
    n0 = sum(1 for b in bases0 if b in ("Z", "Z1"))
    n1 = sum(1 for b in bases1 if b in ("Z", "Z1"))
    assert n0 - n1 <= 2
    assert bases1[0] == "Z" and bases0[0] == "A2"


if __name__ == "__main__":
    test_z_config_defaults()
    test_scheduler_config_from_form()
    test_b_forbidden_after_d()
    test_b_allowed_after_z1()
    test_c_only_after_high()
    test_z_max_consecutive()
    test_z_next_day_not_off()
    test_z_no_sandwich()
    test_phase3_cannot_bc()
    test_newbie_only_banned_from_d()
    test_z_run_counts_history()
    test_fallback_never_bc_without_demand()
    test_b_rule_assign_time()
    test_z_sandwich_deep_gap()
    test_redistribute_balance_z_group()
    print("test_shift_scheduler OK")
