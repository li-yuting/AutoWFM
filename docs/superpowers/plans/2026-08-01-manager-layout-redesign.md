# manager.py 排版重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `ManagerUI._build_ui` 从「顶部 Notebook 选项卡」重构为「标题栏 / 状态条 / 主区(左导航+右内容) / 底栏」四区结构，区间用分隔线分隔，保持原生 ttk，业务逻辑/回调/纯函数/测试全部不动。

**Architecture:** 仅改 `manager.py` 的 UI 外壳（`_build_ui`、`_update_status`、`__init__` 的属性初始化，新增 `_show_page`）和 `tests/test_manager.py`（加构造冒烟测试）。左侧导航用 `tk.Button` + relief 切换 + 内容帧 `raise_()` 替代 `ttk.Notebook`；状态条改 `grid` 对齐 + 状态点；底栏按语义分组 + 竖向分隔线。`ManagedTask`、`tick()`、`_refresh()`、预测/补全/托盘/告警等业务逻辑一律不触碰。

**Tech Stack:** Python 3.14（`.venv`），Tkinter（`tk` + `ttk`），纯 assert 测试（**无 pytest**，`python tests/test_manager.py` 直接运行）。

## Global Constraints

（每个任务的需求都隐含包含本节。）

- 平台 Windows + PowerShell；Python 3.14 在 `.venv`，命令一律用 `.\.venv\Scripts\python.exe`，不用系统 Python。
- 任何中文输出前先 `$env:PYTHONIOENCODING="utf-8"`。
- 测试用 **plain assert，无 pytest**：`python tests/test_manager.py` 直接跑，看到 `ALL manager tests OK` 即通过。
- **无 git（未安装）**：不执行任何 `git` 命令；每个任务的检查点 = 测试通过。任务 1 先备份 `manager.py` 作为回退点（对应 spec §8）。
- **按钮保持 `tk.Button`，不换 `ttk.Button`**（偏离 spec §5「统一 ttk.Button」）：开机自启按钮用了 `textvariable=self.autostart_var`，而 `ttk.Button` 不支持 `textvariable`（只接受 `text`），换会破坏动态标签。导航按钮也用 `tk.Button`（需 relief 切换选中态），全文件按钮类一致。
- 视觉风格：原生 ttk，**不引入深色主题/重度自定义样式**。
- 窗口尺寸保持 `980x680`，`minsize 820x560`。
- 业务逻辑/回调/纯函数（`compute_auto_start`/`auto_stop_minutes`/`in_run_window`/`schedule_text`/`ManagedTask.tick`/`_forecast_summary`）**全部不动**；`tests/test_manager.py` 现有用例必须保持全绿。
- **测试偏差说明**：spec §7 说「Tk UI 不写自动化测试」。本计划新增 2 个**构造冒烟测试**（mock 掉 `_build_tray`/`_refresh`，不起托盘线程、不拉起采集器/看板进程，无副作用），只验证 `_build_ui` 不崩溃 + 结构正确 + `_update_status` 状态点接线，不验证视觉外观。这是为 UI 重构提供自动化安全网，与「易碎的视觉测试」不同。若用户反对，可删去这两个测试、改纯人工验证。

## File Structure

- **Modify:** `manager.py` — `ManagerUI.__init__`（加 `_nav_buttons`/`_nav_pages` 属性）、`ManagerUI._build_ui`（四区重构）、`ManagerUI._update_status`（加状态点 `fg`）、新增 `ManagerUI._show_page`。
- **Modify:** `tests/test_manager.py` — 加 `import tkinter as tk`、`test_ui_constructs`、`test_update_status_sets_dot`，并注册到 `main()`。
- 不新建文件。`ManagedTask` 及其它类/函数不动。

---

### Task 1: 左侧导航替代 Notebook（主区重构）+ 构造冒烟测试

**Files:**
- Modify: `manager.py`（`ManagerUI.__init__`、`_build_ui` 的 Notebook 块、新增 `_show_page`）
- Modify: `tests/test_manager.py`（加 import + `test_ui_constructs` + 注册 `main()`）

**Interfaces:**
- Consumes: 现有 `self.tasks`、`self._log_boxes`、`_build_forecast_page(page)`、`_build_backfill_page(page)`。
- Produces: `ManagerUI._nav_buttons: list[tk.Button]`、`ManagerUI._nav_pages: list[tk.Frame]`、`ManagerUI._show_page(idx: int) -> None`。Task 2/3 依赖构造不崩溃。

