# -*- coding: utf-8 -*-
"""探查 backfill 需要的事实: db 时间格式/现有行、CSV 节奏、在线 label 边界。"""
import csv, sqlite3, os, sys
from pathlib import Path
from collections import Counter

ROOT = Path(r"D:\PythonProject\AutoWFM")
DATA = ROOT / "data"
CSVDIR = Path(r"D:\PythonProject\hfqwfm\everyday\data")

print("=== AutoWFM db 现有行 + 时间格式 ===")
for db in sorted(DATA.glob("*.db")):
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT * FROM t ORDER BY 时间 LIMIT 3").fetchall()
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        cols = [d[0] for d in conn.execute("SELECT * FROM t LIMIT 1").description]
    except Exception as e:
        print(f"{db.name}: ERR {e}"); continue
    finally:
        conn.close()
    print(f"\n[{db.name}] rows={n} cols={cols}")
    for r in rows:
        print("   ", r)

print("\n\n=== CSV 节奏 (一天内的时刻分布) ===")
for name in ["热线登录数据.csv", "12378登录数据.csv", "二线登录数据.csv", "在线接通数据.csv"]:
    p = CSVDIR / name
    times = []
    with open(p, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            t = row[0].strip()
            if t.startswith("2026-07-01"):
                times.append(t.split()[1][:5])  # HH:MM
    cnt = Counter(times)
    print(f"\n[{name}] 2026-07-01 时刻数={len(cnt)}  前8个: {sorted(cnt)[:8]}  末4个: {sorted(cnt)[-4:]}")

print("\n\n=== 在线 label 边界 ===")
p = CSVDIR / "在线接通数据.csv"
label_dates = {"统计监控-在线": [], "统计监控-在线_IM": [], "统计监控-在线_七鱼": []}
with open(p, encoding="utf-8-sig") as f:
    for row in csv.reader(f):
        if not row or not row[0].strip().startswith("2026"): continue
        lab = row[1].strip()
        if lab in label_dates:
            label_dates[lab].append(row[0].strip()[:10])
for lab, ds in label_dates.items():
    print(f"{lab}: {len(ds)} rows, {min(ds) if ds else '-'} ~ {max(ds) if ds else '-'}")

print("\n\n=== 二线 技能 值(精确) ===")
p = CSVDIR / "二线登录数据.csv"
skills = Counter()
with open(p, encoding="utf-8-sig") as f:
    for row in csv.reader(f):
        if row and row[0].strip().startswith("2026"):
            skills[repr(row[1])] += 1
print(dict(skills))