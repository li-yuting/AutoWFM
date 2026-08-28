# -*- coding: utf-8 -*-
"""shift 排班器单元测试：配置、约束、整型步骤（随任务逐个扩充）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "shift"))

from scheduler import SchedulerConfig  # noqa: E402


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


if __name__ == "__main__":
    test_z_config_defaults()
    test_scheduler_config_from_form()
    print("test_shift_scheduler OK")
