# -*- coding: utf-8 -*-
"""检查 CSV 内部时间戳重复 + 节奏变化 + leading-space 行数。"""
import csv
from collections import Counter
from pathlib import Path

CSVDIR = Path(r"D:\PythonProject\hfqwfm\everyday\data")
SINCE = "2026-07-01 00:00"


def scan(name, skill_filter=None):
    times = []
    lead = 0
    with open(CSVDIR / name, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip().startswith("2026"):
                continue
            t = row[0].strip()[:16]
            if t < SINCE:
                continue
            sk = row[1]
            if sk.startswith(" "):
                lead += 1
            if skill_filter is None or sk.strip() == skill_filter:
                times.append(t)
    dup = {t: n for t, n in Counter(times).items() if n > 1}
    print(f"\n[{name}] filter={skill_filter}")
    print(f"  命中行={len(times)}  唯一时间戳={len(set(times))}  重复时间戳={len(dup)}  leading-space行={lead}")
    if dup:
        print(f"  重复样例(前5): {list(dup.items())[:5]}")
    # 节奏: 统计 07-01 和 07-23 的时刻数
    for day in ["2026-07-01", "2026-07-23", "2026-07-24"]:
        ds = sorted({t for t in times if t.startswith(day)})
        if ds:
            print(f"  {day}: {len(ds)} 个时刻, 首={ds[0]} 末={ds[-1]}, 前6={ds[:6]}")


for name, sk in [("热线登录数据.csv", None), ("12378登录数据.csv", None),
                 ("二线登录数据.csv", "常规转接组"), ("在线接通数据.csv", "统计监控-在线")]:
    scan(name, sk)