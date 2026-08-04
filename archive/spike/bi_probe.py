# -*- coding: utf-8 -*-
"""BI 设计前数据探针:确认 9 个 db 的列/范围/累计性/明细性质."""
import sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from storage import SCHEMAS
DATA = ROOT / "data"


def probe(source):
    cols = SCHEMAS[source]
    path = DATA / f"{source}.db"
    if not path.exists():
        print(f"[{source}] 缺失")
        return
    conn = sqlite3.connect(str(path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        mn = conn.execute("SELECT MIN(时间) FROM t").fetchone()[0]
        mx = conn.execute("SELECT MAX(时间) FROM t").fetchone()[0]
        print(f"\n=== {source} ({len(cols)}列) ===")
        print(f"  列: {cols}")
        print(f"  行数={n}  范围: {mn} ~ {mx}")
        rows = conn.execute("SELECT * FROM t ORDER BY 时间 ASC LIMIT 3").fetchall()
        print("  首三行:")
        for r in rows:
            print("   ", r)
        rows = conn.execute("SELECT * FROM t ORDER BY 时间 DESC LIMIT 3").fetchall()
        print("  末三行:")
        for r in rows:
            print("   ", r)
        # 累计性判断:取当天 9:05~20:00 的某列看是否单调
        if n >= 10:
            day = mn[:10] if mn else None
            if day:
                numeric = [c for c in cols if c != "时间"]
                for c in numeric[:6]:
                    vals = [r[0] for r in conn.execute(
                        f'SELECT "{c}" FROM t WHERE 时间 LIKE ? ORDER BY 时间 ASC', (day + "%",)
                    ).fetchall()]
                    vals = [v for v in vals if v is not None]
                    if len(vals) >= 4:
                        mono = all(vals[i] <= vals[i+1] for i in range(len(vals)-1))
                        nulls = n - len(vals)
                        trend = "单调↑(累计)" if mono else "波动(即时)"
                        note = f" (NULL {nulls}行)" if nulls else ""
                        print(f"  [{c}] {day}: {vals[:8]}... -> {trend}{note}")
    finally:
        conn.close()


for src in SCHEMAS:
    probe(src)
