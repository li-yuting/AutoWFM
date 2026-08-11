# -*- coding: utf-8 -*-
"""requests 明细下载 + Excel 解析 + 按组计数。不保存 Excel。"""
import io
import warnings
import requests
import pandas as pd

def _apply_row_exclude(d, fcfg):
    """行级排除：filter.row_exclude.eq_columns 列出的两列值相等则剔除该行。
    可选 exclude_groups：仅对这些组应用排除（其余组保留全部行）。
    未配置或配置不完整则原样返回（向后兼容）。"""
    rc = fcfg.get("row_exclude")
    if not rc:
        return d
    cols = rc.get("eq_columns")
    if not cols or len(cols) < 2:
        return d
    c0, c1 = cols[0], cols[1]
    if c0 not in d.columns or c1 not in d.columns:
        return d  # 列不存在时不过滤，避免 KeyError 中断采集
    eq_mask = d[c0] == d[c1]  # 两列相等
    exc_groups = rc.get("exclude_groups")
    if exc_groups:
        gc = fcfg.get("group_column")
        if gc and gc in d.columns:
            # 仅对 exclude_groups 内的组排除等值行，其余组全保留
            return d[~(eq_mask & d[gc].isin(exc_groups))]
    return d[~eq_mask]

def count_groups(df, fcfg):
    d = df
    if fcfg.get("channel_column"):
        d = d[d[fcfg["channel_column"]].isin(fcfg["channels"])]
    d = d[d[fcfg["group_column"]].isin(fcfg["groups"])]
    d = _apply_row_exclude(d, fcfg)
    cnt = d.groupby(fcfg["group_column"]).size().to_dict()
    return {g: int(cnt.get(g, 0)) for g in fcfg["groups"]}

class EmptyDownloadError(Exception):
    """下载的 Excel 无有效数据行(空表),避免覆盖真实数据。"""

def _parse_excel(content):
    if content[:4] == b'PK\x03\x04':
        engine = "openpyxl"
    elif content[:8] == b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1':
        engine = "xlrd"
    else:
        raise ValueError(f"非Excel文件: {content[:20]!r}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # openpyxl "no default style" 噪声
        return pd.read_excel(io.BytesIO(content), header=2, engine=engine)

def download_and_count(mode_name, mcfg, secrets, today_str, timeout):
    data = dict(mcfg["data"])
    data["token"] = secrets["token"]
    data["tenementId"] = secrets["tenementId"]
    dv = today_str if mcfg["date_format"] == "%Y-%m-%d" else f"{today_str} 00:00:00"
    data[mcfg["date_fields"]["start"]] = dv
    data[mcfg["date_fields"]["end"]] = dv
    resp = requests.post(mcfg["url"], json=data, timeout=timeout)
    resp.raise_for_status()
    df = _parse_excel(resp.content)
    if df.empty:
        raise EmptyDownloadError(f"{mode_name} 下载的 Excel 为空表({today_str})")
    return count_groups(df, mcfg["filter"])
