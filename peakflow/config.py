"""PeakFlow 配置 — 除 auto_tableau_dir（机器相关路径）外均为代码常量。

模型参数曾放 config.yaml 的 forecast 段，但 17 个键全部等于代码默认值
（无人调过），故收敛为常量，仅保留唯一的机器相关路径 auto_tableau_dir 可配。
"""
from __future__ import annotations
from pathlib import Path
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CFG_PATH = BASE_DIR / "config.yaml"

_DEFAULT_TABLEAU = r"D:\PythonProject\AutoTableau"


def _auto_tableau_dir() -> str:
    if not CFG_PATH.is_file():
        return _DEFAULT_TABLEAU
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return (cfg.get("forecast") or {}).get("auto_tableau_dir", _DEFAULT_TABLEAU)


DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
HOLIDAY_FILE = DATA_DIR / "节假日.csv"

# 取数
AUTO_TABLEAU_DIR = Path(_auto_tableau_dir())
FETCH_FILES = ["在线各类用户.csv", "热线各类用户.csv"]
FETCH_MAX_AGE_DAYS = 2

# 业务常量
CLIENT_TYPES = ["M1", "M2-M3", "M3+", "购买过权益卡且未逾期",
                "behind_30", "over_30", "repay_3in", "repay_3out"]
TOTAL_TYPE = "合计"

# 模型参数
HORIZON = 30
MIN_HISTORY = 28
TREND_FIT_DAYS = 14
RATIO_WINDOW = 14
BACKTEST_WINDOW = 14
SIGMA_K = 1.0
TREND_DAMP = 0.8
MONTH_SEASONAL_TYPES = ["M2-M3", "M3+"]
DOM_SMOOTH_WINDOW = 3
DOM_MIN_MONTHS = 2
TOTAL_WINDOW = 14
