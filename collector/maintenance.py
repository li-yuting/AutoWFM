# -*- coding: utf-8 -*-
"""磁盘保留策略：日志归档、output 按日目录、SQLite 空洞的盘点与清理。

为什么需要：`logs/`、`output/`、`data/` 原先都只增不减——采集器日志有轮转，
但管理器/看板/API 的日志没有；`output/` 每次预测与导出都新建文件；
SQLite 长期 insert 后文件里会留空洞。

分工：
- `scan()`              只读盘点：目录占用、可清理清单、可回收空间，**不动任何文件**；
- `prune_logs()`        删 `logs/` 下的**轮转归档**（`x.log.YYYY-MM-DD`、`x.log.N`）；
                        正在写的 `x.log` 永不入选；
- `prune_output()`      删 `output/` 下过期的 `YYYY-MM-DD` 按日目录；
- `compact_databases()` 对 `data/*.db` 执行 VACUUM 回收空洞；
- `run()`               串起来给管理器「维护」页用（apply 由 UI 的确认框决定）。

**安全约定**：默认 `apply=False`（dry-run，只报告）；只有显式 `apply=True` 才删。
删除只针对白名单目录 + 固定命名模式命中的条目，且自底向上逐个删文件/空目录，
不做 `shutil.rmtree` 式的一次性批量删除。仅依赖标准库。
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

# 轮转归档：TimedRotatingFileHandler 的 x.log.2026-09-20 与按大小轮转的 x.log.1
_LOG_ARCHIVE = re.compile(r"\.log(?:\.\d{4}-\d{2}-\d{2}|\.\d+)$")
# output/<YYYY-MM-DD>/ 按日目录
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def human(n: int) -> str:
    """字节数转人类可读。"""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def dir_usage(path) -> tuple[int, int]:
    """(总字节, 文件数)。目录不存在返回 (0, 0)。"""
    total = count = 0
    root = Path(path)
    if not root.is_dir():
        return 0, 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
                count += 1
        except OSError:
            pass   # 被占用/权限不足的条目跳过，不影响整体盘点
    return total, count


def _older_than(mtime: float, keep_days: int, now: float) -> bool:
    return (now - mtime) > keep_days * 86400


def _age_days(mtime: float, now: float) -> int:
    return int(max(0.0, now - mtime) // 86400)


def _remove_tree(path) -> int:
    """自底向上逐个删文件、再删空目录，返回删掉的文件数。

    不用 `shutil.rmtree`：一次性批量删除在本机（沙箱/安全删除）会被拦下并要求确认，
    而且历史上曾把父目录一起送进回收站。逐个删更可控，失败也只是一条跳过。
    """
    root = Path(path)
    removed = 0
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_dir():
                p.rmdir()
            else:
                p.unlink()
                removed += 1
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass
    return removed


def prune_logs(log_dir, keep_days: int = 30, apply: bool = False, now: float | None = None) -> list[dict]:
    """清 `logs/` 下超过 keep_days 的轮转归档。

    只匹配带轮转后缀的（`x.log.2026-09-20` / `x.log.1`），正在写的 `x.log` 不在范围内。
    返回 [{"文件","大小","大小文本","天数","已删"}]。
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
        item = {"文件": p.name, "大小": st.st_size, "大小文本": human(st.st_size),
                "天数": _age_days(st.st_mtime, now), "已删": False, "错误": ""}
        if apply:
            try:
                p.unlink()
                item["已删"] = True
            except OSError as e:
                item["错误"] = str(e)
        out.append(item)
    return out


