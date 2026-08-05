# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile
from collector import backfill, storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_iter_days():
    assert backfill.iter_days("2026-07-15", "2026-07-15") == ["2026-07-15"]
    assert backfill.iter_days("2026-07-15", "2026-07-17") == ["2026-07-15", "2026-07-16", "2026-07-17"]
    assert backfill.iter_days("2026-07-31", "2026-08-02") == ["2026-07-31", "2026-08-01", "2026-08-02"]

def test_day_ops():
    d = tempfile.mkdtemp()
    storage.insert("工单明细", {"时间": "2026-07-15 09:00", "二线客诉处理组": 0,
        "常规工单处理组": 0, "回访组一组": 0, "贷后回访组": 0, "12378回访组": 0,
        "转接一组": 1, "转接二组": 0, "贷后转接组": 0}, d)
    assert backfill.day_row_count("工单明细", "2026-07-15", d) == 1
    assert backfill.day_row_count("工单明细", "2026-07-16", d) == 0
    backfill.clear_day("工单明细", "2026-07-15", d)
    assert backfill.day_row_count("工单明细", "2026-07-15", d) == 0

def test_build_snapshots_gongdan():
    import pandas as pd
    df = pd.DataFrame({
        "创建日期": ["2026-07-15 09:02:00", "2026-07-15 09:07:00", "2026-07-15 09:12:00",
                  "2026-07-15 10:00:00", "2026-07-15 23:30:00"],
        "接收组": ["转接一组", "转接一组", "转接二组", "转接一组", "转接一组"],
    })
    fcfg = {"group_column": "接收组", "groups": ["转接一组", "转接二组"]}
    rows, total = backfill.build_snapshots(df, "2026-07-15", fcfg, ["转接一组", "转接二组"],
                                           "创建日期", "%Y-%m-%d %H:%M:%S", "09:00", "21:04")
    assert len(rows) == 146, len(rows)  # 09:00..21:00 共 145 刻度 + 23:59
    assert rows[0]["时间"] == "2026-07-15 09:00"
    assert rows[0]["转接一组"] == 0 and rows[0]["转接二组"] == 0
    assert rows[1]["时间"] == "2026-07-15 09:05" and rows[1]["转接一组"] == 1, rows[1]
    assert rows[2]["转接一组"] == 2, rows[2]              # <09:10 = 09:02+09:07
    assert rows[3]["转接一组"] == 2 and rows[3]["转接二组"] == 1, rows[3]  # <09:15 含 09:12
    assert rows[-1]["时间"] == "2026-07-15 23:59"
    assert rows[-1]["转接一组"] == 4 and rows[-1]["转接二组"] == 1, rows[-1]  # 全量含 23:30
    assert total == {"转接一组": 4, "转接二组": 1}, total

def test_build_snapshots_channel():
    import pandas as pd
    df = pd.DataFrame({
        "开始时间": ["09:02:00", "09:07:00"],
        "渠道来源": ["电话呼入呼入", "电话呼入"],  # 第二条被 channel 过滤
        "处理组别": ["转接一组", "转接一组"],
    })
    fcfg = {"channel_column": "渠道来源", "channels": ["电话呼入呼入"],
            "group_column": "处理组别", "groups": ["转接一组", "转接二组"]}
    rows, total = backfill.build_snapshots(df, "2026-07-15", fcfg, ["转接一组", "转接二组"],
                                           "开始时间", "%H:%M:%S", "09:00", "21:04")
    assert total == {"转接一组": 1, "转接二组": 0}, total
    assert rows[1]["转接一组"] == 1, rows[1]   # 09:05 刻度 cum = <09:05 = 09:02

def test_backfill_source():
    import yaml, pandas as pd
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    d = tempfile.mkdtemp()
    backfill.SLEEP = 0
    fake = pd.DataFrame({"创建日期": ["2026-07-15 09:02:00"], "接收组": ["转接一组"]})
    backfill.download_day = lambda mcfg, secrets, day, timeout=60: fake
    msgs = []
    res = backfill.backfill_source("工单明细", cfg, ["2026-07-15"], d,
                                   overwrite=True, progress_cb=msgs.append)
    assert res["成功"] == 1 and res["失败"] == 0, res
    assert backfill.day_row_count("工单明细", "2026-07-15", d) == 146, \
        backfill.day_row_count("工单明细", "2026-07-15", d)
    # 失败 continue：07-16 下载失败，不写、不计成功
    def _fail(*a, **k):
        raise RuntimeError("net")
    backfill.download_day = _fail
    res2 = backfill.backfill_source("工单明细", cfg, ["2026-07-16"], d,
                                    overwrite=True, progress_cb=msgs.append)
    assert res2["失败"] == 1 and res2["成功"] == 0, res2
    assert backfill.day_row_count("工单明细", "2026-07-16", d) == 0
    assert any("下载失败" in m for m in msgs), msgs

def test_build_snapshots_cutoff():
    import pandas as pd
    df = pd.DataFrame({
        "创建日期": ["2026-07-15 09:02:00", "2026-07-15 09:07:00", "2026-07-15 09:12:00",
                  "2026-07-15 10:00:00", "2026-07-15 23:30:00"],
        "接收组": ["转接一组", "转接一组", "转接二组", "转接一组", "转接一组"],
    })
    fcfg = {"group_column": "接收组", "groups": ["转接一组", "转接二组"]}
    # cutoff=09:10 -> 只生成 09:00..09:10 刻度，无 23:59
    rows, total = backfill.build_snapshots(df, "2026-07-15", fcfg, ["转接一组", "转接二组"],
                                           "创建日期", "%Y-%m-%d %H:%M:%S", "09:00", "21:04",
                                           cutoff="09:10")
    assert total == {"转接一组": 4, "转接二组": 1}, total  # total 仍为全天
    assert rows[-1]["时间"] == "2026-07-15 09:10", rows[-1]
    assert rows[-1]["转接一组"] == 2, rows[-1]  # <09:10 = 09:02+09:07
    assert all(not r["时间"].endswith("23:59") for r in rows)

def main():
    test_iter_days()
    test_day_ops()
    test_build_snapshots_gongdan()
    test_build_snapshots_channel()
    test_build_snapshots_cutoff()
    test_backfill_source()
    print("backfill OK")

if __name__ == "__main__":
    main()