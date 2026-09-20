# -*- coding: utf-8 -*-
"""指标口径与取值：看板聚合(queries)、企微报表(notify)共用的纯函数。

**为什么单独一个模块**：`流入率`/`接通率` 的百分比格式、以及卡片(card)的组装，
原先在 dashboard/queries.py（日/月视图各写一遍）与 collector/notify.py（markdown 再算一遍）
里各有一份实现，且两处的「分母为 0」口径还不一致（一个给 None、一个给 "0.00%"）。
口径要改就得同时改多处，极易漏。现在只在这里定义一次。

另含「预估流入量.csv」的带缓存读取：一次看板请求要按线路/时间窗反复取同一份 CSV，
按 (mtime, size) 缓存解析结果，避免重复解析。

只依赖标准库，可被 collector / dashboard / api 任意层导入（不反向依赖它们，无循环）。
"""
from __future__ import annotations

import csv
import threading
from pathlib import Path

# ---- 百分比 ----

def pct(num, den, fallback=None):
    """num/den 的百分比字符串(2 位小数)。

    den 为 0/None、或 num 为 None 时返回 fallback——看板传 None(表格渲染成空档)，
    企微报表传 "0.00%"(markdown 里不能出现 None)。
    """
    if num is None or not den:
        return fallback
    return f"{num / den * 100:.2f}%"


# ---- 呼入卡片 ----

_CARD_NUM_KEYS = ("预测量", "时段预测量", "转人工量", "转人工成功量")


def card_in(pred=None, cum=None, zrg=None, succ=None):
    """呼入侧卡片。字段序即看板模板读取序，不要改。

    pred=全天预测量, cum=截止当前的时段预测量, zrg=转人工量, succ=转人工成功量。
    """
    return {
        "预测量": pred, "时段预测量": cum,
        "流入率": pct(zrg, cum),
        "转人工量": zrg, "转人工成功量": succ,
        "接通率": pct(succ, zrg),
    }


def merge_cards(cards):
    """多组呼入卡片 -> 合计卡片：各数值字段求和后按同一口径重算两个率。

    日视图(各组累计最新)与月视图(各组当日收盘值求和)共用；缺值当 0。
    """
    total = {k: sum((c.get(k) or 0) for c in cards) for k in _CARD_NUM_KEYS}
    return card_in(total["预测量"], total["时段预测量"],
                   total["转人工量"], total["转人工成功量"])


def outbound_card(groups, seat_total):
    """外呼侧卡片：total 的量类字段由各组求和；签入/空闲不在本函数算——
    日视图是「各组当前值相加」、月视图是「各日均值再平均」，口径不同，由调用方给 seat_total。
    """
    return {
        "total": {
            "工单量": sum((g.get("工单量") or 0) for g in groups.values()),
            "转接量": sum((g.get("转接量") or 0) for g in groups.values()),
            "签入": seat_total.get("签入"), "空闲": seat_total.get("空闲"),
        },
        "groups": groups,
    }


# ---- 预估流入量.csv（带缓存）----

_FORECAST_NAME = "预估流入量.csv"
_CSV_LOCK = threading.Lock()
# path(str) -> ((mtime_ns, size), [(时间, 线路, 时段预估量, 累计预估量), ...])
_CSV_CACHE: dict[str, tuple[tuple[int, int], list[tuple[str, str, str, str]]]] = {}
_CSV_CACHE_MAX = 8   # 生产环境只有 1 份；上限只为兜住测试里的大量临时目录


def _parse_forecast(path: Path) -> list[tuple[str, str, str, str]]:
    """读成 (时间, 线路, 时段预估量, 累计预估量)，字段保持字符串。

    不在这里 int()：与旧实现一致——只有真正命中「线路/日期」的行才会被转成数字，
    这样某一行脏数据不会因为「跟本次查询无关」而把整次请求带崩。
    """
    with open(path, encoding="utf-8", newline="") as f:
        return [(r.get("时间"), r.get("线路"), r.get("时段预估量"), r.get("累计预估量"))
                for r in csv.DictReader(f)]


def forecast_rows(data_dir):
    """预估流入量.csv 的原始行（字段为字符串）。文件不存在返回 []。

    按 (mtime_ns, size) 失效：文件没动就直接复用上次解析结果。
    """
    path = Path(data_dir) / _FORECAST_NAME
    try:
        st = path.stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    name = str(path)
    with _CSV_LOCK:
        hit = _CSV_CACHE.get(name)
        if hit and hit[0] == key:
            return hit[1]
    rows = _parse_forecast(path)
    with _CSV_LOCK:
        if name not in _CSV_CACHE and len(_CSV_CACHE) >= _CSV_CACHE_MAX:
            _CSV_CACHE.pop(next(iter(_CSV_CACHE)))   # 淘汰最早写入的一项
        _CSV_CACHE[name] = (key, rows)
    return rows


def forecast_at(data_dir, line, ts):
    """某线路在「恰好等于 ts」那一行的累计预估量；未命中返回 0。

    推送(notify)按当前整点取累计值用。
    """
    for t, ln, _inc, cum in forecast_rows(data_dir):
        if ln == line and t == ts:
            try:
                return int(cum)
            except (TypeError, ValueError):
                return 0
    return 0
