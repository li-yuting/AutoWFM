# 功能型子系统插件化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 peakflow / shift / member_limit / writeforecast 四个功能型子系统统一为「taskspec 声明 + 注册表扫描 + manager 通用页」的插件结构，新增子系统只建包+写 taskspec.py，manager 零改动。

**Architecture:** 每个子系统包根放 `taskspec.py` 暴露 `TASK: dict`；根目录新增 `task_registry.py` 负责 discover/validate；manager.py 删除预测与接待上限两个 bespoke 页，换成注册表驱动的通用任务页；service 型（shift）复用现有 ManagedTask 进程条。设计文档：`docs/superpowers/specs/2026-09-29-functional-subsystem-plugin-design.md`。

**Tech Stack:** Python 3.14、tkinter、argparse、importlib；无新依赖。

## Global Constraints

- **不动** collector / dashboard / api 主数据链；不动 `data/`、`output/`、`data/预估流入量.csv` 任何路径
- `python -m peakflow.main --fetch` 与 Windows 计划任务 `AutoWFM_Forecast` **不变**
- config.yaml / .env 现有键不变
- 测试为 plain assert 脚本，**无 pytest**；运行：`$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_<x>.py`
- 统一骨架：`main(argv=None) -> int` + argparse + 薄 `__main__.py`（`sys.exit(main())`）
- bg 型 callable 签名：`fn(progress_cb=None, should_cancel=None, **params) -> str`（返回摘要文本，异常即失败）
- commit 用 Conventional Commits（`feat:`/`refactor:`/`test:`/`docs:`）
- 沙箱执行 git 可能需要提权审批，属正常现象，按提示批准即可

## 任务依赖

Task 1（registry）→ Task 2/3/4/5（四个子系统，互相独立）→ Task 6（manager 集成）→ Task 7（文档+回归）

---

### Task 1: task_registry.py 注册表

**Files:**
- Create: `task_registry.py`
- Test: `tests/test_task_registry.py`

**Interfaces:**
- Produces:
  - `task_registry.PACKAGES = ("peakflow", "shift", "member_limit", "writeforecast")`
  - `task_registry.validate(spec: dict) -> list[str]`（返回错误列表，空 = 通过；**只做结构校验，不 import callable**，避免 manager 启动拉起 pandas/playwright）
  - `task_registry.discover(packages=PACKAGES) -> list[dict]`（加载 `<pkg>.taskspec.TASK`，失败的包跳过并 `log.warning`）
- Consumes: 各子系统 `<pkg>/taskspec.py` 的 `TASK: dict`（Task 2-5 提供，字段：`key/title/kind/callable/params/cancellable/schedulable/url/env/intro`）

- [ ] **Step 1: 写失败测试**

Create `tests/test_task_registry.py`:

```python
# -*- coding: utf-8 -*-
"""task_registry：discover / validate 行为。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib
import tempfile

import task_registry


def test_validate_ok():
    spec = {"key": "demo", "title": "演示", "kind": "bg",
            "callable": "demo.main:run",
            "params": [{"name": "x", "label": "X", "type": "int", "required": True}]}
    assert task_registry.validate(spec) == []


def test_validate_missing_required():
    errors = task_registry.validate({"kind": "bg"})
    assert errors, "缺 key/title/callable 应报错"


def test_validate_bad_kind():
    errors = task_registry.validate({"key": "d", "title": "d", "kind": "loop"})
    assert any("kind" in e for e in errors), errors


def test_validate_bg_requires_callable_format():
    errors = task_registry.validate({"key": "d", "title": "d", "kind": "bg",
                                     "callable": "no-colon"})
    assert any("callable" in e for e in errors), errors


def test_validate_bad_param():
    spec = {"key": "d", "title": "d", "kind": "bg", "callable": "a.b:c",
            "params": [{"name": "x", "type": "float"}]}
    assert task_registry.validate(spec), "未知 param 类型应报错"


def test_validate_select_requires_choices():
    spec = {"key": "d", "title": "d", "kind": "bg", "callable": "a.b:c",
            "params": [{"name": "k", "label": "K", "type": "select"}]}
    assert task_registry.validate(spec), "select 缺 choices 应报错"


def test_discover_skips_missing_package():
    assert task_registry.discover(packages=("no_such_pkg_xyz",)) == []


def test_discover_skips_invalid_spec():
    d = tempfile.mkdtemp()
    pkg = pathlib.Path(d) / "badpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "taskspec.py").write_text("TASK = {'key': 'badpkg'}\n", encoding="utf-8")
    sys.path.insert(0, d)
    try:
        assert task_registry.discover(packages=("badpkg",)) == []
    finally:
        sys.path.remove(d)


def main():
    test_validate_ok()
    test_validate_missing_required()
    test_validate_bad_kind()
    test_validate_bg_requires_callable_format()
    test_validate_bad_param()
    test_validate_select_requires_choices()
    test_discover_skips_missing_package()
    test_discover_skips_invalid_spec()
    print("ALL task_registry tests OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe tests\test_task_registry.py`
Expected: `ModuleNotFoundError: No module named 'task_registry'`

- [ ] **Step 3: 实现 task_registry.py**

Create `task_registry.py`:

```python
# -*- coding: utf-8 -*-
"""功能型子系统注册表：扫描各包 taskspec.py，结构校验后交给 manager 渲染。

只做结构校验，不 import callable 目标模块（避免 manager 启动时拉起
pandas/playwright 等重依赖）；可导入性由 tests/test_taskspec.py 断言，
运行期导入失败由任务页显示错误。
"""
from __future__ import annotations

import importlib
import logging

log = logging.getLogger("manager")

PACKAGES = ("peakflow", "shift", "member_limit", "writeforecast")

_REQUIRED = ("key", "title", "kind")
_KINDS = ("bg", "service")
_PARAM_TYPES = ("int", "str", "select")


def validate(spec: dict) -> list[str]:
    """结构校验：返回错误列表，空列表 = 通过。"""
    errors = []
    for field in _REQUIRED:
        if not spec.get(field):
            errors.append(f"缺字段 {field}")
    kind = spec.get("kind")
    if kind not in _KINDS:
        errors.append(f"kind 需为 bg/service，当前 {kind!r}")
    if kind == "bg":
        callable_path = spec.get("callable") or ""
        if ":" not in callable_path:
            errors.append("bg 任务需 callable 'pkg.mod:func'")
    for p in spec.get("params", []):
        if not p.get("name") or p.get("type") not in _PARAM_TYPES:
            errors.append(f"param 非法: {p!r}")
        elif p["type"] == "select" and not p.get("choices"):
            errors.append(f"select param 缺 choices: {p!r}")
    return errors


def discover(packages=PACKAGES) -> list[dict]:
    """加载各包 taskspec.TASK；加载/校验失败的包跳过并告警，不影响其他包。"""
    specs = []
    for pkg in packages:
        try:
            spec = importlib.import_module(f"{pkg}.taskspec").TASK
        except Exception as exc:
            log.warning("加载 %s.taskspec 失败: %s", pkg, exc)
            continue
        errors = validate(spec)
        if errors:
            log.warning("taskspec %s 校验失败，已跳过: %s", pkg, "; ".join(errors))
            continue
        specs.append(spec)
    return specs
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.\.venv\Scripts\python.exe tests\test_task_registry.py`
Expected: `ALL task_registry tests OK`

