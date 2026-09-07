"""存储层：SQLite 实现。每个源一个独立库，每库一张表 t。

SCHEMAS 是单一事实源（storage.py 从这里 re-export）。
fetch_rows/exec_sql 是共享的「开库-建列-查询-关库」助手，供 repository/
notify/backfill 复用，避免各处手写 PRAGMA+SELECT。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# SCHEMAS 与原 storage.py 保持一致(单一事实源,storage.py 从这里 re-export)
SCHEMAS = {
    "热线":   ["时间","转人工量","接通量","排队量","累计呼入量","外呼量","外呼接通量"],
    "12378":  ["时间","转人工量","接通量","排队量","累计呼入量"],
    "热线明细": ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "常规":   ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "贷后":   ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "12378明细": ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
    "在线":   ["时间","转人工量","转人工失败","排队","咨询","在线","小休","示忙","话后","就餐","培训","回访"],
    "会话记录": ["时间","转接一组","转接二组","贷后转接组","回访组一组","贷后回访组"],
    "工单明细": ["时间","二线客诉处理组","常规工单处理组","回访组一组","贷后回访组","12378回访组","转接一组","转接二组","贷后转接组"],
}


def _table_ddl(source: str) -> str:
    col_def = ",".join(f'"{c}" {"TEXT" if c=="时间" else "INTEGER"}' for c in SCHEMAS[source])
    return f"CREATE TABLE IF NOT EXISTS t ({col_def})"


def fetch_rows(source: str, data_dir: str, sql: str, params=()) -> tuple[list | None, list | None]:
    """对某源库执行 SELECT，返回 (rows, cols)；无库/无表返回 (None, None)。

    cols 为该库表的列名（与表定义顺序一致，可用 SELECT * + zip(cols, row)）。
    """
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return None, None
    con = sqlite3.connect(str(path))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
        if not cols:
            return None, None
        return con.execute(sql, params).fetchall(), cols
    finally:
        con.close()


def exec_sql(source: str, data_dir: str, sql: str, params=()) -> None:
    """对某源库执行写操作并提交；无库则跳过。"""
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return
    con = sqlite3.connect(str(path))
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


class SQLiteRepository:
    """SQLite 写入实现:每源一库,每库一张表 t,每次开/关连接(原 storage.py 语义)。"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    def insert(self, source: str, values: dict, data_dir: str | None = None) -> None:
        path = Path(data_dir or self.data_dir) / f"{source}.db"
        # ponytail: 每次开/关连接 - 9 路各写各的库,无跨线程共享,简单且无锁竞争
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(_table_ddl(source))
            cols = SCHEMAS[source]
            quoted = ",".join('"' + c + '"' for c in cols)
            ph = ",".join("?" * len(cols))
            conn.execute(f'INSERT INTO t ({quoted}) VALUES ({ph})', [values[c] for c in cols])
            conn.commit()
        finally:
            conn.close()

    def ensure_index(self, source: str, data_dir: str | None = None) -> None:
        """为某源建「时间」列索引,加速看板按日/月前缀查询。启动时调用一次,幂等。"""
        path = Path(data_dir or self.data_dir) / f"{source}.db"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(_table_ddl(source))
            conn.execute('CREATE INDEX IF NOT EXISTS idx_t_time ON t("时间")')
            conn.commit()
        finally:
            conn.close()

    def schemas(self) -> dict:
        return SCHEMAS


class SQLiteReadOnlyRepository:
    """SQLite 只读实现:提供原始行/列访问。
    聚合逻辑(build_day/build_month 等)仍在 dashboard/queries.py。"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    def rows_in(self, source: str, prefix: str) -> tuple[list, list]:
        """某天(prefix=YYYY-MM-DD)或某月(prefix=YYYY-MM)该源所有行(升序)+列名。
        无表/无数据返回 ([], [])。"""
        rows, cols = fetch_rows(
            source, self.data_dir,
            'SELECT * FROM t WHERE "时间" LIKE ? ORDER BY "时间"', (f"{prefix}%",))
        return (rows or [], cols or [])

    def latest_date(self) -> str:
        """热线/在线 db 中最新的日期(YYYY-MM-DD)。任一库无数据回落到今天。"""
        from datetime import date as _date
        best = ""
        for src in ("热线", "在线"):
            rows, _ = fetch_rows(src, self.data_dir, 'SELECT MAX(substr("时间",1,10)) FROM t')
            if rows and rows[0] and rows[0][0] and rows[0][0] > best:
                best = rows[0][0]
        return best or _date.today().strftime("%Y-%m-%d")


# ---- 模块级兼容函数（原 storage.py 签名,调用点不变）----

def insert(source, values, data_dir):
    """插入一行到指定源的 t 表。"""
    SQLiteRepository(data_dir).insert(source, values, data_dir)


def ensure_index(source, data_dir):
    """为某源建「时间」列索引,幂等。"""
    SQLiteRepository(data_dir).ensure_index(source, data_dir)
