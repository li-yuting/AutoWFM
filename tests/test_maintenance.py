# -*- coding: utf-8 -*-
"""磁盘保留策略测试：盘点只读、清理只命中白名单模式、VACUUM 真能回收。

全部在 tests/.test_tmp 下造数据，用 os.utime 把文件/目录「做旧」，不碰真实 logs/output/data。
"""
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import maintenance as M

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_repository.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d


def _layout(d, keep_days=30):
    """造一份典型布局：logs 有活动日志 + 两种轮转归档，output 有按日目录，data 有一个库。"""
    logs = Path(d) / "logs"; logs.mkdir()
    outs = Path(d) / "output"; outs.mkdir()
    data = Path(d) / "data"; data.mkdir()
    old = time.time() - (keep_days + 1) * 86400

    (logs / "autowfm.log").write_text("正在写", encoding="utf-8")          # 活动日志
    (logs / "manager.log.2026-01-01").write_text("轮转归档" * 10, encoding="utf-8")
    (logs / "dashboard.log.1").write_text("按大小轮转" * 10, encoding="utf-8")
    (logs / "note.txt").write_text("无关文件", encoding="utf-8")            # 不匹配模式
    for name in ("manager.log.2026-01-01", "dashboard.log.1"):
        os.utime(logs / name, (old, old))

    (outs / "2026-01-01").mkdir()
    (outs / "2026-01-01" / "预测.xlsx").write_text("旧产物", encoding="utf-8")
    (outs / "2026-01-01" / "预测.html").write_text("旧产物", encoding="utf-8")
    (outs / "2026-09-20").mkdir()
    (outs / "2026-09-20" / "热线.csv").write_text("新产物", encoding="utf-8")
    (outs / "scratch").mkdir()                                            # 非按日目录 -> 不动
    (outs / "scratch" / "tmp.txt").write_text("临时", encoding="utf-8")
    os.utime(outs / "2026-01-01", (old, old))
    os.utime(outs / "scratch", (old, old))

    con = sqlite3.connect(str(data / "热线.db"))
    con.execute('CREATE TABLE t ("时间" TEXT, "转人工量" INTEGER, "备注" TEXT)')
    con.executemany('INSERT INTO t VALUES (?,?,?)',
                    [(f"2026-07-27 09:{i % 60:02d}", i, "x" * 200) for i in range(4000)])
    con.execute('DELETE FROM t WHERE "转人工量" > 100')   # 留下大量空闲页
    con.commit(); con.close()
    return logs, outs, data


def test_scan_is_readonly():
    """scan 只报告：可清理清单正确，且一个文件都没动。"""
    d = _tmp(); logs, outs, data = _layout(d)
    res = M.scan(logs, outs, data, keep_days=30)
    names = sorted(x["文件"] for x in res["日志"])
    assert names == ["dashboard.log.1", "manager.log.2026-01-01"], names
    assert [x["目录"] for x in res["输出目录"]] == ["2026-01-01"], res["输出目录"]
    assert res["输出目录"][0]["文件数"] == 2, res["输出目录"]
    assert res["已删条目数"] == 0, "dry-run 不应删任何东西"
    assert (logs / "manager.log.2026-01-01").exists()
    assert (outs / "2026-01-01" / "预测.xlsx").exists()
    # 目录盘点三项都在，且体积为正
    assert [x["名称"][:5] for x in res["目录"]] == ["logs（", "outpu", "data（"], res["目录"]
    assert all(x["大小"] for x in res["目录"]), res["目录"]
    print("scan_is_readonly OK")


def test_prune_logs():
    """只删匹配轮转模式且过期的；活动日志与无关文件永不入选。"""
    d = _tmp(); logs, outs, data = _layout(d)
    logs_ = M.prune_logs(logs, keep_days=30, apply=False)
    assert len(logs_) == 2 and all(not x["已删"] for x in logs_), logs_
    done = M.prune_logs(logs, keep_days=30, apply=True)
    assert all(x["已删"] for x in done), done
    assert all(x["错误"] == "" for x in done)
    assert not (logs / "manager.log.2026-01-01").exists()
    assert not (logs / "dashboard.log.1").exists()
    assert (logs / "autowfm.log").exists(), "活动日志不能被删"
    assert (logs / "note.txt").exists(), "不匹配模式的文件不能被删"
    print("prune_logs OK")