- [ ] **Step 5: Commit**

```powershell
git add task_registry.py tests/test_task_registry.py
git commit -m "feat(registry): 功能型子系统注册表 discover/validate"
```

---

### Task 2: member_limit 插件化

**Files:**
- Create: `member_limit/__main__.py`、`member_limit/taskspec.py`
- Modify: `member_limit/main.py`（加 `run()`）
- Test: `tests/test_member_limit_main.py`（追加用例）

**Interfaces:**
- Consumes: `member_limit.config.load() -> dict`、`member_limit.config.ConfigError`、`member_limit.core.run_member_limit(config, progress_cb=None, should_cancel=None, dry_run=False) -> dict`（均已存在）
- Produces:
  - `member_limit.main.run(limit: int, progress_cb=None, should_cancel=None) -> str`（manager 后台任务入口；空名单抛 `ConfigError`；汇总已由 core 经 progress_cb 输出，返回 `""`）
  - `member_limit.taskspec.TASK`（key=`member_limit`，schedulable+cancellable）
  - `python -m member_limit` 等价于 `python -m member_limit.main`

- [ ] **Step 1: 写失败测试**

Append to `tests/test_member_limit_main.py`（在 `def main():` 之前）:

```python
def test_run_wrapper_rejects_empty_members():
    with patch("member_limit.main.load_config") as load:
        load.return_value = {"members": []}
        try:
            cli.run(3)
            raise AssertionError("空名单应抛 ConfigError")
        except ConfigError:
            pass


def test_run_wrapper_forwards_limit_and_callbacks():
    with patch("member_limit.main.load_config") as load, \
         patch("member_limit.core.run_member_limit") as core_run:
        load.return_value = {"members": ["张三"], "limit": 5}
        cancel = lambda: False
        assert cli.run(7, progress_cb=print, should_cancel=cancel) == ""
        cfg = core_run.call_args[0][0]
        assert cfg["limit"] == 7, cfg
        assert core_run.call_args[1]["should_cancel"] is cancel
```

并在该文件 `main()` 的调用列表末尾（`print(...)` 之前）追加两行调用：

```python
    test_run_wrapper_rejects_empty_members()
    test_run_wrapper_forwards_limit_and_callbacks()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe tests\test_member_limit_main.py`
Expected: `AttributeError: module 'member_limit.main' has no attribute 'run'`

- [ ] **Step 3: 实现**

Modify `member_limit/main.py` — 在 `def main(argv=None)` 之前插入：

```python
def run(limit: int, progress_cb=None, should_cancel=None) -> str:
    """manager 后台任务入口：与 CLI 共用 core.run_member_limit。

    汇总已由 core 经 progress_cb 逐行输出，故返回空串。
    """
    config = load_config()
    if not (config.get("members") or []):
        raise ConfigError("config.yaml 的 member_limit.members 为空，请先配置")
    config["limit"] = limit
    core.run_member_limit(config, progress_cb=progress_cb,
                          should_cancel=should_cancel)
    return ""
```

Create `member_limit/__main__.py`:

```python
# -*- coding: utf-8 -*-
"""python -m member_limit —— 薄转发到 main.main。"""
import sys

from member_limit.main import main

if __name__ == "__main__":
    sys.exit(main())
```

Create `member_limit/taskspec.py`:

```python
# -*- coding: utf-8 -*-
"""manager 插件声明：接待上限批量修改。"""

TASK = {
    "key": "member_limit",
    "title": "接待上限",
    "kind": "bg",
    "callable": "member_limit.main:run",
    "params": [
        {"name": "limit", "label": "上限值", "type": "int", "required": True,
         "default": 3, "default_cfg": "member_limit.limit", "width": 6},
    ],
    "cancellable": True,
    "schedulable": True,
    "intro": (
        "手动执行：填上限值（0 或正整数）后点「开始执行」；运行中可点「停止」逐人中断。\n"
        "预约执行：勾选启用 + 填 HH:MM 时间与上限值，到点自动执行一次后清除。\n"
        "日志同时写入 logs/member_limit.log。"
    ),
}
```

- [ ] **Step 4: 跑测试 + CLI 冒烟**

Run: `.\.venv\Scripts\python.exe tests\test_member_limit_main.py`
Expected: `ALL member_limit main tests OK`（或该文件原有的 OK 结尾行）

Run: `.\.venv\Scripts\python.exe -m member_limit --help`
Expected: 打印 argparse 用法，退出码 0

- [ ] **Step 5: Commit**

```powershell
git add member_limit/__main__.py member_limit/taskspec.py member_limit/main.py tests/test_member_limit_main.py
git commit -m "feat(member_limit): 插件化入口 run() + taskspec + __main__"
```

---

### Task 3: peakflow 插件化

**Files:**
- Create: `peakflow/taskspec.py`
- Modify: `peakflow/main.py`（加 `forecast_summary()` + `run_task()`）
- Test: `tests/test_peakflow_main.py`（追加用例）

**Interfaces:**
- Consumes: `peakflow.main.run_forecast(fetch=False, out_dir=None) -> Path`（已存在）
- Produces:
  - `peakflow.main.forecast_summary(out_path) -> str`（摘要文本，从 manager 的 `_forecast_summary` 原样搬入）
  - `peakflow.main.run_task(progress_cb=None, should_cancel=None) -> str`
  - `peakflow.taskspec.TASK`（key=`peakflow`，无 params）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_peakflow_main.py`（在 `def main():` 之前；该文件已有 `from peakflow import config, main as main_mod` 与 `from pathlib import Path`）:

```python
def test_forecast_summary():
    # Path 归一化输出（Windows 反斜杠 / Linux 正斜杠），断言跨平台一致
    out = Path("output/x.xlsx")
    s = main_mod.forecast_summary(out)
    assert f"Excel: {out}" in s, s
    assert f"HTML:  {out.with_suffix('.html')}" in s, s
