# -*- coding: utf-8 -*-
"""requests 明细下载 + Excel 解析 + 按组计数。不保存 Excel。"""
import io
import warnings
import requests
import pandas as pd

def _match_group(value, groups):
    """把导出的组值按「包含」折叠到配置的规范组。
    同时命中多个组时取最长者；长度相同取匹配位置最靠前者（如 '贷后回访组一组'
    同时含 '贷后回访组'(pos 0) 与 '回访组一组'(pos 3) → '贷后回访组'）。
    无匹配返回 None；精确值必然命中（包含是精确的超集）。"""
    text = "" if value is None else str(value)
    has_ld = "贷后" in text
    best, best_len, best_pos = None, -1, -1
    for g in groups:
        if "贷后" in g and not has_ld:
            continue  # 业务规则：值不含「贷后」二字，不得命中贷后策略组
        pos = text.find(g)
        if pos < 0:
            continue
        if len(g) > best_len or (len(g) == best_len and pos < best_pos):
            best, best_len, best_pos = g, len(g), pos
    return best

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
            # 仅对 exclude_groups 内的组排除等值行，其余组全保留；
            # 前缀折叠后判断是否属于 exclude_groups，保证与 count_groups 同口径
            in_scope = d[gc].map(lambda v: _match_group(v, exc_groups)).notna()
            return d[~(eq_mask & in_scope)]
    return d[~eq_mask]

def count_groups(df, fcfg):
    gc = fcfg["group_column"]
    groups = fcfg["groups"]
    d = df
    if fcfg.get("channel_column"):
        d = d[d[fcfg["channel_column"]].isin(fcfg["channels"])]
    d = d.copy()                                   # 避免 SettingWithCopyWarning / 别名污染调用方
    d[gc] = d[gc].map(lambda v: _match_group(v, groups))  # '回访组一组-25' → '回访组一组'，无匹配 → None
    d = d[d[gc].notna()]                           # 丢弃非成员（替代原 .isin 过滤）
    d = _apply_row_exclude(d, fcfg)
    cnt = d.groupby(gc).size().to_dict()
    return {g: int(cnt.get(g, 0)) for g in groups}

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