def test_prune_logs_keep_recent():
    """未过期的归档不动。"""
    d = _tmp(); logs, outs, data = _layout(d, keep_days=30)
    (logs / "autowfm.log.2026-09-19").write_text("昨天的归档", encoding="utf-8")
    assert M.prune_logs(logs, keep_days=30, apply=True) != []
    assert (logs / "autowfm.log.2026-09-19").exists(), "当天/近期的归档应保留"
    print("prune_logs_keep_recent OK")


def test_prune_output():
    """只删过期的按日目录；新目录与非按日目录保留。"""
    d = _tmp(); logs, outs, data = _layout(d)
    dry = M.prune_output(outs, keep_days=30, apply=False)
    assert [x["目录"] for x in dry] == ["2026-01-01"], dry
    assert (outs / "2026-01-01").exists(), "dry-run 不应删"
    done = M.prune_output(outs, keep_days=30, apply=True)
    assert done and done[0]["已删"] is True, done
    assert not (outs / "2026-01-01").exists()
    assert (outs / "2026-09-20" / "热线.csv").exists(), "未过期目录应保留"
    assert (outs / "scratch" / "tmp.txt").exists(), "非 YYYY-MM-DD 目录不归它管"
    print("prune_output OK")


def test_compact_databases():
    """VACUUM 回收空闲页；dry-run 不动文件。"""
    d = _tmp(); logs, outs, data = _layout(d)
    db = data / "热线.db"
    before = db.stat().st_size
    dry = M.compact_databases(data, apply=False)
    assert len(dry) == 1 and dry[0]["库"] == "热线.db", dry
    assert dry[0]["可回收"] > 0, f"删了 3900 行应留下空闲页: {dry}"
    assert db.stat().st_size == before, "dry-run 不应改动库文件"

    done = M.compact_databases(data, apply=True)
    assert done[0]["错误"] == "", done
    after = db.stat().st_size
    assert after < before, f"VACUUM 应缩小文件: {before} -> {after}"
    con = sqlite3.connect(str(db))
    try:
        assert con.execute("PRAGMA freelist_count").fetchone()[0] == 0, "VACUUM 后不应有空闲页"
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 101, "数据不能丢"
    finally:
        con.close()
    print("compact_databases OK")


def test_run_apply_then_scan_clean():
    """run 串起全部：apply 后过期项清空，再 scan 已无可清理项。"""
    d = _tmp(); logs, outs, data = _layout(d)
    first = M.run(logs, outs, data, keep_days=30, apply=False)
    assert first["已删条目数"] == 0 and first["可清理"] > 0, first
    applied = M.run(logs, outs, data, keep_days=30, apply=True)
    # 2 个日志归档 + 1 个按日目录 = 3 个条目（按「条目」计，不是文件数）
    assert applied["已删条目数"] == 3, applied
    again = M.scan(logs, outs, data, keep_days=30)
    assert again["日志"] == [] and again["输出目录"] == [], again
    assert again["可清理文本"] == "0 B", again["可清理文本"]
    # 目录盘点里 data 仍应大于 0（库还在）
    assert next(x for x in again["目录"] if x["名称"].startswith("data"))["文件数"] == 1
    print("run_apply_then_scan_clean OK")


def test_human():
    assert M.human(0) == "0 B"
    assert M.human(999) == "999 B"
    assert M.human(2048) == "2.0 KB"
    assert M.human(5 * 1024 * 1024) == "5.0 MB"
    print("human OK")


def main():
    test_human()
    test_scan_is_readonly()
    test_prune_logs()
    test_prune_logs_keep_recent()
    test_prune_output()
    test_compact_databases()
    test_run_apply_then_scan_clean()
    shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("maintenance OK")


if __name__ == "__main__":
    main()
