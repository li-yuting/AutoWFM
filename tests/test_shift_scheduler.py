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
from scheduler import shape_z_runs, _swap_pair  # noqa: E402


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


def test_cluster_high_pairs_d_family_only():
    from scheduler import cluster_high_pairs, precompute
    # 块内 D@1 与 D1@3 不相邻 → 应聚成 D D1 相连；Z1@5 不参与计数
    # （旧逻辑按 HIGH_LIMIT_SHIFTS 计 D/Z1/D1 = 3 个 → 跳过聚合；precompute 供需求容差校验）
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "D", "A2", "D1", "A2", "Z1", "OFF", "A2"])], lock_values=False)
    precompute(s, make_config())
    cluster_high_pairs(s, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[1:3] == ["D", "D1"], bases
    assert bases[3] == "A2", bases


def test_shape_z_extends_orphan():
    from scheduler import precompute
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "Z", "A2", "A2", "A2", "A2", "A2", "OFF", "A2", "A2"])], lock_values=False)
    precompute(s, make_config())  # 供需求容差校验（与 cluster_high_pairs 用例同因）
    shape_z_runs(s, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[1:3] == ["Z", "Z"], bases


def test_shape_z_respects_zmax():
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "Z", "Z", "Z", "A2", "A2", "A2", "A2", "OFF", "A2"])], lock_values=False)
    shape_z_runs(s, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[4] == "A2", "已达上限不得再扩"


def test_shape_z_merges_runs():
    from scheduler import precompute
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "Z", "Z", "A2", "Z", "Z", "A2", "OFF", "A2", "A2"])], lock_values=False)
    precompute(s, make_config(z_max_consecutive=5))  # 供需求容差校验
    shape_z_runs(s, make_config(z_max_consecutive=5))
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[1:6] == ["Z"] * 5, bases


def test_shape_z_pull_rejects_invariant_break():
    # 贴 OFF 对调(len2 Z 块)会产生 Z/A2/Z → check09 夹心 + check18 多 Z 块，守卫必须拒绝并原样保留
    from scheduler import precompute
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "A2", "Z", "Z", "A2", "A2", "A2", "A2", "OFF", "A2"])], lock_values=False)
    precompute(s, make_config())  # 供需求容差校验
    shape_z_runs(s, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[1] == "A2" and bases[2:4] == ["Z", "Z"], bases


def test_swap_pair():
    # 单 Z 块对调贴 OFF：Z@2 ← A2@1 → OFF Z A2 ...，不拆 Z 块、无夹心，合法
    from scheduler import precompute
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "A2", "Z", "A2", "A2", "OFF", "A2", "A2", "A2", "A2"])], lock_values=False)
    precompute(s, make_config())  # 供需求容差校验
    assert _swap_pair(s, s.employees[0], 1, 2, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[1:3] == ["Z", "A2"]


def test_swap_pair_rejects_b_after_d():
    # 对调后 D 移到 B 前一日 → 幸存 B 违反前置规则(check16)，必须整体回退
    from scheduler import precompute
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "B", "D", "A2", "OFF", "A2", "A2", "A2", "A2", "A2"])], lock_values=False)
    precompute(s, make_config())  # 供需求容差校验
    assert not _swap_pair(s, s.employees[0], 1, 2, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[1] == "B" and bases[2] == "D", bases


def test_convert_to_z_rejects_b_prev_break():
    # B 的前一日被整成 Z → 幸存邻格被破坏(check16)，_convert_to_z 必须回退
    # （Z→A→Z 深夹心已由 _can_place_z/_z_sandwich 在放置前拒绝，见 test_z_sandwich_deep_gap）
    from scheduler import _convert_to_z, precompute
    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "A2", "A2", "B", "A2", "OFF", "A2", "A2", "A2", "A2"])], lock_values=False)
    precompute(s, make_config())  # 供需求容差校验
    assert not _convert_to_z(s, s.employees[0], 2, make_config())
    bases = [c.base_shift for c in s.employees[0].schedule]
    assert bases[2] == "A2" and bases[3] == "B", bases


def test_move_rest_converts_blocking_z():
    # 8 连班 B Z Z Z A2 A2 A3 A3，唯一合法 OFF 位(5)被前日 Z@4 的次日规则挡住：
    # 回退应把 Z@4 原子转为缺口班(A2)，再落 OFF@5
    from scheduler import _move_rest_to_day, precompute

    s = make_schedule([("甲", "A", 1.0, "正式",
                        ["OFF", "B", "Z", "Z", "Z", "A2", "A2", "A3", "A3", "OFF", "OFF", "A2"])],
                      lock_values=False)
    s.demands[4].demand["A2"] = 2.0  # 前日格的缺口班候选
    precompute(s, make_config())
    emp = s.employees[0]
    assert _move_rest_to_day(s, emp, 5, make_config())
    bases = [c.base_shift for c in emp.schedule]
    assert bases[5] == "OFF", bases
    assert bases[4] == "A2", bases            # 挡路 Z 已转为缺口班
    assert bases[2:4] == ["Z", "Z"], bases    # 剩余 Z 块仍为 2 天


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
    test_cluster_high_pairs_d_family_only()
    test_shape_z_extends_orphan()
    test_shape_z_respects_zmax()
    test_shape_z_merges_runs()
    test_shape_z_pull_rejects_invariant_break()
    test_swap_pair()
    test_swap_pair_rejects_b_after_d()
    test_convert_to_z_rejects_b_prev_break()
    test_move_rest_converts_blocking_z()
    print("test_shift_scheduler OK")
