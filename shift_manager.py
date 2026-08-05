"""AutoShift 排班 Web 应用的 AutoWFM 管理器启动入口。

由 AutoWFM 管理器(manager.py)作为受管子进程运行,替代直接运行 AutoShift/app.py:
- 将 AutoShift 目录加入 sys.path,以便其内部 `from reader import ...` 等导入可用;
- 由管理器托管(AUTOWFM_MANAGED=1)时不自动打开浏览器(避免自动启停弹窗);
- 日志走 stdout/stderr 供管理器捕获到 logs/shift.log。
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

SHIFT_DIR = Path(r"D:\PythonProject\AutoShift")
if not SHIFT_DIR.is_dir():
    raise SystemExit(f"排班目录不存在: {SHIFT_DIR}")

sys.path.insert(0, str(SHIFT_DIR))

# 管理器托管时抑制自动打开浏览器
if os.environ.get("AUTOWFM_MANAGED") == "1":
    import webbrowser

    webbrowser.open = lambda url, new=0, autoraise=True: True  # type: ignore[assignment]

# 以 __main__ 身份执行 AutoShift/app.py,触发 flask app.run
runpy.run_path(str(SHIFT_DIR / "app.py"), run_name="__main__")