from __future__ import annotations

from pathlib import Path

import pandas as pd

from peakflow import config
from peakflow.forecast import total_by_date, weekly_summary


def _detail_frame(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """长表 → 明细宽表：每日期 8 类型行 + 合计行。"""
    rows = []
    for d in sorted(forecast_df["date"].unique()):
        sub = forecast_df[forecast_df["date"] == d].set_index("client_type")
        for t in config.CLIENT_TYPES:
            r = sub.loc[t]
            rows.append({"日期": d.date(), "客户类型": t,
                         "客户量": round(r["client_vol"]),
                         "咨询占比": round(r["ratio"], 6),
                         "进线-悲观": round(r["inbound_low"]), "进线-中性": round(r["inbound"]),
                         "进线-乐观": round(r["inbound_high"]),
                         "转人工-悲观": round(r["transfer_low"]), "转人工-中性": round(r["transfer"]),
                         "转人工-乐观": round(r["transfer_high"])})
        agg = {c: sum(sub[c]) for c in
               ["client_vol", "inbound_low", "inbound", "inbound_high",
                "transfer_low", "transfer", "transfer_high"]}
        rows.append({"日期": d.date(), "客户类型": "合计",
                     "客户量": round(agg["client_vol"]),
                     "咨询占比": None,
                     "进线-悲观": round(agg["inbound_low"]), "进线-中性": round(agg["inbound"]),
                     "进线-乐观": round(agg["inbound_high"]),
                     "转人工-悲观": round(agg["transfer_low"]), "转人工-中性": round(agg["transfer"]),
                     "转人工-乐观": round(agg["transfer_high"])})
    return pd.DataFrame(rows)


def _overview_frame(o_total, h_total, o_week, h_week) -> pd.DataFrame:
    rows = []
    o_by = {d: r for d, r in zip(o_total["date"], o_total.iterrows())}
    h_by = {d: r for d, r in zip(h_total["date"], h_total.iterrows())}
    for d in sorted(o_total["date"]):
        o = o_by[d][1]
        h = h_by[d][1]
        rows.append({"日期": d.date(), "星期": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()],
                     "在线进线-悲观": round(o["inbound_low"]), "在线进线-中性": round(o["inbound"]),
                     "在线进线-乐观": round(o["inbound_high"]),
                     "在线转人工-悲观": round(o["transfer_low"]), "在线转人工-中性": round(o["transfer"]),
                     "在线转人工-乐观": round(o["transfer_high"]),
                     "热线进线-悲观": round(h["inbound_low"]), "热线进线-中性": round(h["inbound"]),
                     "热线进线-乐观": round(h["inbound_high"]),
                     "热线转人工-悲观": round(h["transfer_low"]), "热线转人工-中性": round(h["transfer"]),
                     "热线转人工-乐观": round(h["transfer_high"])})
    df = pd.DataFrame(rows)
    week_rows = []
    o_week_by = {w: r for w, r in zip(o_week["week"], o_week.iterrows())}
    h_week_by = {w: r for w, r in zip(h_week["week"], h_week.iterrows())}
    for w in o_week["week"]:
        o = o_week_by[w][1]
        h = h_week_by[w][1]
        week_rows.append({"日期": f"周汇总 {w}", "星期": "",
                           "在线进线-悲观": round(o["inbound"] * 0.9), "在线进线-中性": round(o["inbound"]),
                           "在线进线-乐观": round(o["inbound"] * 1.1),
                           "在线转人工-悲观": round(o["transfer"] * 0.9), "在线转人工-中性": round(o["transfer"]),
                           "在线转人工-乐观": round(o["transfer"] * 1.1),
                           "热线进线-悲观": round(h["inbound"] * 0.9), "热线进线-中性": round(h["inbound"]),
                           "热线进线-乐观": round(h["inbound"] * 1.1),
                           "热线转人工-悲观": round(h["transfer"] * 0.9), "热线转人工-中性": round(h["transfer"]),
                           "热线转人工-乐观": round(h["transfer"] * 1.1)})
    return pd.concat([df, pd.DataFrame(week_rows)], ignore_index=True)


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