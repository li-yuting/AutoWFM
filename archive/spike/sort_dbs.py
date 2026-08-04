# -*- coding: utf-8 -*-
"""把 9 个 db 的行按 时间 升序物理重排并 VACUUM 压缩。

每库: 建 t_new(同 SCHEMAS DDL) -> INSERT ... ORDER BY 时间 -> DROP t -> RENAME -> VACUUM。
需先停 main.py(避免 copy/drop 间隙丢实时行)。
"""
import sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from storage import SCHEMAS  # noqa: E402

DATA = ROOT / "data"


def sort_db(source):
    cols = SCHEMAS[source]
    path = DATA / f"{source}.db"
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit: VACUUM 需事务外
    try:
        n_before = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        col_def = ",".join(f'"{c}" {"TEXT" if c == "时间" else "INTEGER"}' for c in cols)
        quoted = ",".join('"' + c + '"' for c in cols)
        conn.execute("DROP TABLE IF EXISTS t_new")  # 清残留(上次崩溃可能留下)
        conn.execute(f'CREATE TABLE t_new ({col_def})')
        conn.execute(f'INSERT INTO t_new ({quoted}) SELECT {quoted} FROM t ORDER BY 时间')
        conn.execute("DROP TABLE t")
        conn.execute("ALTER TABLE t_new RENAME TO t")
        conn.execute("VACUUM")
        n_after = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        first = conn.execute("SELECT 时间 FROM t LIMIT 1").fetchone()[0]
        last = conn.execute("SELECT 时间 FROM t ORDER BY 时间 DESC LIMIT 1").fetchone()[0]
        times = [r[0] for r in conn.execute("SELECT 时间 FROM t").fetchall()]
        ok = times == sorted(times)
        print(f"[{source}] {n_before}->{n_after}  {first} ~ {last}  raw_sorted={ok}")
    finally:
        conn.close()


if __name__ == "__main__":
    for source in SCHEMAS:
        sort_db(source)
