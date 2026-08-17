from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from peakflow import config

_ENCODINGS = ["utf-16", "utf-8-sig", "gbk"]


def detect_encoding(path: Path) -> str:
    for enc in _ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as f:
                f.read(64)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法识别文件编码: {path}")


def _to_num(s: pd.Series) -> pd.Series:
    return (s.astype(str).str.replace(",", "", regex=False)
             .str.strip().replace("", np.nan).astype(float))


def load_channel_data(path: Path) -> pd.DataFrame:
    path = Path(path)
    enc = detect_encoding(path)
    raw = pd.read_csv(path, sep="\t", encoding=enc, dtype=str)
    raw["date"] = pd.to_datetime(raw.iloc[:, 0])
    raw["client_type"] = raw.iloc[:, 1].astype(str).str.strip()

    def find(*keys):
        for k in keys:
            if k in raw.columns:
                return k
        raise ValueError(f"{path} 缺少必需列，需要包含其中之一: {keys}")

    c_cnt = find("客户量-实际", "客户量")
    c_in = find("进线次数-实际", "进线次数")
    c_tr = find("转人工次数-实际", "转人工次数")

    df = pd.DataFrame({
        "date": raw["date"],
        "client_type": raw["client_type"],
        "client_count": _to_num(raw[c_cnt]),
        "inbound": _to_num(raw[c_in]),
        "transfer": _to_num(raw[c_tr]),
    })
    df = df[df["client_type"] != config.TOTAL_TYPE].reset_index(drop=True)
    _validate(df, raw, path)
    return df


def _validate(df: pd.DataFrame, raw: pd.DataFrame, path: Path) -> None:
    for d, sub in df.groupby("date"):
        have = set(sub["client_type"])
        miss = set(config.CLIENT_TYPES) - have
        if miss:
            raise ValueError(f"{path} {d.date()} 缺少客户类型: {sorted(miss)}")
    totals = raw[raw["client_type"] == config.TOTAL_TYPE]
    for _, trow in totals.iterrows():
        d = trow["date"]
        sub = df[df["date"] == d]
        if sub["client_count"].isna().all():
            continue
        exp = float(str(trow.iloc[2]).replace(",", ""))
        if abs(sub["client_count"].sum() - exp) > 1e-3:
            raise ValueError(
                f"{path} {d.date()} 客户量合计不一致: 分项和={sub['client_count'].sum():.0f} 合计={exp:.0f}")