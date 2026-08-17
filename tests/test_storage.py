# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3, shutil, time
from pathlib import Path
from collector import storage

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_peakflow_main.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")

def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d

def main():
    d = _tmp()
    storage.insert("热线", {"时间":"2026-07-22 09:05","转人工量":10,"接通量":9,
        "排队量":1,"累计呼入量":100,"外呼量":5,"外呼接通量":4}, d)
    conn = sqlite3.connect(Path(d)/"热线.db")
    rows = conn.execute('SELECT 转人工量, 累计呼入量 FROM t').fetchall()
    assert rows == [(10,100)], rows
    storage.insert("热线", {"时间":"2026-07-22 09:10","转人工量":11,"接通量":10,
        "排队量":0,"累计呼入量":110,"外呼量":6,"外呼接通量":5}, d)
    n = conn.execute('SELECT COUNT(*) FROM t').fetchone()[0]
    assert n == 2, n
    storage.insert("在线", {"时间":"x","转人工量":1,"转人工失败":0,"排队":0,"咨询":0,
        "在线":2,"小休":1,"示忙":1,"话后":0,"就餐":0,"培训":0,"回访":0}, d)
    assert (Path(d)/"在线.db").exists()
    print("storage OK")
    shutil.rmtree(_WS_TMP, ignore_errors=True)

if __name__ == "__main__": main()
