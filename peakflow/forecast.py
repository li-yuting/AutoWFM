from __future__ import annotations

import numpy as np
import pandas as pd

from peakflow import config
from peakflow.models import forecast_client_volumes, forecast_ratio, mean_recent_ratio


def _uses_month_seasonal(client_type: str) -> bool:
    """该客户类型是否启用月内日序季节项(仅 M2-M3/M3+ 等账龄类型)。"""
    return client_type in config.MONTH_SEASONAL_TYPES


def point_forecast(history_df: pd.DataFrame, future_dates: list) -> pd.DataFrame:
    cvs = forecast_client_volumes(history_df, future_dates)
    rows = []
    for t in config.CLIENT_TYPES:
        sub = history_df[history_df["client_type"] == t].set_index("date").sort_index()
        cv = cvs[t]
        r_series = sub["inbound"] / sub["client_count"].replace(0, np.nan)
        rf = forecast_ratio(r_series, future_dates,
                            use_month_seasonal=_uses_month_seasonal(t))
        tr = mean_recent_ratio(sub["transfer"] / sub["inbound"].replace(0, np.nan))
        for i, d in enumerate(future_dates):
            inbound = float(cv[i] * rf[i])
            rows.append({"date": d, "client_type": t,
                         "client_vol": float(cv[i]), "ratio": float(rf[i]),
                         "inbound": inbound, "transfer": inbound * tr})
    return pd.DataFrame(rows)


def total_by_date(forecast_df: pd.DataFrame) -> pd.DataFrame:
    g = forecast_df.groupby("date")
    agg_cols = ["inbound", "transfer", "inbound_low", "inbound_high",
                "transfer_low", "transfer_high"]
    agg_cols = [c for c in agg_cols if c in forecast_df.columns]
    return g[agg_cols].sum().reset_index()


def weekly_summary(total_df: pd.DataFrame) -> pd.DataFrame:
    tmp = total_df.copy()
    idx = pd.DatetimeIndex(tmp["date"])
    iso = idx.isocalendar()
    tmp["week"] = [f"{y}-W{w:02d}" for y, w in zip(iso.year, iso.week)]
    g = tmp.groupby("week")
    return g[["inbound", "transfer"]].sum().reset_index()


def backtest_sigma(history_df: pd.DataFrame) -> dict:
    """滚动起点回测最近 BACKTEST_WINDOW 天，返回每类型 {sigma_in, sigma_tr, mape}。"""
    dates = sorted(history_df["date"].unique())
    back = dates[-config.BACKTEST_WINDOW:]
    res_in = {t: [] for t in config.CLIENT_TYPES}
    res_tr = {t: [] for t in config.CLIENT_TYPES}
    mape_num = {t: [] for t in config.CLIENT_TYPES}
    mape_den = {t: [] for t in config.CLIENT_TYPES}
    sub_by_type = {t: history_df[history_df["client_type"] == t].set_index("date").sort_index()
                   for t in config.CLIENT_TYPES}
    for d in back:
        train_df = history_df[history_df["date"] < d]
        if train_df["date"].nunique() < 21:
            continue
        cvs = forecast_client_volumes(train_df, [d])
        for t in config.CLIENT_TYPES:
            sub = sub_by_type[t]
            train = sub[sub.index < d]
            if len(train) < 21:
                continue
            fv = cvs[t][0]
            rs = train["inbound"] / train["client_count"].replace(0, np.nan)
            fr = forecast_ratio(rs, [d], use_month_seasonal=_uses_month_seasonal(t))[0]
            tr = mean_recent_ratio(train["transfer"] / train["inbound"].replace(0, np.nan))
            pred_in = fv * fr
            pred_tr = pred_in * tr
            act_in = float(sub.loc[d, "inbound"])
            act_tr = float(sub.loc[d, "transfer"])
            res_in[t].append(abs(pred_in - act_in))
            res_tr[t].append(abs(pred_tr - act_tr))
            if act_in > 0:
                mape_num[t].append(abs(pred_in - act_in))
                mape_den[t].append(act_in)
    sigma = {}
    for t in config.CLIENT_TYPES:
        si = float(np.std(res_in[t])) if len(res_in[t]) >= 3 else 0.0
        st = float(np.std(res_tr[t])) if len(res_tr[t]) >= 3 else 0.0
        mape = float(np.mean(np.array(mape_num[t]) / np.array(mape_den[t]))) if mape_den[t] else 0.0
        sigma[t] = {"sigma_in": si, "sigma_tr": st, "mape": mape}
    return sigma


def three_band_forecast(history_df: pd.DataFrame, future_dates: list) -> tuple[pd.DataFrame, dict]:
    base = point_forecast(history_df, future_dates)
    sigma = backtest_sigma(history_df)
    k = config.SIGMA_K
    low_in, high_in = [], []
    low_tr, high_tr = [], []
    for _, row in base.iterrows():
        si = sigma[row["client_type"]]["sigma_in"]
        st = sigma[row["client_type"]]["sigma_tr"]
        low_in.append(max(0.0, row["inbound"] - k * si))
        high_in.append(row["inbound"] + k * si)
        low_tr.append(max(0.0, row["transfer"] - k * st))
        high_tr.append(row["transfer"] + k * st)
    out = base.copy()
    out["inbound_low"] = low_in
    out["inbound_high"] = high_in
    out["transfer_low"] = low_tr
    out["transfer_high"] = high_tr
    return out, sigma
