# -*- coding: utf-8 -*-
"""轮转日志归档清理。

只处理 `logs/` 顶层的 `x.log.YYYY-MM-DD` 与 `x.log.N`，超过保留期才删除。
正在写入的 `x.log`、其他目录和业务数据均不在范围内。仅依赖标准库。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

# TimedRotatingFileHandler 的 x.log.2026-09-20 与按大小轮转的 x.log.1
_LOG_ARCHIVE = re.compile(r"\.log(?:\.\d{4}-\d{2}-\d{2}|\.\d+)$")


def human(n: int) -> str:
    """字节数转人类可读。"""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def _older_than(mtime: float, keep_days: int, now: float) -> bool:
    return (now - mtime) > keep_days * 86400


def _age_days(mtime: float, now: float) -> int:
    return int(max(0.0, now - mtime) // 86400)


def prune_logs(log_dir, keep_days: int = 30, now: float | None = None) -> list[dict]:
    """删除 `logs/` 顶层超过 keep_days 的轮转归档。

    返回 [{"文件","大小","大小文本","天数","已删","错误"}]。
    单个文件删除失败时记录错误并继续；正在写的 `x.log` 永不入选。
    """
    now = now or time.time()
    out: list[dict] = []
    root = Path(log_dir)
    if not root.is_dir():
        return out

    for p in sorted(root.iterdir()):
        if not p.is_file() or not _LOG_ARCHIVE.search(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if not _older_than(st.st_mtime, keep_days, now):
            continue

        item = {
            "文件": p.name,
            "大小": st.st_size,
            "大小文本": human(st.st_size),
            "天数": _age_days(st.st_mtime, now),
            "已删": False,
            "错误": "",
        }
        try:
            p.unlink()
            item["已删"] = True
        except OSError as exc:
            item["错误"] = str(exc)
        out.append(item)
    return out