- [ ] **Step 1: 备份 manager.py（无 git，作为回退点）**

Run:
```powershell
Copy-Item D:\PythonProject\AutoWFM\manager.py D:\PythonProject\AutoWFM\manager.py.bak
```
Expected: 生成 `manager.py.bak`。

- [ ] **Step 2: 在 test_manager.py 加 `import tkinter as tk`**

`tests/test_manager.py` 顶部 import 区，把：

```python
import datetime as dt
import os, sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
```

改为：

```python
import datetime as dt
import os, sys
from pathlib import Path
import tkinter as tk
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
```

- [ ] **Step 3: 写失败测试 `test_ui_constructs`**

在 `tests/test_manager.py` 的 `def main():` 之前插入：

```python
def test_ui_constructs():
    """构造 ManagerUI 不崩溃 + 左侧导航结构正确。
    _build_tray/_refresh mock 成空操作,避免起托盘线程/拉起采集器看板进程。"""
    with patch.object(ManagerUI, "_build_tray", lambda self: None), \
         patch.object(ManagerUI, "_refresh", lambda self: None):
        root = tk.Tk()
        root.withdraw()
        ui = ManagerUI(root, _cfg())
        try:
            assert len(ui._nav_buttons) == 4, f"4 个导航按钮, 实际 {len(ui._nav_buttons)}"
            assert len(ui._nav_pages) == 4, f"4 个内容页, 实际 {len(ui._nav_pages)}"
            assert len(ui._log_boxes) == 2, f"2 个日志框, 实际 {len(ui._log_boxes)}"
        finally:
            root.destroy()
    print("ui_constructs OK")
```

并在 `main()` 里注册——把：

```python
    test_stop_automatic_parameter()
    print("ALL manager tests OK")
```

改为：

```python
    test_stop_automatic_parameter()
    test_ui_constructs()
    print("ALL manager tests OK")
```

- [ ] **Step 4: 跑测试确认失败**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_manager.py
```
Expected: 在 `test_ui_constructs` 处失败（`AttributeError: 'ManagerUI' object has no attribute '_nav_buttons'`，因为当前还是 Notebook）。

- [ ] **Step 5: `__init__` 加 `_nav_buttons`/`_nav_pages` 属性**

`manager.py` 的 `ManagerUI.__init__`，把：

```python
        self._vars: list[dict[str, tk.StringVar]] = []
        self._log_boxes: list[scrolledtext.ScrolledText] = []
        self.last_popup_at: dt.datetime | None = None
```

改为：

```python
        self._vars: list[dict[str, tk.StringVar]] = []
        self._log_boxes: list[scrolledtext.ScrolledText] = []
        self._nav_buttons: list[tk.Button] = []
        self._nav_pages: list[tk.Frame] = []
        self.last_popup_at: dt.datetime | None = None
```

- [ ] **Step 6: 用左导航+右内容替代 Notebook 块**

`manager.py` 的 `_build_ui`，把整个 Notebook 块：

```python
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 4))
        for task in self.tasks:
            page = tk.Frame(nb)
            nb.add(page, text=f"{task.name} 日志")
            box = scrolledtext.ScrolledText(page, wrap=tk.WORD)
            box.pack(fill=tk.BOTH, expand=True)
            box.configure(state=tk.DISABLED)
            self._log_boxes.append(box)
        fc_page = tk.Frame(nb)
        nb.add(fc_page, text="进线量预测")
        self._build_forecast_page(fc_page)
        bf_page = tk.Frame(nb)
        nb.add(bf_page, text="数据补全")
        self._build_backfill_page(bf_page)
```

改为：

```python
        # 主区: 左导航 + 右内容
        main = tk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 4))
        nav = tk.Frame(main, width=120)
        nav.pack(side=tk.LEFT, fill=tk.Y)
        nav.pack_propagate(False)
        content = tk.Frame(main)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        nav_items: list[tuple[str, tk.Frame]] = []
        for task in self.tasks:
            page = tk.Frame(content)
            page.grid(row=0, column=0, sticky="nsew")
            box = scrolledtext.ScrolledText(page, wrap=tk.WORD)
            box.pack(fill=tk.BOTH, expand=True)
            box.configure(state=tk.DISABLED)
            self._log_boxes.append(box)
            nav_items.append((f"{task.name} 日志", page))
        fc_page = tk.Frame(content)
        fc_page.grid(row=0, column=0, sticky="nsew")
        self._build_forecast_page(fc_page)
        nav_items.append(("进线量预测", fc_page))
        bf_page = tk.Frame(content)
        bf_page.grid(row=0, column=0, sticky="nsew")
        self._build_backfill_page(bf_page)
        nav_items.append(("数据补全", bf_page))

        self._nav_pages = [page for _, page in nav_items]
        self._nav_buttons = []
        for idx, (label, _page) in enumerate(nav_items):
            btn = tk.Button(nav, text=label, relief=tk.RAISED,
                            command=lambda i=idx: self._show_page(i))
            btn.pack(fill=tk.X, pady=(0, 4))
            self._nav_buttons.append(btn)
        self._show_page(0)
