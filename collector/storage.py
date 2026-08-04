"""9 个独立 SQLite,按源名分库,每库一张表 t。"""
import sqlite3
from pathlib import Path

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

def insert(source, values, data_dir):
    cols = SCHEMAS[source]
    path = Path(data_dir) / f"{source}.db"
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
