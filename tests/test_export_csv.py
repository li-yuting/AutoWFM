# -*- coding: utf-8 -*-
"""导出 CSV 测试:全量导出、日期区间过滤、空库跳过、utf-8-sig 编码。"""
import csv, os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector.repository import export_csv, list_sources

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_repository.py）
# 不 rmtree：本机沙箱会做「安全删除」并把被删对象的父目录一并送入回收站，
# 目录已进 .gitignore，每次运行用独立子目录即可。
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _tmp(name):
    d = os.path.join(_WS_TMP, f"{name}{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d


def _make_db(data_dir, source, dates):
    con = sqlite3.connect(os.path.join(data_dir, f"{source}.db"))
    con.execute('CREATE TABLE t ("时间" TEXT, "转人工量" INTEGER)')
    con.executemany('INSERT INTO t VALUES (?,?)', [(d, 1) for d in dates])
    con.commit()
    con.close()


def test_export_full():
    """全量导出:一库一 CSV,列名/行序/编码正确,无表的空库跳过。"""
    data = _tmp("data_")
    out = _tmp("out_")
    _make_db(data, "热线", ["2026-09-19 09:00", "2026-09-20 09:00", "2026-09-21 09:00"])
    _make_db(data, "在线", ["2026-09-20 09:00"])
    open(os.path.join(data, "空库.db"), "wb").close()  # 未建表的库

    msgs = []
    rows = export_csv(data, out, progress_cb=msgs.append)

    assert [r["源"] for r in rows] == ["在线", "热线"], rows   # 空库被跳过
    assert [r["行数"] for r in rows] == [1, 3], rows
    assert len(msgs) == 2, msgs
    assert not os.path.exists(os.path.join(out, "空库.csv"))
    assert os.path.exists(os.path.join(out, "热线.csv"))

    raw = open(os.path.join(out, "热线.csv"), "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf"), raw[:8]            # utf-8-sig,BOM 供 Excel 识别
    with open(os.path.join(out, "热线.csv"), encoding="utf-8-sig", newline="") as f:
        got = list(csv.reader(f))
    assert got[0] == ["时间", "转人工量"], got[0]
    assert [r[0] for r in got[1:]] == ["2026-09-19 09:00", "2026-09-20 09:00", "2026-09-21 09:00"]


def test_export_date_range():
    """日期区间:含首尾边界,区间外不导。"""
    data = _tmp("data_")
    out = _tmp("out_")
    _make_db(data, "热线", ["2026-09-19 09:00", "2026-09-20 09:00", "2026-09-21 09:05"])
    rows = export_csv(data, out, "2026-09-20", "2026-09-20")
    assert {r["源"]: r["行数"] for r in rows} == {"热线": 1}, rows
    with open(os.path.join(out, "热线.csv"), encoding="utf-8-sig", newline="") as f:
        assert [r[0] for r in list(csv.reader(f))[1:]] == ["2026-09-20 09:00"]


def test_export_selected_sources():
    """只导勾选的源;不存在的源名跳过而不报错。"""
    data = _tmp("data_")
    out = _tmp("out_")
    _make_db(data, "热线", ["2026-09-20 09:00"])
    _make_db(data, "在线", ["2026-09-20 09:00"])
    assert list_sources(data) == ["在线", "热线"], list_sources(data)

    rows = export_csv(data, out, sources=["热线"])
    assert [r["源"] for r in rows] == ["热线"], rows
    assert os.path.exists(os.path.join(out, "热线.csv"))
    assert not os.path.exists(os.path.join(out, "在线.csv"))

    assert export_csv(data, out, sources=["不存在的库"]) == []


def main():
    test_export_full()
    test_export_date_range()
    test_export_selected_sources()
    print("export_csv OK")


if __name__ == "__main__":
    main()
