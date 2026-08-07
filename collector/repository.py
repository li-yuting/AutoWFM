"""存储层抽象:Repository 模式隔离存储实现,当前为 SQLite,将来可换 PostgreSQL。

写入侧(StorageRepository):collector 侧 scheduler/backfill 用,对应原 storage.py。
读取侧(ReadOnlyRepository):dashboard/queries.py + api/ 用,对应原 queries.py 底层访问。

两个抽象都已有 SQLite 实现;切换后端只需新增实现类并在 config.yaml storage.backend 指定。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

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


class StorageRepository(Protocol):
    """写入侧存储抽象(采集器用)。"""

    def insert(self, source: str, values: dict, data_dir: str) -> None: ...
    def ensure_index(self, source: str, data_dir: str) -> None: ...
    def schemas(self) -> dict: ...


class ReadOnlyRepository(Protocol):
    """读取侧存储抽象(看板/API 用)。"""

    def rows_in(self, source: str, prefix: str) -> tuple[list, list]: ...
    def cols(self, source: str) -> list: ...
    def latest_date(self) -> str: ...


class SQLiteRepository:
    """SQLite 写入实现:每源一库,每库一张表 t,每次开/关连接(原 storage.py 语义)。"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    def insert(self, source: str, values: dict, data_dir: str | None = None) -> None:
        cols = SCHEMAS[source]
        path = Path(data_dir or self.data_dir) / f"{source}.db"
        # ponytail: 每次开/关连接 - 9 路各写各的库,无跨线程共享,简单且无锁竞争
        conn = sqlite3.connect(str(path))
        try:
            col_def = ",".join(f'"{c}" {"TEXT" if c=="时间" else "INTEGER"}' for c in cols)
            conn.execute(f'CREATE TABLE IF NOT EXISTS t ({col_def})')
            quoted = ",".join('"' + c + '"' for c in cols)
            ph = ",".join("?" * len(cols))
            conn.execute(f'INSERT INTO t ({quoted}) VALUES ({ph})', [values[c] for c in cols])
            conn.commit()
        finally:
            conn.close()

    def ensure_index(self, source: str, data_dir: str | None = None) -> None:
        """为某源建「时间」列索引,加速看板按日/月前缀查询。启动时调用一次,幂等。"""
        cols = SCHEMAS[source]
        path = Path(data_dir or self.data_dir) / f"{source}.db"
        conn = sqlite3.connect(str(path))
        try:
            col_def = ",".join(f'"{c}" {"TEXT" if c=="时间" else "INTEGER"}' for c in cols)
            conn.execute(f'CREATE TABLE IF NOT EXISTS t ({col_def})')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_t_time ON t("时间")')
            conn.commit()
        finally:
            conn.close()

    def schemas(self) -> dict:
        return SCHEMAS


class SQLiteReadOnlyRepository:
    """SQLite 只读实现:包装 queries.py 的底层 _connect/_rows_in/_cols/latest_data_date。
    聚合逻辑(build_day/build_month 等)仍在 queries.py,本类只提供原始行/列访问。"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir

    def _path(self, source: str) -> Path:
        return Path(self.data_dir) / f"{source}.db"

    def cols(self, source: str) -> list:
        """返回该源表的列名列表;无表返回 []。"""
        path = self._path(source)
        if not path.exists():
            return []
        con = sqlite3.connect(str(path))
        try:
            return [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
        finally:
            con.close()

    def rows_in(self, source: str, prefix: str) -> tuple[list, list]:
        """某天(prefix=YYYY-MM-DD)或某月(prefix=YYYY-MM)该源所有行(升序)+列名。
        无表/无数据返回 ([], [])。"""
        path = self._path(source)
        if not path.exists():
            return [], []
        con = sqlite3.connect(str(path))
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
            if not cols:
                return [], []
            rows = con.execute(
                f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM t '
                f'WHERE "时间" LIKE ? ORDER BY "时间"', (f"{prefix}%",)
            ).fetchall()
        finally:
            con.close()
        return rows, cols

    def latest_date(self) -> str:
        """热线/在线 db 中最新的日期(YYYY-MM-DD)。任一库无数据回落到今天。"""
        from datetime import date as _date
        best = ""
        for src in ("热线", "在线"):
            path = self._path(src)
            if not path.exists():
                continue
            con = sqlite3.connect(str(path))
            try:
                cols = [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
                if "时间" not in cols:
                    continue
                row = con.execute('SELECT MAX(substr("时间",1,10)) FROM t').fetchone()
                if row and row[0] and row[0] > best:
                    best = row[0]
            finally:
                con.close()
        return best or _date.today().strftime("%Y-%m-%d")
