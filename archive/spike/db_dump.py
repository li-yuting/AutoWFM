# -*- coding: utf-8 -*-
"""dump data/*.db: 列、行数、最近 3 行。"""
import sqlite3, glob, os
os.chdir(r"D:\PythonProject\AutoWFM")
for f in sorted(glob.glob("data/*.db")):
    conn = sqlite3.connect(f)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(t)")]
    n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    print(f"\n=== {os.path.basename(f)}  ({n} rows) ===")
    print("  cols:", cols)
    for r in conn.execute("SELECT * FROM t ORDER BY rowid DESC LIMIT 3").fetchall():
        print("  ", r)
    conn.close()
