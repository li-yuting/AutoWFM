"""共享工具函数:时间解析、窗口判断、配置加载。"""
import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# 密钥环境变量映射: (cfg 路径元组, 环境变量名)。环境变量非空则覆盖 config.yaml。
_SECRET_ENV_MAP = [
    (("secrets", "token"), "AUTOWFM_TOKEN"),
    (("secrets", "tenementId"), "AUTOWFM_TENEMENT_ID"),
    (("notify", "webhook", "main_key"), "AUTOWFM_WEBHOOK_MAIN"),
    (("notify", "webhook", "secondary_key"), "AUTOWFM_WEBHOOK_SECONDARY"),
    (("notify", "dash_token"), "AUTOWFM_DASH_TOKEN"),
]


def _apply_env_secrets(cfg: dict) -> None:
    """用环境变量覆盖 config.yaml 中的密钥(未设则保留原值,向后兼容)。"""
    for path, env in _SECRET_ENV_MAP:
        val = os.environ.get(env)
        if not val:
            continue
        node = cfg
        for key in path[:-1]:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node[path[-1]] = val


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
    # 加载 .env(若存在),将密钥注入环境变量;load_dotenv 默认不覆盖已设变量。
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv 未装时退化为纯环境变量
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _apply_env_secrets(cfg)
    return cfg