```

并在该文件 `main()` 调用列表末尾追加：

```python
    test_forecast_summary()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe tests\test_peakflow_main.py`
Expected: `AttributeError: ... has no attribute 'forecast_summary'`

- [ ] **Step 3: 实现**

Modify `peakflow/main.py` — 在 `def main(argv=None)` 之前插入：

```python
def forecast_summary(out_path) -> str:
    """预测产物摘要（manager 任务页与测试共用）。"""
    path = Path(out_path)
    return "\n".join([
        "预测完成！",
        f"Excel: {path}",
        f"HTML:  {path.with_suffix('.html')}",
        "",
        "用 Excel 打开查看总览/分类型明细/回测误差/运行说明。",
        "用浏览器打开 HTML 查看可视化图表。",
    ])


def run_task(progress_cb=None, should_cancel=None) -> str:
    """manager 后台任务入口：同步 AutoTableau 数据并预测未来 30 天。"""
    return forecast_summary(run_forecast(fetch=True))
```

Create `peakflow/taskspec.py`:

```python
# -*- coding: utf-8 -*-
"""manager 插件声明：30 天进线量预测。"""

TASK = {
    "key": "peakflow",
    "title": "进线量预测",
    "kind": "bg",
    "callable": "peakflow.main:run_task",
    "params": [],
    "intro": (
        "点「开始执行」从 AutoTableau 同步最新数据并预测未来 30 天。\n"
        "每天 09:30 Windows 计划任务自动运行。\n"
        "输出: output/YYYY-MM-DD/预测_YYYYMMDD_未来30天.xlsx + .html"
    ),
}
```

- [ ] **Step 4: 跑测试 + CLI 冒烟（计划任务路径回归）**

Run: `.\.venv\Scripts\python.exe tests\test_peakflow_main.py`
Expected: 全部 OK

Run: `.\.venv\Scripts\python.exe -m peakflow.main --help`
Expected: 打印 argparse 用法，退出码 0（证明计划任务命令入口未被破坏）

- [ ] **Step 5: Commit**

```powershell
git add peakflow/taskspec.py peakflow/main.py tests/test_peakflow_main.py
git commit -m "feat(peakflow): manager 入口 run_task/forecast_summary + taskspec"
```

---

### Task 4: shift 插件化

**Files:**
- Create: `shift/__main__.py`、`shift/taskspec.py`
- Modify: `shift/main.py`（`main()` 加 `argv=None` + sys.path 自保）、`shift/app.py`（`__main__` 块抽 `serve()`）
- Test: `tests/test_shift_utils.py` 不动；新增用例放 `tests/test_taskspec.py`（Task 6 创建，此处不动）

**Interfaces:**
- Consumes: 无
- Produces:
  - `shift.app.serve() -> None`（原 `__main__` 块的浏览器定时器 + `app.run(127.0.0.1:5000)`）
  - `shift.main.main(argv=None) -> int`（原为 `main()`，行为不变）
  - `python -m shift` 启动 Flask 服务（内部完成 sys.path 注入与 `AUTOWFM_MANAGED=1` 时浏览器抑制，替代 manager 的 `_SHIFT_PRE_RUN` runpy hack）
  - `shift.taskspec.TASK`（key=`shift`，kind=`service`，含 `url` 与 `env`）

- [ ] **Step 1: 冒烟验证现状（改前基准）**

Run: `.\.venv\Scripts\python.exe tests\test_shift_pipeline.py`
Expected: 全部 OK（记录基线，改后须一致）

- [ ] **Step 2: 实现 `shift/app.py` 抽 serve()**

Modify `shift/app.py` — 把文件末尾：

```python
if __name__ == "__main__":
    threading.Timer(1.0, _open_browser).start()
    app.run(host="127.0.0.1", port=5000, use_reloader=False)
```

改为：

```python
def serve() -> None:
    threading.Timer(1.0, _open_browser).start()
    app.run(host="127.0.0.1", port=5000, use_reloader=False)


if __name__ == "__main__":
    serve()
```

- [ ] **Step 3: 实现 `shift/__main__.py`**

Create `shift/__main__.py`:

```python
# -*- coding: utf-8 -*-
"""python -m shift：排班 Web 服务（manager 守护入口）。

shift 内部为扁平 import（reader/scheduler/...），这里把包目录注入 sys.path；
AUTOWFM_MANAGED=1 时抑制自动开浏览器（由 manager 托管）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if os.environ.get("AUTOWFM_MANAGED") == "1":
    import webbrowser
    webbrowser.open = lambda url, new=0, autoraise=True: True

from app import serve  # noqa: E402  扁平 import 需先注入 sys.path

serve()
```

- [ ] **Step 4: 实现 `shift/main.py` argv + sys.path 自保**

Modify `shift/main.py` — 文件头部的 import 区：

```python
from __future__ import annotations

import argparse
from pathlib import Path

from reader import read_schedule
from scheduler import SchedulerConfig, run_scheduler
from validators import validate_schedule
from writer import write_schedule
```

改为：

```python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reader import read_schedule
from scheduler import SchedulerConfig, run_scheduler
from validators import validate_schedule
from writer import write_schedule
```

`def main() -> int:` 改为 `def main(argv=None) -> int:`；`args = parser.parse_args()` 改为 `args = parser.parse_args(argv)`。

- [ ] **Step 5: 实现 `shift/taskspec.py`**

Create `shift/taskspec.py`:

```python
# -*- coding: utf-8 -*-
"""manager 插件声明：排班 Web 服务（守护进程型）。"""

TASK = {
    "key": "shift",
    "title": "排班",
    "kind": "service",
    "url": "http://127.0.0.1:5000",
    "env": {"AUTOWFM_MANAGED": "1"},
}
```

- [ ] **Step 6: 验证**

Run: `.\.venv\Scripts\python.exe tests\test_shift_pipeline.py; .\.venv\Scripts\python.exe tests\test_shift_utils.py; .\.venv\Scripts\python.exe tests\test_shift_validators.py`
Expected: 与基线一致，全 OK

Run: `.\.venv\Scripts\python.exe -m shift`（另开窗口或用 `run_in_background`；启动后访问 http://127.0.0.1:5000 应返回排班页面，然后 Ctrl+C / job_kill 停止）
Expected: 服务正常启动、5000 端口可访问；`AUTOWFM_MANAGED=1` 环境下不弹浏览器（可在后台 job 验证：`$env:AUTOWFM_MANAGED="1"; .\.venv\Scripts\python.exe -m shift`）

- [ ] **Step 7: Commit**

```powershell
git add shift/__main__.py shift/taskspec.py shift/main.py shift/app.py
git commit -m "feat(shift): python -m shift 入口，内化 sys.path 与浏览器抑制"
```

---

### Task 5: writeforecast 转包 + 插件化

**Files:**
- Create: `writeforecast/__init__.py`、`writeforecast/__main__.py`、`writeforecast/main.py`、`writeforecast/taskspec.py`
- Modify: `writeforecast/writeforecast.py`（`sys.exit` → raise，抽 `main(argv)`）、`writeforecast/时段人力数架构准备_v2.py`（同上）
- Test: `tests/test_writeforecast.py`（新建）

**Interfaces:**
- Consumes: 无（pandas 已装）
- Produces:
  - `writeforecast.writeforecast.transform_forecast(xlsx_path: Path) -> None`（签名不变；**错误改为 raise**，不再 `sys.exit`）
  - `writeforecast.writeforecast.main(argv=None) -> int`
  - `writeforecast.时段人力数架构准备_v2.transform_schedule(xlsx_path: Path) -> None`（同样 raise 化）
  - `writeforecast.时段人力数架构准备_v2.main(argv=None) -> int`
  - `writeforecast.main.main(argv=None) -> int`（子命令 `forecast|shifts [xlsx]`）
  - `writeforecast.main.run(kind: str, path: str, progress_cb=None, should_cancel=None) -> str`（manager 入口）
  - `writeforecast.taskspec.TASK`（key=`writeforecast`）

- [ ] **Step 1: 写失败测试**

Create `tests/test_writeforecast.py`:

```python
# -*- coding: utf-8 -*-
"""writeforecast：转包后的入口骨架与错误传播。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path


