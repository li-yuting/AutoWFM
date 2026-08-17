from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from peakflow import config

_TEMPLATE = Path(__file__).with_name("dashboard_template.html")
_INNER_BANNER = "<!-- ===="  # 内页源文档起始横幅（构建时定位，不进入输出）
_SRCDOC_PLACEHOLDER = "__SRCDOC__"


def _iso_date(value) -> str:
    return pd.Timestamp(value).date().isoformat()


def _history_records(history: pd.DataFrame) -> list[dict]:
    totals = (history.groupby("date", as_index=False)[["inbound", "transfer"]].sum()
              .sort_values("date"))
    return [{"d": _iso_date(row.date), "in": round(float(row.inbound)),
             "tr": round(float(row.transfer))}
            for row in totals.itertuples()]


def _band_records(forecast: pd.DataFrame, low_col: str, mid_col: str,
                  high_col: str) -> list[dict]:
    """每日合计三档区间，输出字段统一为 low/mid/high（进线与转人工通用）。"""
    totals = (forecast.groupby("date", as_index=False)[[low_col, mid_col, high_col]].sum()
              .sort_values("date"))
    return [{"d": _iso_date(row.date),
             "low": round(float(getattr(row, low_col))),
             "mid": round(float(getattr(row, mid_col))),
             "high": round(float(getattr(row, high_col)))}
            for row in totals.itertuples()]


def _type_records(forecast: pd.DataFrame, column: str) -> list[dict]:
    rows = []
    for row in forecast.sort_values(["date", "client_type"]).itertuples():
        value = getattr(row, column)
        rows.append({"d": _iso_date(row.date), "t": row.client_type,
                     "v": round(float(value), 6)})
    return rows


def build_dashboard_data(histories: dict[str, pd.DataFrame],
                         forecasts: dict[str, pd.DataFrame],
                         meta: dict) -> dict:
    first_history = next(iter(histories.values()))
    first_forecast = next(iter(forecasts.values()))
    data = {
        "meta": {
            "data_until": _iso_date(first_history["date"].max()),
            "horizon": int(first_forecast["date"].nunique()),
            "history_days": int(first_history["date"].nunique()),
            "types": list(config.CLIENT_TYPES),
            "generated": meta.get("生成时间", ""),
        },
        "channels": {},
    }
    for channel in ("在线", "热线"):
        history = histories[channel]
        forecast = forecasts[channel]
        data["channels"][channel] = {
            "history": _history_records(history),
            "forecast": _band_records(forecast, "inbound_low", "inbound", "inbound_high"),
            "transfer": _band_records(forecast, "transfer_low", "transfer", "transfer_high"),
            "client_vol": _type_records(forecast, "client_vol"),
            "ratio_fc": _type_records(forecast, "ratio"),
        }
    return data


def write_dashboard(data: dict, out_path: Path,
                    template_path: Path = _TEMPLATE) -> Path:
    """渲染网页：读模板外壳，把内页源文档注入 JSON 后整体 html.escape 进
    <iframe srcdoc>。模板内页按普通 HTML/JS 编写（不再手工转义）。"""
    template = Path(template_path).read_text(encoding="utf-8")
    banner = template.index(_INNER_BANNER)  # 外壳结束 / 内页源开始
    outer = template[:banner]
    rest = template[banner:]
    inner = rest[rest.index("<!doctype html>"):].rstrip("\r\n")
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    fd_placeholder = "var FD = __FD_PAYLOAD__;"
    if inner.count(fd_placeholder) != 1:
        raise ValueError(f"网页模板缺少唯一数据占位位置: {template_path}")
    inner = inner.replace(fd_placeholder, f"var FD = {raw};", 1)
    escaped = html.escape(inner, quote=True)
    if outer.count(_SRCDOC_PLACEHOLDER) != 1:
        raise ValueError(f"网页模板缺少 iframe 占位位置: {template_path}")
    rendered = outer.replace(_SRCDOC_PLACEHOLDER, escaped, 1)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return out_path