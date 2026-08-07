"""9 个独立 SQLite,按源名分库,每库一张表 t。

薄封装层:委托给 collector.repository.SQLiteRepository。
保留 insert/ensure_index/SCHEMAS 函数签名不变(向后兼容 scheduler/backfill/main/tests 调用点)。
SCHEMAS 从 repository.py 导入(单一事实源)。
"""
from collector.repository import SQLiteRepository, SCHEMAS

# 模块级默认实例(向后兼容无 data_dir 参数的场景)
_repo = SQLiteRepository("data")


def insert(source, values, data_dir):
    """插入一行到指定源的 t 表。"""
    _repo.insert(source, values, data_dir)


def ensure_index(source, data_dir):
    """为某源建「时间」列索引,幂等。"""
    _repo.ensure_index(source, data_dir)