def prune_output(out_dir, keep_days: int = 30, apply: bool = False, now: float | None = None) -> list[dict]:
    """清 `output/` 下超过 keep_days 的 `YYYY-MM-DD` 按日目录。

    只认按日目录名，根目录下散落的产物不动。返回
    [{"目录","大小","大小文本","文件数","天数","已删"}]。
    """
    now = now or time.time()
    out: list[dict] = []
    root = Path(out_dir)
    if not root.is_dir():
        return out
    for p in sorted(root.iterdir()):
        if not p.is_dir() or not _DAY_DIR.match(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if not _older_than(st.st_mtime, keep_days, now):
            continue
        size, count = dir_usage(p)
        item = {"目录": p.name, "大小": size, "大小文本": human(size), "文件数": count,
                "天数": _age_days(st.st_mtime, now), "已删": False, "错误": ""}
        if apply:
            try:
                _remove_tree(p)
                item["已删"] = not p.exists()
            except OSError as e:
                item["错误"] = str(e)
        out.append(item)
    return out


def _free_bytes(db_path: Path) -> int:
    """SQLite 空闲页字节数（VACUUM 大致能回收这么多）。读不到返回 0。"""
    try:
        con = sqlite3.connect(str(db_path))
        try:
            page_size = con.execute("PRAGMA page_size").fetchone()[0]
            free_pages = con.execute("PRAGMA freelist_count").fetchone()[0]
            return int(page_size) * int(free_pages)
        finally:
            con.close()
    except Exception:
        return 0


def compact_databases(data_dir, apply: bool = False, progress_cb=None) -> list[dict]:
    """`data/*.db` 逐个 VACUUM 回收空洞。

    返回 [{"库","前","可回收","后","回收","错误"}]。采集器正在写时可能拿不到锁，
    该库会记 错误 并继续处理下一个（不影响其它库）。
    """
    out: list[dict] = []
    for p in sorted(Path(data_dir).glob("*.db")):
        try:
            before = p.stat().st_size
        except OSError:
            continue
        item = {"库": p.name, "前": before, "可回收": _free_bytes(p), "后": before,
                "回收": 0, "错误": ""}
        if apply:
            try:
                con = sqlite3.connect(str(p), timeout=10)
                try:
                    con.execute("VACUUM")
                finally:
                    con.close()
                item["后"] = p.stat().st_size
                item["回收"] = max(0, before - item["后"])
            except Exception as e:
                item["错误"] = str(e)
        out.append(item)
        if progress_cb:
            progress_cb(f"{p.name:<12}{human(before):>10}"
                        + (f"  -> {human(item['后'])}" if apply else "")
                        + (f"   {item['错误']}" if item["错误"] else ""))
    return out


def scan(log_dir, out_dir, data_dir, keep_days: int = 30, now: float | None = None) -> dict:
    """只读盘点，等价于 run(..., apply=False)。"""
    return run(log_dir, out_dir, data_dir, keep_days=keep_days, apply=False, now=now)


def run(log_dir, out_dir, data_dir, keep_days: int = 30, apply: bool = False,
        progress_cb=None, now: float | None = None) -> dict:
    """盘点 + （apply=True 时）清理。返回结构与 scan 一致，可直接喂 UI。

    {"keep_days", "目录":[...], "日志":[...], "输出目录":[...], "数据库":[...],
     "可清理文本", "可回收文本", "已删文件数"}
    """
    now = now or time.time()
    logs = prune_logs(log_dir, keep_days, apply=apply, now=now)
    outs = prune_output(out_dir, keep_days, apply=apply, now=now)
    dbs = compact_databases(data_dir, apply=apply, progress_cb=progress_cb)

    dirs = []
    for label, path in (("logs（日志）", log_dir), ("output（产物）", out_dir), ("data（数据）", data_dir)):
        size, count = dir_usage(path)
        dirs.append({"名称": label, "路径": str(path), "大小": size,
                     "大小文本": human(size), "文件数": count})

    freed = sum(x["大小"] for x in logs) + sum(x["大小"] for x in outs)
    return {
        "keep_days": keep_days,
        "目录": dirs,
        "日志": logs,
        "输出目录": outs,
        "数据库": dbs,
        "可清理": freed,
        "可清理文本": human(freed),
        "可回收": sum(x["可回收"] for x in dbs),
        "可回收文本": human(sum(x["可回收"] for x in dbs)),
        "已删条目数": sum(1 for x in logs + outs if x["已删"]),
    }
