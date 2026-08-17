from __future__ import annotations

import numpy as np
import pandas as pd

from peakflow import config


def _trend_tail(series: pd.Series) -> pd.Series:
    """7 日中心滑动平均趋势，边缘用最近值填充。"""
    trend = series.astype(float).rolling(7, center=True).mean()
    return trend.ffill().bfill()


def _seasonal_index(series: pd.Series, trend: pd.Series) -> pd.Series:
    """按星期几(0-6)求 序列-趋势 的均值。"""
    resid = series.astype(float) - trend
    tmp = pd.DataFrame({"v": resid.values, "wd": resid.index.weekday})
    return tmp.groupby("wd")["v"].mean()


def _extrapolate(trend: pd.Series, horizon: int) -> np.ndarray:
    """对趋势尾部 TREND_FIT_DAYS 天线性拟合外推 horizon 步。"""
    n = len(trend)
    fit = min(config.TREND_FIT_DAYS, n)
    tail = trend.iloc[-fit:].values.astype(float)
    x = np.arange(fit, dtype=float)
    slope, intercept = np.polyfit(x, tail, 1)
    xf = np.arange(fit, fit + horizon, dtype=float)
    if config.TREND_DAMP > 0:
        steps = xf - (fit - 1)
        eff = slope * (config.TREND_DAMP ** steps)
        out = np.empty(horizon)
        out[0] = intercept + slope * (fit - 1) + eff[0]
        for i in range(1, horizon):
            out[i] = out[i - 1] + eff[i]
        return out
    return intercept + slope * xf


def forecast_client_volume(series: pd.Series, future_dates: list) -> np.ndarray:
    """客户量：趋势+周季节分解外推。返回长度=len(future_dates)、值>=0 的数组。"""
    s = series.dropna()
    if len(s) < 21:
        raise ValueError(f"客户量历史不足({len(s)} 天)，需要至少 21 天")
    s = s.sort_index()
    trend = _trend_tail(s)
    sidx = _seasonal_index(s, trend)
    tf = _extrapolate(trend, len(future_dates))
    out = np.array([tf[i] + sidx.get(future_dates[i].weekday(), 0.0)
                    for i in range(len(future_dates))])
    return np.maximum(out, 0.0)


def forecast_ratio(series: pd.Series, future_dates: list) -> np.ndarray:
    """咨询占比：对 log(r) 做趋势+周季节分解外推，再 exp。返回 (0,1] 数组。"""
    s = series.dropna()
    s = s[s > 0]
    if len(s) < 21:
        last = float(s.iloc[-1]) if len(s) else 0.0
        return np.clip(np.full(len(future_dates), last), 1e-9, 1.0)
    s = s.sort_index()
    ls = np.log(s)
    trend = _trend_tail(ls)
    sidx = _seasonal_index(ls, trend)
    tf = _extrapolate(trend, len(future_dates))
    out = np.exp(np.array([tf[i] + sidx.get(future_dates[i].weekday(), 0.0)
                           for i in range(len(future_dates))]))
    return np.clip(out, 1e-9, 1.0)


def mean_recent_ratio(series: pd.Series, window: int | None = None) -> float:
    """最近 window 天占比均值，clamp [0,1]。"""
    window = window or config.RATIO_WINDOW
    s = series.dropna()
    s = s[s > 0]
    if len(s) == 0:
        return 0.0
    v = float(s.iloc[-window:].mean())
    return float(np.clip(v, 0.0, 1.0))