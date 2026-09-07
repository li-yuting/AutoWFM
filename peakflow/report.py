from __future__ import annotations

from pathlib import Path

import pandas as pd

from peakflow import config
from peakflow.forecast import total_by_date, weekly_summary


_DETAIL_COLS = ["client_vol", "inbound_low", "inbound", "inbound_high",
                "transfer_low", "transfer", "transfer_high"]


def _detail_row(d, label, vals, ratio):
    return {"日期": d.date(), "客户类型": label,
            "客户量": round(vals["client_vol"]),
            "咨询占比": ratio,
            "进线-悲观": round(vals["inbound_low"]), "进线-中性": round(vals["inbound"]),
            "进线-乐观": round(vals["inbound_high"]),
            "转人工-悲观": round(vals["transfer_low"]), "转人工-中性": round(vals["transfer"]),
            "转人工-乐观": round(vals["transfer_high"])}


def _detail_frame(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """长表 → 明细宽表：每日期 8 类型行 + 合计行。"""
    rows = []
    for d in sorted(forecast_df["date"].unique()):
        sub = forecast_df[forecast_df["date"] == d].set_index("client_type")
        for t in config.CLIENT_TYPES:
            r = sub.loc[t]
            rows.append(_detail_row(d, t, r, round(r["ratio"], 6)))
        rows.append(_detail_row(d, "合计", sub[_DETAIL_COLS].sum(), None))
    return pd.DataFrame(rows)


_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# (标签, 日视图列名, 周视图基准列名, 周视图三档系数)
_BANDS = [("进线-悲观", "inbound_low", "inbound", 0.9),
          ("进线-中性", "inbound", "inbound", 1.0),
          ("进线-乐观", "inbound_high", "inbound", 1.1),
          ("转人工-悲观", "transfer_low", "transfer", 0.9),
          ("转人工-中性", "transfer", "transfer", 1.0),
          ("转人工-乐观", "transfer_high", "transfer", 1.1)]


def _overview_frame(o_total, h_total, o_week, h_week) -> pd.DataFrame:
    rows = []
    daily = o_total.merge(h_total, on="date", suffixes=("_o", "_h")).sort_values("date")
    for r in daily.itertuples():
        row = {"日期": r.date.date(), "星期": _WEEKDAY_CN[r.date.weekday()]}
        for ch, suf in (("在线", "_o"), ("热线", "_h")):
            for label, o_col, _base, _k in _BANDS:
                row[f"{ch}{label}"] = round(getattr(r, f"{o_col}{suf}"))
        rows.append(row)
    weekly = o_week.merge(h_week, on="week", suffixes=("_o", "_h"))
    for r in weekly.itertuples():
        row = {"日期": f"周汇总 {r.week}", "星期": ""}
        for ch, suf in (("在线", "_o"), ("热线", "_h")):
            for label, _o_col, base, k in _BANDS:
                row[f"{ch}{label}"] = round(getattr(r, f"{base}{suf}") * k)
        rows.append(row)
    return pd.DataFrame(rows)


def _backtest_frame(sigmas) -> pd.DataFrame:
    rows = []
    for ch, sig in sigmas.items():
        for t in config.CLIENT_TYPES:
            v = sig[t]
            rows.append({"渠道": ch, "客户类型": t,
                         "σ-进线": round(v["sigma_in"], 2), "σ-转人工": round(v["sigma_tr"], 2),
                         "MAPE": f"{v['mape'] * 100:.1f}%"})
    return pd.DataFrame(rows)


def write_report(online_df: pd.DataFrame, hotline_df: pd.DataFrame,
                 sigmas: dict, meta: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    o_total = total_by_date(online_df)
    h_total = total_by_date(hotline_df)
    o_week = weekly_summary(o_total)
    h_week = weekly_summary(h_total)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        _overview_frame(o_total, h_total, o_week, h_week).to_excel(writer, sheet_name="总览", index=False)
        _detail_frame(online_df).to_excel(writer, sheet_name="在线-分类型明细", index=False)
        _detail_frame(hotline_df).to_excel(writer, sheet_name="热线-分类型明细", index=False)
        _backtest_frame(sigmas).to_excel(writer, sheet_name="回测误差", index=False)
        pd.DataFrame({"项": list(meta.keys()), "值": list(meta.values())}).to_excel(
            writer, sheet_name="运行说明", index=False)
    return out_path
