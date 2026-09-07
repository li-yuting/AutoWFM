"""兼容层:re-export repository 的 insert/ensure_index/SCHEMAS(向后兼容旧调用点)。

新增代码请直接从 collector.repository 导入。
"""
from collector.repository import SCHEMAS, ensure_index, insert  # noqa: F401
