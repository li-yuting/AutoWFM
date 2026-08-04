# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3, tempfile
from pathlib import Path
from collector import storage

def main():
    d = tempfile.mkdtemp()
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

if __name__ == "__main__": main()
