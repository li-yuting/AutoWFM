# -*- coding: utf-8 -*-
"""指标口径与取值的测试：百分比、卡片合并、预估流入量 CSV 的缓存读取。

口径在这里定义一次、被看板(dashboard.queries)与企微推送(collector.notify)共用，
所以除了算得对，还要保证「同一个文件只解析一次」——这正是本次抽模块要解决的问题。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import metrics

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_repository.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d


def _seed_csv(d, rows):
    with open(Path(d) / "预估流入量.csv", "w", encoding="utf-8", newline="") as f:
        f.write("时间,线路,时段预估量,累计预估量\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def test_pct():
    """2 位小数；分母为 0/None 或分子为 None 时给 fallback（两处消费方 fallback 不同）。"""
    assert metrics.pct(1108, 1187) == "93.34%"
    assert metrics.pct(0, 20) == "0.00%"          # 分子 0 但分母有值 -> 真的 0%
    assert metrics.pct(340, 345) == "98.55%"
    # 看板口径：无分母给 None(模板渲染成空档)
    assert metrics.pct(5, 0) is None
    assert metrics.pct(5, None) is None
    assert metrics.pct(None, 5) is None
    # 企微口径：同一个函数传 fallback="0.00%"
    assert metrics.pct(5, 0, "0.00%") == "0.00%"
    assert metrics.pct(None, 5, "0.00%") == "0.00%"


def test_card_in():
    """卡片字段集与字段序（模板按序读取，不要改）。"""
    c = metrics.card_in(1000, 500, 400, 380)
    assert list(c) == ["预测量", "时段预测量", "流入率", "转人工量", "转人工成功量", "接通率"], list(c)
    assert c["流入率"] == "80.00%"     # 400/500
    assert c["接通率"] == "95.00%"     # 380/400


def test_merge_cards():
    """合计卡片 = 各组同字段求和后重算两个率（日/月视图共用）。"""
    groups = [
        metrics.card_in(2000, 230, 100, 95),
        metrics.card_in(1500, 115, 200, 195),
        metrics.card_in(0, 0, 40, 38),
    ]
    t = metrics.merge_cards(groups)
    assert t["预测量"] == 3500 and t["时段预测量"] == 345
    assert t["转人工量"] == 340 and t["转人工成功量"] == 328
    assert t["流入率"] == "98.55%"     # 340/345
    assert t["接通率"] == "96.47%"     # 328/340
    # 缺值当 0，不因 None 抛异常
    empty = metrics.merge_cards([metrics.card_in(), metrics.card_in()])
    assert empty["预测量"] == 0 and empty["流入率"] is None
    assert metrics.merge_cards([])["转人工量"] == 0


def test_outbound_card():
    """外呼卡片：量类字段由各组求和；签入/空闲按调用方给的口径（日=相加、月=均值）原样带入。"""
    groups = {
        "常规二线": {"工单量": 34, "转接量": 20, "签入": 12, "空闲": 0},
        "贷后二线": {"工单量": 44, "转接量": 20, "签入": 15, "空闲": 4},
        "二线客诉": {"工单量": 10},     # 只有工单量
    }
    card = metrics.outbound_card(groups, {"签入": 27, "空闲": 4})
    assert card["groups"] is groups, "分组原样透传，不做拷贝"
    assert list(card["total"]) == ["工单量", "转接量", "签入", "空闲"], list(card["total"])
    assert card["total"]["工单量"] == 88     # 34+44+10
    assert card["total"]["转接量"] == 40     # 20+20（二线客诉无此项 -> 0）
    assert card["total"]["签入"] == 27 and card["total"]["空闲"] == 4
    # 月视图口径：签入传各日均值
    card2 = metrics.outbound_card({"常规二线": {"工单量": 1}}, {"签入": 30.0, "空闲": 2.5})
    assert card2["total"]["签入"] == 30.0 and card2["total"]["空闲"] == 2.5


def test_forecast_rows_missing():
    """没有 CSV 时返回 []，不抛异常。"""
    assert metrics.forecast_rows(_tmp()) == []


def test_forecast_rows_cached():
    """文件没变 -> 只解析一次；文件变了 -> 重新解析并且拿到新内容。"""
    d = _tmp()
    _seed_csv(d, [("2026-07-28 11:00", "热线", 100, 1187)])
    calls = {"n": 0}
    orig = metrics._parse_forecast

    def counting(path):
        calls["n"] += 1
        return orig(path)

    metrics._parse_forecast = counting
    try:
        metrics._CSV_CACHE.clear()
        first = metrics.forecast_rows(d)
        assert calls["n"] == 1, calls
        # 第二次/第三次读同一份未改动的文件 -> 命中缓存，不再解析
        assert metrics.forecast_rows(d) == first
        assert metrics.forecast_rows(d) == first
        assert calls["n"] == 1, f"未改动应命中缓存, 实际解析 {calls['n']} 次"
        assert first[0][:2] == ("2026-07-28 11:00", "热线"), first
        # 改写文件 -> 失效重解析
        _seed_csv(d, [("2026-07-28 11:00", "热线", 100, 999),
                      ("2026-07-28 11:15", "热线", 10, 1009)])
        new = metrics.forecast_rows(d)
        assert calls["n"] == 2, f"文件改动后应重新解析, 实际 {calls['n']} 次"
        assert len(new) == 2 and new[0][3] == "999", new
    finally:
        metrics._parse_forecast = orig
        metrics._CSV_CACHE.clear()


def test_forecast_at():
    """按「线路 + 完整时间戳」精确取值；未命中 0；脏值 0（企微推送用）。"""
    d = _tmp()
    _seed_csv(d, [("2026-07-28 11:00", "热线", 100, 1187),
                  ("2026-07-28 11:00", "在线", 50, 811),
                  ("2026-07-28 11:15", "热线", 10, "abc")])
    metrics._CSV_CACHE.clear()
    assert metrics.forecast_at(d, "热线", "2026-07-28 11:00") == 1187
    assert metrics.forecast_at(d, "在线", "2026-07-28 11:00") == 811
    assert metrics.forecast_at(d, "热线", "2026-07-28 12:00") == 0      # 无该时间戳
    assert metrics.forecast_at(d, "贷后", "2026-07-28 11:00") == 0      # 无该线路
    assert metrics.forecast_at(d, "热线", "2026-07-28 11:15") == 0      # 累计量脏值
    assert metrics.forecast_at(_tmp(), "热线", "2026-07-28 11:00") == 0  # 无 CSV


def main():
    test_pct()
    test_card_in()
    test_merge_cards()
    test_outbound_card()
    test_forecast_rows_missing()
    test_forecast_rows_cached()
    test_forecast_at()
    import shutil
    shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("metrics OK")


if __name__ == "__main__":
    main()
