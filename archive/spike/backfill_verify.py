# -*- coding: utf-8 -*-
"""校验回填结果: 行数/时间范围/重复时间戳/历史行NULL/实时行完整。"""
import sqlite3
from pathlib import Path

DATA = Path(r"D:\PythonProject\AutoWFM\data")
for db in sorted(DATA.glob("*.db")):
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        nd = conn.execute("SELECT COUNT(DISTINCT 时间) FROM t").fetchone()[0]
        mn = conn.execute("SELECT MIN(时间) FROM t").fetchone()[0]
        mx = conn.execute("SELECT MAX(时间) FROM t").fetchone()[0]
    finally:
        conn.close()
    print(f"[{db.name}] rows={n} distinct时间={nd} {'OK' if n==nd else 'DUP!'}  {mn} ~ {mx}")

print("\n=== 历史行(应有 NULL 缺列) ===")
conn = sqlite3.connect(str(DATA / "热线.db"))
print("热线 2026-07-01 09:00:", conn.execute("SELECT * FROM t WHERE 时间='2026-07-01 09:00'").fetchone())
conn.close()
conn = sqlite3.connect(str(DATA / "在线.db"))
print("在线 2026-07-01 09:00:", conn.execute("SELECT * FROM t WHERE 时间='2026-07-01 09:00'").fetchone())
conn.close()

print("\n=== 实时行(应完整,累计呼入量非NULL) ===")
conn = sqlite3.connect(str(DATA / "热线.db"))
print("热线 2026-07-24 13:50:", conn.execute("SELECT * FROM t WHERE 时间='2026-07-24 13:50'").fetchone())
conn.close()

print("\n=== 07-10 重复时间戳(应只1条) ===")
conn = sqlite3.connect(str(DATA / "热线.db"))
print("热线 2026-07-10 16:15 条数:", conn.execute("SELECT COUNT(*) FROM t WHERE 时间='2026-07-10 16:15'").fetchone()[0])
conn.close()