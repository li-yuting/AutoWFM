# -*- coding: utf-8 -*-
"""删除 07-22 的 smoke 旧数据,保留 main.py 写的真实行。"""
import sqlite3, glob, os
os.chdir(r"D:\PythonProject\AutoWFM")
for f in sorted(glob.glob("data/*.db")):
    conn = sqlite3.connect(f, timeout=10)  # busy_timeout,防 main.py 正在写
    cur = conn.execute('DELETE FROM t WHERE "时间" LIKE ?', ('2026-07-22%',))
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    print(f"{os.path.basename(f)}: deleted {cur.rowcount}, remaining {n}")
    conn.close()
