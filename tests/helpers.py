import numpy as np
import pandas as pd

from peakflow import config

_WEEKDAY_SEAS = np.array([0.0, 10.0, 12.0, 11.0, 9.0, -10.0, -20.0])


def make_series(n_days: int = 70, level: float = 100.0, slope: float = 0.0) -> pd.Series:
    """确定性周季节 + 线性趋势序列，index 为 DatetimeIndex（从 2026-06-01 起）。"""
    idx = pd.date_range("2026-06-01", periods=n_days)
    trend = level + np.arange(n_days) * slope
    vals = trend + _WEEKDAY_SEAS[idx.weekday]
    return pd.Series(vals, index=idx)


def make_history(n_days: int = 35, transfer_rate: float = 0.30) -> pd.DataFrame:
    """构造覆盖全部 CLIENT_TYPES 的长表历史，列与 loader 输出一致。"""
    idx = pd.date_range("2026-06-01", periods=n_days)
    rows = []
    for t in config.CLIENT_TYPES:
        base = make_series(n_days, level=1000.0 + config.CLIENT_TYPES.index(t) * 500.0, slope=0.3)
        ratio = np.clip(np.linspace(0.05, 0.02, n_days) + config.CLIENT_TYPES.index(t) * 0.0005,
                        1e-4, 1.0)
        inbound = base.values * ratio
        transfer = inbound * transfer_rate
        for i, d in enumerate(idx):
            rows.append({"date": d, "client_type": t,
                         "client_count": float(base.values[i]),
                         "inbound": float(inbound[i]),
                         "transfer": float(transfer[i])})
    return pd.DataFrame(rows)
