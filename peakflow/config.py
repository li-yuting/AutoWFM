"""PeakFlow 配置 — 从 AutoWFM config.yaml 的 forecast 段读取。"""
from __future__ import annotations
from pathlib import Path
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CFG_PATH = BASE_DIR / "config.yaml"

def _load_forecast_cfg():
    if not CFG_PATH.is_file():
        return {}
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("forecast", {})

_fc = _load_forecast_cfg()

DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / _fc.get("output_dir", "output")

# 取数
AUTO_TABLEAU_DIR = Path(_fc.get("auto_tableau_dir", r"D:\PythonProject\AutoTableau"))
FETCH_FILES = _fc.get("fetch_files", ["在线各类用户.csv", "热线各类用户.csv"])
FETCH_MAX_AGE_DAYS = int(_fc.get("fetch_max_age_days", 2))

# 业务常量
CLIENT_TYPES = ["M1", "M2-M3", "M3+", "购买过权益卡且未逾期",
                "behind_30", "over_30", "repay_3in", "repay_3out"]
TOTAL_TYPE = "合计"

# 模型参数
HORIZON = int(_fc.get("horizon", 30))
MIN_HISTORY = int(_fc.get("min_history", 28))
TREND_FIT_DAYS = int(_fc.get("trend_fit_days", 14))
RATIO_WINDOW = int(_fc.get("ratio_window", 14))
BACKTEST_WINDOW = int(_fc.get("backtest_window", 14))
SIGMA_K = float(_fc.get("sigma_k", 1.0))
TREND_DAMP = float(_fc.get("trend_damp", 0.0))