```

- [ ] **Step 7: 新增 `_show_page` 方法**

`manager.py` 的 `_build_ui` 末尾（底栏最后一行之后、`# ---- 手动操作 ----` 之前），把：

```python
        tk.Label(bar, text=f"管理器日志: {MANAGER_LOG}", fg="#777777").pack(side=tk.LEFT, padx=16)

    # ---- 手动操作 ----
```

改为：

```python
        tk.Label(bar, text=f"管理器日志: {MANAGER_LOG}", fg="#777777").pack(side=tk.LEFT, padx=16)

    def _show_page(self, idx: int) -> None:
        """切换左侧导航到第 idx 个内容页(raise_ 叠放帧 + 高亮选中按钮)。"""
        self._nav_pages[idx].tkraise()
        for i, btn in enumerate(self._nav_buttons):
            btn.configure(relief=tk.SUNKEN if i == idx else tk.RAISED)

    # ---- 手动操作 ----
```

- [ ] **Step 8: 跑测试确认通过**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_manager.py
```
Expected: 末尾输出 `ALL manager tests OK`（含 `ui_constructs OK`）。

- [ ] **Step 9: 人工核对（用户执行）**

Run: `.\.venv\Scripts\python.exe manager.py`

核对：
- 左侧出现 4 个导航按钮（采集器日志 / 看板日志 / 进线量预测 / 数据补全），默认「采集器日志」凹陷高亮。
- 点各按钮右侧内容切换；采集器日志/看板日志页是日志框，进线量预测/数据补全页是原工具表单。
- 顶部状态卡片、底栏暂仍是旧样式（Task 2/3 再改），属正常中间态。

确认后关闭窗口。检查点：测试通过 + 人工确认导航可用（无 git，不提交）。

---

### Task 2: 状态条网格化 + 状态点 + `_update_status` 接线

**Files:**
- Modify: `manager.py`（`_build_ui` 的状态卡片块、`_update_status`）
- Modify: `tests/test_manager.py`（加 `test_update_status_sets_dot` + 注册 `main()`）

**Interfaces:**
- Consumes: Task 1 的构造可用性（`ManagerUI` 可构造）。
- Produces: 每个 `self._vars[i]` 多一个 `"status_dot"` 键（`tk.Label`，`fg` 由 `_update_status` 按状态设置）。

- [ ] **Step 1: 写失败测试 `test_update_status_sets_dot`**

在 `tests/test_manager.py` 的 `test_ui_constructs` 之后、`def main():` 之前插入：

```python
def test_update_status_sets_dot():
    """_update_status 在运行中状态会把状态点染绿(验证状态点接线)。"""
    with patch.object(ManagerUI, "_build_tray", lambda self: None), \
         patch.object(ManagerUI, "_refresh", lambda self: None):
        root = tk.Tk()
        root.withdraw()
        ui = ManagerUI(root, _cfg())
        try:
            task = ui.tasks[0]
            proc = MagicMock(); proc.poll.return_value = None; proc.pid = 12345
            task.process = proc
            ui._update_status()
            assert ui._vars[0]["status_dot"].cget("fg") == "#16803c", "运行中状态点应为绿 #16803c"
        finally:
            root.destroy()
    print("update_status_sets_dot OK")
```

并在 `main()` 注册——把：

```python
    test_stop_automatic_parameter()
    test_ui_constructs()
    print("ALL manager tests OK")
