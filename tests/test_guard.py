# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from zoneinfo import ZoneInfo
from collector._utils import in_window
TZ = ZoneInfo("Asia/Shanghai")
cfg = {"schedule":{"window_start":"09:00","window_end":"21:00","timezone":"Asia/Shanghai"}}
sub_12378 = {"name":"12378","schedule":{"weekday":{"start":"08:30","end":"21:00"},"weekend":{"start":"09:00","end":"18:00"}}}
def at(h,m): return datetime(2026,7,22,h,m,tzinfo=TZ)   # 周三
def sat(h,m): return datetime(2026,7,25,h,m,tzinfo=TZ)  # 周六

def main():
    # 全局窗口(无 dow 区分,每天 9-21)
    assert in_window(cfg, None, at(9,0)) is False
    assert in_window(cfg, None, at(9,5)) is True
    assert in_window(cfg, None, at(21,0)) is True
    assert in_window(cfg, None, at(21,5)) is False
    assert in_window(cfg, None, sat(9,5)) is True   # 周末也 9-21
    # 12378 周内 (8:30, 21:00]
    assert in_window(cfg, sub_12378, at(8,30)) is False
    assert in_window(cfg, sub_12378, at(8,35)) is True
    assert in_window(cfg, sub_12378, at(9,0)) is True
    assert in_window(cfg, sub_12378, at(21,0)) is True
    assert in_window(cfg, sub_12378, at(21,5)) is False
    # 12378 周末 (9:00, 18:00]
    assert in_window(cfg, sub_12378, sat(8,35)) is False
    assert in_window(cfg, sub_12378, sat(9,0)) is False
    assert in_window(cfg, sub_12378, sat(9,5)) is True
    assert in_window(cfg, sub_12378, sat(18,0)) is True
    assert in_window(cfg, sub_12378, sat(18,5)) is False
    print("guard OK")

if __name__ == "__main__": main()