def test_transform_forecast_missing_file_raises():
    from writeforecast.writeforecast import transform_forecast
    try:
        transform_forecast(Path("不存在_12345.xlsx"))
        raise AssertionError("缺文件应 raise，不再 sys.exit")
    except FileNotFoundError:
        pass


def test_transform_schedule_missing_file_raises():
    import importlib
    mod = importlib.import_module("writeforecast.时段人力数架构准备_v2")
    try:
        mod.transform_schedule(Path("不存在_12345.xlsx"))
        raise AssertionError("缺文件应 raise，不再 sys.exit")
    except FileNotFoundError:
        pass


def test_script_main_returns_int():
    from writeforecast import writeforecast as wf
    assert wf.main(["不存在_12345.xlsx"]) == 1  # 错误收敛为退出码 1


def test_pkg_main_dispatch_help():
    from writeforecast.main import main
    try:
        main(["--help"])
        raise AssertionError("--help 应触发 SystemExit(0)")
    except SystemExit as exc:
        assert exc.code == 0


def main():
    test_transform_forecast_missing_file_raises()
    test_transform_schedule_missing_file_raises()
    test_script_main_returns_int()
    test_pkg_main_dispatch_help()
    print("ALL writeforecast tests OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe tests\test_writeforecast.py`
Expected: `ModuleNotFoundError: No module named 'writeforecast'`（或 transform 内 `SystemExit` 而非 `FileNotFoundError`）

- [ ] **Step 3: 改造 `writeforecast/writeforecast.py`**

三处错误处理替换（保留 print 警告不变）：

`transform_forecast` 开头改为：

```python
def transform_forecast(xlsx_path: Path) -> None:
    """将周度预测 Excel 转为按时间、线路展开的长表，追加到预估流入量总表"""
    if not xlsx_path.exists():
        raise FileNotFoundError(f"文件不存在 — {xlsx_path}")

    try:
        sheets = pd.read_excel(xlsx_path, sheet_name=None)
    except Exception as e:
        raise RuntimeError(f"读取 Excel 失败 — {e}") from e
```

文件末尾 `if __name__ == "__main__":` 块整体替换为：

```python
def main(argv=None) -> int:
    xlsx = Path(argv[0]) if argv else DATA_DIR / "量级预估20260920.xlsx"
    try:
        transform_forecast(xlsx)
    except Exception as e:
        print(f"错误: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: 改造 `writeforecast/时段人力数架构准备_v2.py`**

`transform_schedule` 开头三处错误处理改为：

```python
    if not xlsx_path.exists():
        raise FileNotFoundError(f"文件不存在 — {xlsx_path}")

    try:
        df = pd.read_excel(xlsx_path)
    except Exception as e:
        raise RuntimeError(f"读取 Excel 失败 — {e}") from e

    expected_cols = {"班组", "姓名", "入职日期"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(f"缺少必要列 {missing}，当前列: {list(df.columns)}")
```

文件末尾 `if __name__ == "__main__":` 块整体替换为：

```python
def main(argv=None) -> int:
    xlsx = Path(argv[0]) if argv else DATA_DIR / "班表.xlsx"
    try:
        transform_schedule(xlsx)
    except Exception as e:
        print(f"错误: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: 包化文件**

Create `writeforecast/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""writeforecast 包：周度预估写入 data/预估流入量.csv + 班表转时段人力架构表。"""
```

Create `writeforecast/__main__.py`:

```python
# -*- coding: utf-8 -*-
"""python -m writeforecast —— 薄转发到 main.main。"""
import sys

from writeforecast.main import main

if __name__ == "__main__":
    sys.exit(main())
```

Create `writeforecast/main.py`:

```python
# -*- coding: utf-8 -*-
"""CLI/manager 入口：python -m writeforecast forecast|shifts [xlsx]。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run(kind: str, path: str, progress_cb=None, should_cancel=None) -> str:
    """manager 后台任务入口；异常直接抛出由调用方呈现。"""
    xlsx = Path(path)
    if kind == "forecast":
        from writeforecast.writeforecast import transform_forecast
        transform_forecast(xlsx)
        return f"已写入预估流入量: {xlsx}"
    from writeforecast.时段人力数架构准备_v2 import transform_schedule
    transform_schedule(xlsx)
    return f"已生成时段人力架构表: {xlsx.with_stem(xlsx.stem + '_结果')}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="周度预估写入 / 时段人力架构准备")
    ap.add_argument("kind", choices=["forecast", "shifts"],
                    help="forecast=周度预估→预估流入量.csv；shifts=班表→时段人力架构表")
    ap.add_argument("xlsx", nargs="?", default=None, help="输入 Excel 路径（缺省用各脚本默认文件）")
    args = ap.parse_args(argv)
    forward = [args.xlsx] if args.xlsx else []
    if args.kind == "forecast":
        from writeforecast import writeforecast as wf
        return wf.main(forward)
    import importlib
    sf = importlib.import_module("writeforecast.时段人力数架构准备_v2")
    return sf.main(forward)


if __name__ == "__main__":
    sys.exit(main())
```

Create `writeforecast/taskspec.py`:

```python
# -*- coding: utf-8 -*-
"""manager 插件声明：周度预估写入 / 时段人力架构准备。"""

TASK = {
    "key": "writeforecast",
    "title": "写入预估",
    "kind": "bg",
    "callable": "writeforecast.main:run",
    "params": [
        {"name": "kind", "label": "类型", "type": "select",
         "choices": ["forecast", "shifts"], "default": "forecast", "width": 10},
        {"name": "path", "label": "Excel路径", "type": "str",
         "required": True, "width": 36},
    ],
    "intro": (
        "类型 forecast：周度预估 Excel → 追加写入 data/预估流入量.csv（按 时间+线路 去重）。\n"
        "类型 shifts：班表 Excel → 生成 <文件名>_结果.xlsx 时段人力架构表。\n"
        "请填写 Excel 的完整路径。"
    ),
}
```

- [ ] **Step 6: 跑测试 + 直跑回归**

Run: `.\.venv\Scripts\python.exe tests\test_writeforecast.py`
Expected: `ALL writeforecast tests OK`

Run: `.\.venv\Scripts\python.exe -m writeforecast --help` 与 `.\.venv\Scripts\python.exe writeforecast\writeforecast.py 不存在_12345.xlsx`
Expected: 前者打印用法退出码 0；后者打印 `错误: 文件不存在 — ...` 退出码 1（直跑脚本行为保持）

- [ ] **Step 7: Commit**

```powershell
git add writeforecast/__init__.py writeforecast/__main__.py writeforecast/main.py writeforecast/taskspec.py writeforecast/writeforecast.py writeforecast/时段人力数架构准备_v2.py tests/test_writeforecast.py
git commit -m "refactor(writeforecast): 转包 + main(argv) 骨架 + taskspec，错误改 raise"
```

---

### Task 6: manager.py 通用任务页 + 注册表集成

**Files:**
- Modify: `manager.py`（删约 250 行 bespoke 代码，加约 200 行通用代码）
- Test: `tests/test_manager.py`（改 3 处 + 加新用例）、Create `tests/test_taskspec.py`

**Interfaces:**
- Consumes: Task 1 的 `task_registry.discover()`；Task 2/3/5 的 bg 型 TASK；Task 4 的 service 型 TASK
- Produces:
  - `manager.service_task_def(spec: dict, log_dir: Path, log_max_mb: int) -> dict`（service taskspec → ManagedTask 构造参数，模块级纯函数）
  - `ManagerUI._build_task_page(page, spec) -> None`（page 由调用方创建并 grid）、`_manual_run_task(spec)`、`_run_task(spec, params, label) -> bool`、`_stop_task(spec)`、`_on_task_done(spec, summary, err, label)`、`_append_task_text(key, text)`、`_build_task_schedule(page, spec)`、`_check_task_schedules(now)`、`ManagerUI._parse_task_params(spec, vars_) -> dict`（staticmethod）
  - `ManagedTask.url` 属性（默认 `None`，service 任务由 taskspec `url` 填充，进程条显示「打开页面」）

- [ ] **Step 1: 写失败测试 ① — tests/test_taskspec.py（契约总闸）**

Create `tests/test_taskspec.py`:

```python
# -*- coding: utf-8 -*-
"""四个功能型子系统的 taskspec 契约 + 骨架入口签名。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib
import inspect

import task_registry

PACKAGES = ("peakflow", "shift", "member_limit", "writeforecast")


def test_taskspec_contract():
    for pkg in PACKAGES:
        spec = importlib.import_module(f"{pkg}.taskspec").TASK
        assert spec["key"] == pkg, (pkg, spec["key"])
        assert spec["title"] and spec["kind"] in ("bg", "service"), spec
        assert task_registry.validate(spec) == [], (pkg, task_registry.validate(spec))


def test_discover_loads_all_four():
    specs = task_registry.discover()
    keys = {s["key"] for s in specs}
    assert {"peakflow", "shift", "member_limit", "writeforecast"} <= keys, keys


def test_bg_callable_signature():
    """bg 任务 callable：可导入且接受 progress_cb/should_cancel。"""
    for pkg in PACKAGES:
        spec = importlib.import_module(f"{pkg}.taskspec").TASK
        if spec["kind"] != "bg":
            continue
        mod_name, func_name = spec["callable"].split(":")
        fn = getattr(importlib.import_module(mod_name), func_name)
        params = inspect.signature(fn).parameters
        assert "progress_cb" in params and "should_cancel" in params, (pkg, spec["callable"])


def test_main_skeleton():
    """统一骨架：各包 main(argv=None) -> int。"""
    for mod_name in ("peakflow.main", "member_limit.main", "writeforecast.main", "shift.main"):
        fn = importlib.import_module(mod_name).main
        first = list(inspect.signature(fn).parameters.values())
        assert first and first[0].name == "argv", mod_name


def main():
    test_taskspec_contract()
    test_discover_loads_all_four()
    test_bg_callable_signature()
    test_main_skeleton()
    print("ALL taskspec tests OK")


if __name__ == "__main__":
    main()
```

Run: `.\.venv\Scripts\python.exe tests\test_taskspec.py`
Expected: PASS（Task 1-5 已就位；此测试是把四件套钉在一起的总闸，应先通过。若失败说明前序任务有遗漏，先补齐再继续）

- [ ] **Step 2: 写失败测试 ② — test_manager.py 新用例**

Append to `tests/test_manager.py`（`def main():` 之前）:

```python
def test_service_task_def():
    """service taskspec → ManagedTask 构造参数。"""
    from manager import service_task_def
    spec = {"key": "shift", "title": "排班", "kind": "service",
            "url": "http://127.0.0.1:5000", "env": {"AUTOWFM_MANAGED": "1"}}
    d = service_task_def(spec, Path("logs"), 5)
    assert d["name"] == "排班" and d["module"] == "shift"
    assert d["log_path"] == Path("logs") / "shift.log"
    assert d["env_extra"] == {"AUTOWFM_MANAGED": "1"}
    assert d["auto_enabled"] is False
    print("service_task_def OK")


class _Var:
    """StringVar 替身（_parse_task_params 只依赖 .get()）。"""
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def test_parse_task_params():
    spec = {"params": [
        {"name": "limit", "label": "上限", "type": "int", "required": True},
        {"name": "kind", "label": "类型", "type": "select", "choices": ["a", "b"]},
    ]}
    out = ManagerUI._parse_task_params(spec, {"limit": _Var("3"), "kind": _Var("a")})
    assert out == {"limit": 3, "kind": "a"}, out
    for bad in ("", "-1", "abc"):
        try:
            ManagerUI._parse_task_params(spec, {"limit": _Var(bad), "kind": _Var("a")})
            raise AssertionError(f"{bad!r} 应抛 ValueError")
        except ValueError:
            pass
    print("parse_task_params OK")
```

并在 `main()` 调用列表末尾追加：

```python
    test_service_task_def()
    test_parse_task_params()
```

同时**修改两处既有用例**：

1. `test_forecast_summary`（约 129-135 行）整个函数删除，并把 `main()` 调用列表里的 `test_forecast_summary()` 一并删除（该测试已搬到 tests/test_peakflow_main.py）。
2. `test_member_limit_schedule_rearm`（约 321-351 行）改为通用结构：

```python
def test_member_limit_schedule_rearm():
    """预约触发后(fired=True)重新勾选启用,应重置 fired 允许第二次预约;
    已勾选但时间未填(idle)不应被静默取消勾选。"""
    with patch.object(ManagerUI, "_build_tray", lambda self: None), \
         patch.object(ManagerUI, "_refresh", lambda self: None):
        root = tk.Tk()
        root.withdraw()
        ui = ManagerUI(root, _cfg())
        try:
            now = dt.datetime(2026, 8, 21, 10, 0)
            # 场景1: 上次已触发,用户重新勾选 -> 重新武装进入等待
            st = ui._task_state["member_limit"]["sched"][0]
            st["enabled"].set(True)
            st["time"].set("11:00")
            st["vars"]["limit"].set("3")
            st["fired"] = True
            st["status"].set("已执行")
            ui._check_task_schedules(now)
            assert st["fired"] is False, "重新勾选后应重置 fired,允许第二次预约"
            assert st["status"].get() == "等待预约"
            assert st["enabled"].get() is True, "等待到点中不应被取消勾选"
            # 场景2: 勾选了但时间还没填完(idle) -> 不应被静默取消
            st2 = ui._task_state["member_limit"]["sched"][1]
            st2["enabled"].set(True)
            st2["time"].set("")
            ui._check_task_schedules(now)
            assert st2["enabled"].get() is True, "时间未填(idle)不应取消勾选"
            assert st2["fired"] is False
        finally:
            root.destroy()
    print("member_limit_schedule_rearm OK")
```

3. `test_ui_constructs` 中的计数断言改为：

```python
            assert len(ui._nav_buttons) == 9, f"9 个导航按钮, 实际 {len(ui._nav_buttons)}"
            assert len(ui._nav_pages) == 9, f"9 个内容页, 实际 {len(ui._nav_pages)}"
            assert len(ui._log_boxes) == 4, f"4 个日志框, 实际 {len(ui._log_boxes)}"
```

（9 = 4 日志页 + 数据补全 + 导出 CSV + 3 个 bg 任务页；4 = 采集器/API/看板/排班日志框。）

- [ ] **Step 3: 跑测试确认失败**

Run: `.\.venv\Scripts\python.exe tests\test_manager.py`
Expected: 失败（`ImportError: cannot import name 'service_task_def'` 或 nav 计数断言失败）

- [ ] **Step 4: 实现 — 删除 bespoke 代码**

对 `manager.py` 做以下删除（行号以当前文件为准，按函数名定位）：

1. 删 `_SHIFT_PRE_RUN` 与 `_SHIFT_LAUNCHER` 两个常量（541-551 行附近）
2. `TASK_DEFS` 中删「排班」条目（`dict(name="排班", ...)`），只留采集器/API/看板
3. 删 `_build_forecast_page`、`_run_forecast`、`_on_forecast_done`、`_forecast_summary`（936-989 行）
4. 删 `_build_member_limit_page`、`_append_member_limit_text`、`_manual_member_limit`、`_run_member_limit`、`_stop_member_limit`、`_on_member_limit_progress`、`_on_member_limit_done`、`_check_member_limit_schedules`（1070-1218 行），以及不再被引用的 `MEMBER_LIMIT_LOG` 常量（47 行；通用页改用 `LOG_DIR / f"{key}.log"`，同名文件不变）
5. `__init__` 中删 `self._ml_running` / `self._ml_cancel` / `self._ml_sched` 三行（594-596 行附近）
6. `_build_ui` 中删 forecast/member_limit 两个 page 构建块（663-674 行附近的 fc_page 与 ml_page 两段）
7. `_refresh` 中 `self._check_member_limit_schedules(now)` 改为 `self._check_task_schedules(now)`
8. 文件 docstring 第 3 行「与排班(`shift/app.py`)子进程」改为「与排班(`python -m shift`)子进程」

- [ ] **Step 5: 实现 — 加入注册表与通用代码**

Modify `manager.py`:

① import 区（`from collector.repository import list_sources` 之后）追加：

```python
import importlib
import webbrowser

import task_registry
```

② `service_task_def` 模块级函数（放在 `ManagedTask` 类定义之前）：

```python
def service_task_def(spec: dict, log_dir: Path, log_max_mb: int) -> dict:
    """service 型 taskspec → ManagedTask 构造参数（复用进程条+日志页机制）。"""
    return dict(name=spec["title"], module=spec["key"],
                log_path=log_dir / f"{spec['key']}.log", capture_log=True,
                cwd=ROOT, env_extra=spec.get("env") or {},
                auto_enabled=False, log_max_mb=log_max_mb)
```

③ `ManagedTask.__init__` 中 `self._log_handle = None` 之后加一行：

```python
        self.url: str | None = None             # service 任务的 Web 入口(taskspec url)，进程条显示「打开页面」
```

④ `ManagerUI.__init__`：把

```python
        self.tasks: list[ManagedTask] = [
            ManagedTask(**d, log_max_mb=_maint_cfg(cfg)["child_log_max_mb"]) for d in TASK_DEFS
        ]
```

改为：

```python
        self._task_specs = task_registry.discover()
        self._bg_specs = [s for s in self._task_specs if s["kind"] == "bg"]
        _log_max = _maint_cfg(cfg)["child_log_max_mb"]
        self.tasks: list[ManagedTask] = [
            ManagedTask(**d, log_max_mb=_log_max) for d in TASK_DEFS
        ]
        for _s in self._task_specs:
            if _s["kind"] == "service":
                _t = ManagedTask(**service_task_def(_s, LOG_DIR, _log_max))
                _t.url = _s.get("url")
                self.tasks.append(_t)
        self._task_state = {
            s["key"]: {"running": False, "cancel": False, "sched": []} for s in self._bg_specs
        }
```

⑤ `_build_ui`：在 ex_page 块之后、工具按钮之前，把 bg 任务页加入导航：

```python
        for spec in self._bg_specs:
            t_page = tk.Frame(content)
            t_page.grid(row=0, column=0, sticky="nsew")
            self._build_task_page(t_page, spec)
            nav_items.append((spec["title"], t_page))
```

⑥ 进程条 url 按钮：`_build_ui` 的 task 行按钮区（「重启」按钮那行之后）加：

```python
            if getattr(task, "url", None):
                tk.Button(btns, text="打开页面", width=8,
                          command=lambda u=task.url: webbrowser.open(u)).pack(side=tk.LEFT, padx=3)
```

⑦ 通用任务页方法（加在 `ManagerUI` 内，`_build_export_page` 之前的注释分隔区处）：

```python
    # ---- 注册表驱动的通用任务页(bg 型功能子系统)----
    def _build_task_page(self, page: tk.Frame, spec: dict) -> None:
        key = spec["key"]
        st = self._task_state[key]
        st["log_path"] = LOG_DIR / f"{key}.log"

        top = tk.Frame(page, padx=10, pady=8)
        top.pack(fill=tk.X)
        vars_ = {}
        for p in spec.get("params", []):
            tk.Label(top, text=f"{p['label']}:").pack(side=tk.LEFT)
            default = p.get("default", "")
            if p.get("default_cfg"):  # 从 config.yaml 取缺省值，如 "member_limit.limit"
                node = self.cfg
                for part in p["default_cfg"].split("."):
                    if not isinstance(node, dict):
                        node = None
                        break
                    node = node.get(part)
                if node is not None:
                    default = node
            var = tk.StringVar(value=str(default))
            vars_[p["name"]] = var
            if p["type"] == "select":
                ttk.Combobox(top, width=p.get("width", 10), textvariable=var,
                             values=p["choices"], state="readonly").pack(side=tk.LEFT, padx=4)
            else:
                tk.Entry(top, width=p.get("width", 8), textvariable=var).pack(side=tk.LEFT, padx=4)
        st["vars"] = vars_

        btn_run = tk.Button(top, text="开始执行", width=10,
                            command=lambda s=spec: self._manual_run_task(s))
        btn_run.pack(side=tk.LEFT, padx=6)
        btns = {"run": btn_run}
        if spec.get("cancellable"):
            btn_stop = tk.Button(top, text="停止", width=8, state=tk.DISABLED,
                                 command=lambda s=spec: self._stop_task(s))
            btn_stop.pack(side=tk.LEFT, padx=6)
            btns["stop"] = btn_stop
        st["btns"] = btns
        st["status"] = tk.StringVar(value="就绪")
        tk.Label(top, textvariable=st["status"], fg="#555555").pack(side=tk.LEFT, padx=10)

        if spec.get("schedulable"):
            self._build_task_schedule(page, spec)

        box = scrolledtext.ScrolledText(page, wrap=tk.WORD, font=("Consolas", 10))
        box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        box.configure(state=tk.DISABLED)
        st["box"] = box
        if spec.get("intro"):
            self._box_set(box, spec["intro"])

    def _build_task_schedule(self, page: tk.Frame, spec: dict) -> None:
        st = self._task_state[spec["key"]]
        sched = tk.Frame(page, padx=10, pady=4)
        sched.pack(fill=tk.X)
        tk.Label(sched, text="预约运行:", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        for k in (1, 2):
            row_st = {"enabled": tk.BooleanVar(value=False),
                      "time": tk.StringVar(value=""),
                      "status": tk.StringVar(value="等待预约"),
                      "fired": False,
                      "vars": {}}
            st["sched"].append(row_st)
            row = tk.Frame(sched)
            row.pack(fill=tk.X, pady=2, anchor="w")
            tk.Checkbutton(row, text=f"预约{k}", variable=row_st["enabled"]).pack(side=tk.LEFT)
            tk.Label(row, text="预约时间").pack(side=tk.LEFT, padx=(4, 2))
            tk.Entry(row, width=7, textvariable=row_st["time"]).pack(side=tk.LEFT, padx=3)
            for p in spec.get("params", []):
                tk.Label(row, text=p["label"]).pack(side=tk.LEFT)
                var = tk.StringVar(value=str(p.get("default", "")))
                row_st["vars"][p["name"]] = var
                tk.Entry(row, width=4, textvariable=var).pack(side=tk.LEFT, padx=3)
            tk.Label(row, textvariable=row_st["status"], fg="#555555").pack(side=tk.LEFT, padx=4)

    @staticmethod
    def _parse_task_params(spec: dict, vars_: dict) -> dict:
        """按 taskspec params 声明解析表单值；非法值抛 ValueError(中文提示)。"""
        params = {}
        for p in spec.get("params", []):
            raw = str(vars_[p["name"]].get()).strip()
            if p["type"] == "int":
                v = parse_limit(raw)
                if v is None:
                    raise ValueError(f"{p['label']}需为 0 或正整数")
                params[p["name"]] = v
            else:
                if p.get("required") and not raw:
                    raise ValueError(f"{p['label']}不能为空")
                params[p["name"]] = raw
        return params

    def _append_task_text(self, key: str, text: str) -> None:
        st = self._task_state[key]
        self._box_append(st["box"], text, st["log_path"])

    def _manual_run_task(self, spec: dict) -> None:
        st = self._task_state[spec["key"]]
        try:
            params = self._parse_task_params(spec, st["vars"])
        except ValueError as exc:
            st["status"].set(str(exc))
            return
        self._run_task(spec, params, "手动执行")

    def _run_task(self, spec: dict, params: dict, label: str) -> bool:
        """启动一次 bg 任务；返回是否真正启动(False=已有执行在跑)。"""
        key = spec["key"]
        st = self._task_state[key]
        if st["running"]:
            self._append_task_text(key, f"[{label}] 已有执行在跑，本次触发跳过")
            return False
        st["running"] = True
        st["cancel"] = False
        st["btns"]["run"].configure(state=tk.DISABLED)
        if "stop" in st["btns"]:
            st["btns"]["stop"].configure(state=tk.NORMAL)
        st["status"].set("运行中...")
        self._box_set(st["box"], f"[{label}] 开始，参数: {params}")
        mod_name, func_name = spec["callable"].split(":", 1)

        def fn():
            fn_impl = getattr(importlib.import_module(mod_name), func_name)
            return fn_impl(
                progress_cb=lambda text: self.root.after(0, self._append_task_text, key, text),
                should_cancel=lambda: st["cancel"],
                **params)

        self._run_bg(fn, lambda summary, err: self._on_task_done(spec, summary, err, label), key)
        return True

    def _stop_task(self, spec: dict) -> None:
        st = self._task_state[spec["key"]]
        st["cancel"] = True
        st["status"].set("停止中...")
        self._append_task_text(spec["key"], ">> 已请求停止")
        st["btns"]["stop"].configure(state=tk.DISABLED)

    def _on_task_done(self, spec: dict, summary, err, label: str) -> None:
        key = spec["key"]
        st = self._task_state[key]
        st["running"] = False
        st["btns"]["run"].configure(state=tk.NORMAL)
        if "stop" in st["btns"]:
            st["btns"]["stop"].configure(state=tk.DISABLED)
        if err is not None:
            st["status"].set("失败")
            self._append_task_text(key, f"\n[{label}] 执行失败: {err}")
            for row in st["sched"]:
                if row["status"].get() == "执行中":
                    row["status"].set("已失败")
            return
        st["status"].set("完成")
        if summary:
            self._append_task_text(key, str(summary))
        for row in st["sched"]:
            if row["status"].get() == "执行中":
                row["status"].set("已执行")

    def _check_task_schedules(self, now: dt.datetime) -> None:
        """每次 _refresh 调用：对所有 schedulable 任务按状态机处理预约行。"""
        now_mins = now.hour * 60 + now.minute
        for spec in self._bg_specs:
            if not spec.get("schedulable"):
                continue
            st = self._task_state[spec["key"]]
            for row in st["sched"]:
                if row["enabled"].get() and row["fired"]:
                    # 触发过又重新勾选启用 = 再次预约一次
                    row["fired"] = False
                    row["status"].set("等待预约")
                sched_mins = parse_schedule(row["time"].get().strip())
                action = schedule_action(row["enabled"].get(), now_mins, sched_mins, row["fired"])
                if action in ("idle", "wait"):
                    continue
                row["enabled"].set(False)
                if action == "expired":
                    row["status"].set("已过期")
                    self._append_task_text(spec["key"], f"[预约 {row['time'].get()}] 时间已过，跳过本次")
                elif action == "run":
                    row["fired"] = True
                    row["status"].set("执行中")
                    try:
                        params = self._parse_task_params(spec, row["vars"])
                    except ValueError as exc:
                        row["status"].set("已取消(参数无效)")
                        self._append_task_text(spec["key"],
                                               f"[预约 {row['time'].get()}] {exc}，已取消本次")
                        continue
                    self._append_task_text(spec["key"],
                                           f"[预约 {row['time'].get()}] 到点自动执行，参数: {params}")
                    started = self._run_task(spec, params, f"预约 {row['time'].get()}")
                    if not started:
                        row["status"].set("已跳过")
```

⑧ `_refresh` 中调用点改为 `self._check_task_schedules(now)`（见 Step 4-7）。

- [ ] **Step 6: 跑测试**

Run: `.\.venv\Scripts\python.exe tests\test_manager.py`
Expected: `ALL manager tests OK`

Run: `.\.venv\Scripts\python.exe tests\test_taskspec.py`
Expected: `ALL taskspec tests OK`

- [ ] **Step 7: 手工冒烟（GUI）**

Run: `.\.venv\Scripts\python.exe manager.py`（本地人工或后台）
Expected:
- 顶部进程条：采集器/API/看板/排班四行；排班行有「打开页面」按钮
- 左导航 9 项：4 日志 + 数据补全 + 导出 CSV + 进线量预测/接待上限/写入预估
- 「进线量预测」页可点「开始执行」（无 AutoTableau 数据时显示失败原因即正常）
- 「接待上限」页有上限值输入 + 停止按钮 + 两条预约行，行为与改前一致
- 排班「启动」后 http://127.0.0.1:5000 可访问（`python -m shift` 路径验证）

- [ ] **Step 8: Commit**

```powershell
git add manager.py tests/test_manager.py tests/test_taskspec.py
git commit -m "refactor(manager): 注册表驱动通用任务页，删除预测/接待上限 bespoke 页"
```

---

### Task 7: 文档同步 + 全量回归

**Files:**
- Modify: `AGENTS.md`
- Test: 全量

**Interfaces:**
- Consumes: Task 1-6 全部完成

- [ ] **Step 1: 更新 AGENTS.md**

对 `AGENTS.md` 做三处修订：

1. `member_limit/` 条目：「manager.py「接待上限」页调用」改为「manager.py 通用任务页（taskspec 注册）调用；CLI: `python -m member_limit`」
2. `writeforecast/` 条目整体改为：「包化脚本集（`python -m writeforecast forecast|shifts [xlsx]`）：周度预估 Excel → `data/预估流入量.csv`；班表 Excel → 时段人力架构表。manager.py「写入预估」页（taskspec 注册）调用」
3. 「shift/ 和 writeforecast/ 用 flat imports，必须直跑不能用 -m」的说明改为：「shift/ 内部保持 flat imports；`shift/main.py` 与 `shift/__main__.py` 已做 sys.path 自保，可用 `python -m shift`（Web 服务）与 `python -m shift.main`（CLI）。manager.py 经 taskspec 注册表管理排班进程，不再有 runpy 启动 hack」

- [ ] **Step 2: 全量回归**

Run: `Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }`
Expected: 每个文件均 OK 结尾，无失败（smoke.py 不跑）

- [ ] **Step 3: Commit**

```powershell
git add AGENTS.md
git commit -m "docs: AGENTS.md 同步插件化后的子系统入口说明"
```

---

## Self-Review 记录

- Spec 覆盖：包结构(T2-T5) / taskspec 契约(T1-T5) / manager 通用页(T6) / 错误处理与测试(T1-T7 各步) / 验收 1-3(T2-T6 冒烟)、验收 4(T1 test_discover_skips_invalid_spec + T6 通用页机制)、验收 5(T7 全量回归)
- 两处设计细化已回写 spec：validate 只做结构校验（避免 manager 启动拉重依赖）；service 型复用进程条+日志页而非新建页面
- 已知取舍：member_limit 页不再显示「成员: N 人」（intro 文案覆盖使用说明）；排班外部进程 match_key 回退为 module 名 "shift"（覆盖 `python -m shift`，旧 runpy 方式的外部实例不再被识别——重构后该启动方式已删除，可接受）