```

改为：

```python
    test_stop_automatic_parameter()
    test_ui_constructs()
    test_update_status_sets_dot()
    print("ALL manager tests OK")
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_manager.py
```
Expected: `test_update_status_sets_dot` 处失败（`KeyError: 'status_dot'`，状态点尚未创建）。

- [ ] **Step 3: 状态卡片块改为 grid 状态条 + 状态点**

`manager.py` 的 `_build_ui`，把两个 `LabelFrame` 的 for 块：

```python
        for task in self.tasks:
            vars_ = {
                "status": tk.StringVar(value="未运行"),
                "pid": tk.StringVar(value="PID: -"),
                "fail": tk.StringVar(value="失败: 0/3"),
                "src": tk.StringVar(value=""),
            }
            self._vars.append(vars_)
            row = tk.LabelFrame(self.root, text=task.name, padx=12, pady=8)
            row.pack(fill=tk.X, padx=12, pady=6)
            status_lbl = tk.Label(row, textvariable=vars_["status"], font=("Microsoft YaHei UI", 13, "bold"))
            status_lbl.pack(side=tk.LEFT)
            vars_["status_label"] = status_lbl
            tk.Label(row, textvariable=vars_["pid"], padx=12).pack(side=tk.LEFT)
            tk.Label(row, textvariable=vars_["fail"], padx=12).pack(side=tk.LEFT)
            tk.Label(row, textvariable=vars_["src"], padx=12, fg="#777777").pack(side=tk.LEFT)
            btns = tk.Frame(row)
            btns.pack(side=tk.RIGHT)
            tk.Button(btns, text="启动", width=8, command=lambda t=task: self._manual_start(t)).pack(side=tk.LEFT, padx=3)
            tk.Button(btns, text="停止", width=8, command=lambda t=task: self._manual_stop(t)).pack(side=tk.LEFT, padx=3)
            tk.Button(btns, text="重启", width=8, command=lambda t=task: self._manual_restart(t)).pack(side=tk.LEFT, padx=3)
```

改为（单 Frame 两行 grid，列6 为弹性间距把按钮推到右侧）：

```python
        strip = tk.Frame(self.root, padx=12, pady=6)
        strip.pack(fill=tk.X)
        strip.grid_columnconfigure(6, weight=1)  # 列6 弹性间距,把按钮推到右侧
        for r, task in enumerate(self.tasks):
            vars_ = {
                "status": tk.StringVar(value="未运行"),
                "pid": tk.StringVar(value="PID: -"),
                "fail": tk.StringVar(value="失败: 0/3"),
                "src": tk.StringVar(value=""),
            }
            self._vars.append(vars_)
            dot = tk.Label(strip, text="●", font=("Microsoft YaHei UI", 12), fg="#666666")
            dot.grid(row=r, column=0, padx=(0, 6), sticky="w")
            vars_["status_dot"] = dot
            tk.Label(strip, text=task.name, font=("Microsoft YaHei UI", 11, "bold")).grid(row=r, column=1, padx=(0, 10), sticky="w")
            status_lbl = tk.Label(strip, textvariable=vars_["status"], font=("Microsoft YaHei UI", 13, "bold"))
            status_lbl.grid(row=r, column=2, padx=(0, 12), sticky="w")
            vars_["status_label"] = status_lbl
            tk.Label(strip, textvariable=vars_["pid"]).grid(row=r, column=3, padx=(0, 12), sticky="w")
            tk.Label(strip, textvariable=vars_["fail"]).grid(row=r, column=4, padx=(0, 12), sticky="w")
            tk.Label(strip, textvariable=vars_["src"], fg="#777777").grid(row=r, column=5, sticky="w")
            btns = tk.Frame(strip)
            btns.grid(row=r, column=7, sticky="e")
            tk.Button(btns, text="启动", width=8, command=lambda t=task: self._manual_start(t)).pack(side=tk.LEFT, padx=3)
            tk.Button(btns, text="停止", width=8, command=lambda t=task: self._manual_stop(t)).pack(side=tk.LEFT, padx=3)
            tk.Button(btns, text="重启", width=8, command=lambda t=task: self._manual_restart(t)).pack(side=tk.LEFT, padx=3)
