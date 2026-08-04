"""共享工具函数:时间解析、窗口判断、配置加载。"""
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


def parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_hhmm(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def in_window(cfg, sub=None, now=None):
    """sub 带 schedule(weekday/weekend)则按周几判;否则用全局 window_start/window_end。
    sub=None 走全局窗口(requests 用)。边界 (start, end]。"""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.datetime.now(tz)
    mins = now.hour * 60 + now.minute
    sch = sub.get("schedule") if sub else None
    if sch and ("weekday" in sch or "weekend" in sch):
        w = sch["weekday"] if now.weekday() < 5 else sch["weekend"]
        start, end = parse_hhmm(w["start"]), parse_hhmm(w["end"])
    else:
        g = cfg["schedule"]
        start, end = parse_hhmm(g["window_start"]), parse_hhmm(g["window_end"])
    return start < mins <= end


def load_cfg(path: str | Path = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)