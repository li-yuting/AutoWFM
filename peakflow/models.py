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


def _dom_seasonal_index(resid: pd.Series, window: int) -> pd.Series:
    """按「几号(1-31)」对残差取均值，再做 window 天中心平滑(边缘缩窗)。"""
    tmp = pd.DataFrame({"v": resid.values, "dom": resid.index.day})
    idx = tmp.groupby("dom")["v"].mean().reindex(range(1, 32))
    return idx.rolling(window, center=True, min_periods=1).mean().ffill().bfill()


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


def forecast_client_volumes(history_df: pd.DataFrame, future_dates: list) -> dict[str, np.ndarray]:
    """客户量预测：flat 总量 × 份额(forecast_ratio + 归一化)。

    返回 {client_type: np.ndarray(len(future_dates))}，值 >= 0；总量守恒。
    """
    total = history_df.groupby("date")["client_count"].sum().sort_index()
    recent = total.iloc[-config.TOTAL_WINDOW:]
    if len(recent) == 0 or recent.mean() <= 0:
        return {t: np.zeros(len(future_dates)) for t in config.CLIENT_TYPES}
    flat = float(recent.mean())
    shares = {}
    for t in config.CLIENT_TYPES:
        sub = history_df[history_df["client_type"] == t].set_index("date").sort_index()
        share = sub["client_count"].divide(total).replace([np.inf, -np.inf], np.nan)
        shares[t] = forecast_ratio(share, future_dates, use_month_seasonal=False)
    out = {t: np.empty(len(future_dates)) for t in config.CLIENT_TYPES}
    for i in range(len(future_dates)):
        s = sum(shares[t][i] for t in config.CLIENT_TYPES)
        for t in config.CLIENT_TYPES:
            out[t][i] = flat * (shares[t][i] / s) if s > 0 else 0.0
    return out


def forecast_ratio(series: pd.Series, future_dates: list,
                   use_month_seasonal: bool = False) -> np.ndarray:
    """咨询占比：对 log(r) 做趋势+周季节分解外推，再 exp。返回 (0,1] 数组。

    use_month_seasonal=True 时叠加月内日序季节项(仅 M2-M3/M3+ 等账龄类型)。
    """
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
    dom_idx = None
    if use_month_seasonal:
        n_months = s.index.to_period("M").nunique()
        if n_months >= config.DOM_MIN_MONTHS:
            wd = pd.Series([sidx.get(d.weekday(), 0.0) for d in ls.index],
                           index=ls.index)
            resid = ls - trend - wd
            dom_idx = _dom_seasonal_index(resid, config.DOM_SMOOTH_WINDOW)
    out = np.empty(len(future_dates))
    for i in range(len(future_dates)):
        d = future_dates[i]
        v = tf[i] + sidx.get(d.weekday(), 0.0)
        if dom_idx is not None:
            v += float(dom_idx.get(d.day, 0.0))
        out[i] = v
    return np.clip(np.exp(out), 1e-9, 1.0)


def mean_recent_ratio(series: pd.Series, window: int | None = None) -> float:
    """最近 window 天占比均值，clamp [0,1]。"""
    window = window or config.RATIO_WINDOW
    s = series.dropna()
    s = s[s > 0]
    if len(s) == 0:
        return 0.0
    v = float(s.iloc[-window:].mean())
    return float(np.clip(v, 0.0, 1.0))