```

- [ ] **Step 4: `_update_status` 加状态点 `fg` 接线**

`manager.py` 的 `_update_status`，把整个方法：

```python
    def _update_status(self) -> None:
        for task, vars_ in zip(self.tasks, self._vars):
            if task.is_running():
                vars_["status"].set("运行中")
                vars_["status_label"].configure(fg="#16803c")
                vars_["pid"].set(f"PID: {task.process.pid}")
                vars_["src"].set("UI 管理")
            elif task.external_pid:
                vars_["status"].set("运行中(外部)")
                vars_["status_label"].configure(fg="#16803c")
                vars_["pid"].set(f"PID: {task.external_pid}")
                vars_["src"].set("外部启动")
            else:
                if task.restart_failures >= MAX_FAILURES:
                    vars_["status"].set("已暂停重启")
                    vars_["status_label"].configure(fg="#aa2222")
                elif task.user_stopped:
                    vars_["status"].set("已停止")
                    vars_["status_label"].configure(fg="#666666")
                else:
                    vars_["status"].set("未运行")
                    vars_["status_label"].configure(fg="#aa2222")
                vars_["pid"].set("PID: -")
                vars_["src"].set("")
            vars_["fail"].set(f"失败: {task.restart_failures}/{MAX_FAILURES}")
```

改为（每个状态分支多一行 `status_dot` 同色设置，颜色与 `status_label` 一致）：

```python
    def _update_status(self) -> None:
        for task, vars_ in zip(self.tasks, self._vars):
            if task.is_running():
                vars_["status"].set("运行中")
                vars_["status_label"].configure(fg="#16803c")
                vars_["status_dot"].configure(fg="#16803c")
                vars_["pid"].set(f"PID: {task.process.pid}")
                vars_["src"].set("UI 管理")
            elif task.external_pid:
                vars_["status"].set("运行中(外部)")
                vars_["status_label"].configure(fg="#16803c")
                vars_["status_dot"].configure(fg="#16803c")
                vars_["pid"].set(f"PID: {task.external_pid}")
                vars_["src"].set("外部启动")
            else:
                if task.restart_failures >= MAX_FAILURES:
                    vars_["status"].set("已暂停重启")
                    vars_["status_label"].configure(fg="#aa2222")
                    vars_["status_dot"].configure(fg="#aa2222")
                elif task.user_stopped:
                    vars_["status"].set("已停止")
                    vars_["status_label"].configure(fg="#666666")
                    vars_["status_dot"].configure(fg="#666666")
                else:
                    vars_["status"].set("未运行")
                    vars_["status_label"].configure(fg="#aa2222")
                    vars_["status_dot"].configure(fg="#aa2222")
                vars_["pid"].set("PID: -")
                vars_["src"].set("")
            vars_["fail"].set(f"失败: {task.restart_failures}/{MAX_FAILURES}")
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_manager.py
```
Expected: 末尾 `ALL manager tests OK`（含 `update_status_sets_dot OK`）。

- [ ] **Step 6: 人工核对（用户执行）**

Run: `.\.venv\Scripts\python.exe manager.py`

核对：
- 顶部状态条两行（采集器/看板），`●状态点 任务名 状态 PID 失败 来源` 与右侧 `启动/停止/重启` 按钮两行严格列对齐。
- 未运行时状态点为红/灰；启动采集器后状态点变绿、PID 出现；停止后变灰。（若不便起进程，至少确认未运行态红点显示。）

确认后关闭窗口。检查点：测试通过 + 人工确认对齐与状态点变色（无 git，不提交）。

---

### Task 3: 底栏分组 + 区间分隔线

**Files:**
- Modify: `manager.py`（`_build_ui`：3 处插入水平分隔线 + 底栏重排）
- Test: 无新测试；`test_ui_constructs` 仍须通过（验证重构未破坏构造）。

**Interfaces:**
- Consumes: Task 1/2 已建好的四区（标题栏 / 状态条 / 主区 / 底栏）。
- Produces: 四区之间 3 条 `ttk.Separator`（水平）；底栏 3 组之间 2 条 `ttk.Separator`（竖向），日志路径右对齐。

- [ ] **Step 1: 标题栏与状态条之间加分隔线**

`manager.py` 的 `_build_ui`，把：

```python
        tk.Label(top, textvariable=self.schedule_var, padx=16, fg="#555555").pack(side=tk.LEFT)

        strip = tk.Frame(self.root, padx=12, pady=6)
```

改为：

```python
        tk.Label(top, textvariable=self.schedule_var, padx=16, fg="#555555").pack(side=tk.LEFT)
        ttk.Separator(self.root).pack(fill=tk.X, padx=12)

        strip = tk.Frame(self.root, padx=12, pady=6)
```

- [ ] **Step 2: 状态条与主区之间加分隔线**

`manager.py` 的 `_build_ui`，把（状态条 for 块最后一行 + 主区注释）：

```python
            tk.Button(btns, text="重启", width=8, command=lambda t=task: self._manual_restart(t)).pack(side=tk.LEFT, padx=3)

        # 主区: 左导航 + 右内容
