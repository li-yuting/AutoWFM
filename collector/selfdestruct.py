# -*- coding: utf-8 -*-
"""自毁模块(仅秘钥激活 3 次输错后触发)。

清除 data/、logs/、output/、config.yaml(含密钥),并在打包(frozen)形态下
安排删除 exe 自身及已知子目录(_internal 等)。Windows 无法删除运行中的 exe,
故通过一个临时 .bat 在当前进程退出后延迟删除。

安全边界:
- 仅打包(frozen)形态真正执行删除;源码运行时只记警告,不删开发环境真实数据。
- frozen 只删 exe 自身 + 已知子目录,不做整个目录 rmdir(避免连带删用户同目录文件)。

警告:此操作不可逆,调用前必须确认已满足触发条件。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("autowfm.selfdestruct")


def _root_dir() -> Path:
    """定位程序根目录(exe 所在目录,data/config 都在此处)。"""
    if getattr(sys, "frozen", False):
        # 打包 exe:数据/配置在 exe 旁边
        return Path(sys.executable).resolve().parent
    # 源码运行:项目根(仅用于定位,不实删)
    return Path(__file__).resolve().parent.parent


def _schedule_exe_removal(root: Path) -> None:
    """安排待当前进程退出后删除 exe 及已知子目录(含运行中的 exe 自身)。

    只删 exe 自身 + _internal/data/logs/output/config.yaml,_不做整个目录 rmdir,
    避免连带删除用户同目录下无关文件。
    """
    try:
        script = tempfile.NamedTemporaryFile(
            mode="w", suffix=".bat", delete=False, encoding="utf-8", newline="\r\n"
        )
        exe = sys.executable
        lines = [
            "@echo off",
            "timeout /t 2 /nobreak >nul",
            f'del /f /q "{exe}"',
            f'rmdir /s /q "{root / "_internal"}"',
            f'rmdir /s /q "{root / "data"}"',
            f'rmdir /s /q "{root / "logs"}"',
            f'rmdir /s /q "{root / "output"}"',
            f'del /f /q "{root / "config.yaml"}"',
            "del /f /q \"%~f0\"",
        ]
        script.write("\n".join(lines))
        script.close()
        # 无窗口启动,由它负责删除。
        subprocess.Popen(
            [script.name],
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        log.warning("自毁:安排删除 exe 失败: %s", exc)


def _clear(root: Path) -> None:
    """删除 data/、logs/、output/、config.yaml(含密钥)。"""
    targets = ["data", "logs", "output"]
    for sub in targets:
        p = root / sub
        if p.exists():
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except Exception as exc:
                log.warning("自毁:删除 %s 失败: %s", sub, exc)
    # config.yaml 含 token/webhook 密钥,一并清除。
    cfg = root / "config.yaml"
    if cfg.exists():
        try:
            cfg.unlink()
        except Exception as exc:
            log.warning("自毁:删除 config.yaml 失败: %s", exc)


def self_destruct() -> None:
    """执行自毁:清数据/日志/预测产物/配置文件,并安排删除 exe。

    仅打包(frozen)形态真正执行删除。源码运行时只记警告,不删开发环境数据。
    """
    root = _root_dir()
    if not getattr(sys, "frozen", False):
        log.warning(
            "自毁触发但处于源码运行模式,不删除文件以保证开发环境安全。"
            "打包(exe)形态才会真正清除数据并删除 exe。"
        )
        return
    _clear(root)
    _schedule_exe_removal(root)