# -*- coding: utf-8 -*-
"""日志保留策略测试：只删除过期轮转归档，不触碰其他数据。"""
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import maintenance as M

_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d


def _layout(d):
    logs = Path(d) / "logs"
    outs = Path(d) / "output"
    data = Path(d) / "data"
    logs.mkdir()
    (outs / "2026-01-01").mkdir(parents=True)
    data.mkdir()

    now = time.time()
    old = now - 31 * 86400
    recent = now - 86400
    for name, mtime in (
        ("manager.log.2026-01-01", old),
        ("dashboard.log.1", old),
        ("autowfm.log.2026-09-19", recent),
    ):
        p = logs / name
        p.write_text("归档" * 10, encoding="utf-8")
        os.utime(p, (mtime, mtime))
    (logs / "autowfm.log").write_text("正在写", encoding="utf-8")
    (logs / "note.txt").write_text("无关文件", encoding="utf-8")
    (logs / "sub").mkdir()
    (logs / "sub" / "keep.log.1").write_text("嵌套文件", encoding="utf-8")

    product = outs / "2026-01-01" / "预测.xlsx"
    product.write_text("旧产物", encoding="utf-8")
    db = data / "热线.db"
    con = sqlite3.connect(str(db))
    con.execute('CREATE TABLE t ("时间" TEXT)')
    con.execute('INSERT INTO t VALUES ("2026-09-23 05:00")')
    con.commit()
    con.close()
    return logs, product, db, now


def test_prune_logs_removes_only_expired_archives():
    """只删 logs 顶层两类过期轮转归档；近期、活动、无关及嵌套文件保留。"""
    d = _tmp()
    logs, product, db, now = _layout(d)
    product_before = product.read_bytes()
    db_before = db.read_bytes()

    removed = M.prune_logs(logs, keep_days=30, now=now)

    assert sorted(x["文件"] for x in removed) == [
        "dashboard.log.1", "manager.log.2026-01-01"
    ], removed
    assert all(x["已删"] and not x["错误"] for x in removed), removed
    assert not (logs / "manager.log.2026-01-01").exists()
    assert not (logs / "dashboard.log.1").exists()
    assert (logs / "autowfm.log.2026-09-19").exists(), "近期归档不能删"
    assert (logs / "autowfm.log").exists(), "活动日志不能删"
    assert (logs / "note.txt").exists(), "不匹配模式的文件不能删"
    assert (logs / "sub" / "keep.log.1").exists(), "只处理 logs 顶层文件"
    assert product.read_bytes() == product_before, "output 必须保持不变"
    assert db.read_bytes() == db_before, "SQLite 数据库必须保持不变"
    print("prune_logs_removes_only_expired_archives OK")


def test_human():
    assert M.human(0) == "0 B"
    assert M.human(999) == "999 B"
    assert M.human(2048) == "2.0 KB"
    assert M.human(5 * 1024 * 1024) == "5.0 MB"
    print("human OK")


def main():
    test_human()
    test_prune_logs_removes_only_expired_archives()
    shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("maintenance OK")


if __name__ == "__main__":
    main()