```

改为：

```python
            tk.Button(btns, text="重启", width=8, command=lambda t=task: self._manual_restart(t)).pack(side=tk.LEFT, padx=3)
        ttk.Separator(self.root).pack(fill=tk.X, padx=12)

        # 主区: 左导航 + 右内容
```

- [ ] **Step 3: 主区与底栏之间加分隔线 + 底栏分组重排**

`manager.py` 的 `_build_ui`，把（`self._show_page(0)` + 整个底栏块）：

```python
        self._show_page(0)

        bar = tk.Frame(self.root, padx=12, pady=6)
        bar.pack(fill=tk.X)
        tk.Button(bar, text="刷新日志", command=self._load_logs).pack(side=tk.LEFT)
        tk.Button(bar, textvariable=self.autostart_var, command=self._toggle_autostart).pack(side=tk.LEFT, padx=8)
        tk.Button(bar, text="重启控制台", command=self._restart_manager).pack(side=tk.LEFT, padx=8)
        tk.Label(bar, text=f"管理器日志: {MANAGER_LOG}", fg="#777777").pack(side=tk.LEFT, padx=16)
```

改为：

```python
        self._show_page(0)
        ttk.Separator(self.root).pack(fill=tk.X, padx=12)

        bar = tk.Frame(self.root, padx=12, pady=6)
        bar.pack(fill=tk.X)
        tk.Button(bar, text="刷新日志", command=self._load_logs).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        tk.Button(bar, textvariable=self.autostart_var, command=self._toggle_autostart).pack(side=tk.LEFT)
        tk.Button(bar, text="重启控制台", command=self._restart_manager).pack(side=tk.LEFT, padx=8)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        tk.Label(bar, text=f"管理器日志: {MANAGER_LOG}", fg="#777777").pack(side=tk.RIGHT)
```

- [ ] **Step 4: 跑测试确认仍通过**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_manager.py
```
Expected: 末尾 `ALL manager tests OK`。

- [ ] **Step 5: 人工核对（用户执行）**

Run: `.\.venv\Scripts\python.exe manager.py`

核对：
- 标题栏 / 状态条 / 主区 / 底栏 之间各有 1 条水平分隔线，四区层次清晰。
- 底栏三组：`[刷新日志]` ｜ `[开机自启] [重启控制台]` ｜ `管理器日志: …`（右侧），组间竖向分隔线。
- 「开机自启」按钮文字随状态切换（已开启/已关闭）正常；点「刷新日志」日志框刷新；「重启控制台」弹确认框（可取消，不实际重启）。

确认后关闭窗口。检查点：测试通过 + 人工确认分区与底栏分组（无 git，不提交）。

- [ ] **Step 6: 收尾——删除备份（可选）**

全部确认无误后，删除 Task 1 的备份：

Run:
```powershell
Remove-Item D:\PythonProject\AutoWFM\manager.py.bak
```
（若想保留回退点，跳过此步。）

---

## Self-Review

**1. Spec 覆盖：**
- §2 四区 + 分隔线 → Task 3（3 条水平分隔线）+ 各任务建区。✓
- §3 状态条 grid + 状态点 + 颜色 → Task 2。✓
- §4 左导航 4 按钮 / `raise_` / 默认采集器日志 / 内容复用 → Task 1。✓
- §5 底栏 3 组 + 竖向分隔线 + 日志路径右对齐 → Task 3。✓（按钮未换 ttk.Button，已在 Global Constraints 说明偏离原因。）
- §6 不变部分 → 仅改 `_build_ui`/`_update_status`/`__init__`/新增 `_show_page`，业务逻辑未触碰。✓
- §7 测试 → 现有用例全绿 + 新增 2 个构造冒烟测试 + 每任务人工核对。✓（构造测试偏离 spec §7，已说明。）
- §8 备份回退 → Task 1 Step 1 备份。✓

**2. 占位符扫描：** 无 TBD/TODO/"类似 Task N"；每个改代码步骤均给出完整 old/new 代码块。✓

**3. 类型/命名一致：** `_nav_buttons`/`_nav_pages`（Task 1 定义，`__init__` 与 `_build_ui` 与 `_show_page` 一致）；`vars_["status_dot"]`（Task 2 `_build_ui` 写入、`_update_status` 读取、测试断言，三处一致）；`_show_page(idx)` 签名一致。✓
