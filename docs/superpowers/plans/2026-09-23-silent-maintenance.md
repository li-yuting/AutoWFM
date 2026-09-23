# 管理器静默磁盘维护实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` for inline execution.

**Goal:** 管理器每天 05:00 静默执行一次磁盘维护，当天错过则补跑；只清理过期日志并压缩有空闲页的 SQLite 数据库。

**Architecture:** 复用 `ManagerUI._refresh()` 的 5 秒循环和 `_run_bg()` 后台线程；用独立 `maintenance_state.json` 记录最近尝试日期。`collector/maintenance.py` 删除 `output/` 操作，并在空闲页为 0 时跳过 `VACUUM`。

**Tech Stack:** Python 标准库、Tkinter、SQLite、plain-assert 测试。

## Global Constraints

- 使用 `.\.venv\Scripts\python.exe`，运行前设置 `$env:PYTHONIOENCODING="utf-8"`。
- 不新增依赖，不新增 Windows 计划任务，不增加配置项。
- `maintenance.keep_days` 保持 30 天。
- `output/` 不参与盘点、dry-run 或删除。
- 活动 `.log`、非轮转日志和业务数据永不删除。
- 自动维护不弹窗、不修改维护页，只写 `logs/manager.log`。
- `manager.py` 与 `tests/test_manager.py` 已有用户未提交改动；不得回退或提交这些无关改动。
- 每个任务先运行失败测试，再实现，再运行通过测试。

---

### Task 1: 维护范围收窄与零空闲页跳过

**Files:**
- Modify: `tests/test_maintenance.py`
- Modify: `collector/maintenance.py`
- Modify: `manager.py`（仅维护报告、页面文案和调用参数）
- Modify: `AGENTS.md`、`README.md`、`config.example.yaml`、`config.yaml` 中维护范围说明
- Modify: `docs/superpowers/specs/2026-09-23-silent-maintenance-design.md` 不需要修改

**Interfaces:**
- Produces:
  - `scan(log_dir, data_dir, keep_days=30, now=None) -> dict`
  - `run(log_dir, data_dir, keep_days=30, apply=False, progress_cb=None, now=None) -> dict`
  - 返回值不再包含 `"输出目录"`；`"目录"` 只含 `logs/` 和 `data/`。
  - `compact_databases()` 在 `可回收 == 0` 且 `apply=True` 时不再连接数据库执行 `VACUUM`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_maintenance.py` 顶部增加：

```python
from unittest.mock import patch
```

删除 `test_prune_output()`，新增：

```python
def test_run_does_not_touch_output():
    """维护完全不操作 output/，即使按日目录已过期。"""
    d = _tmp(); logs, outs, data = _layout(d)
    old_file = outs / "2026-01-01" / "预测.xlsx"
    M.scan(logs, data, keep_days=30)
    M.run(logs, data, keep_days=30, apply=True)
    assert old_file.exists(), "output/ 必须完全退出维护范围"
    assert (outs / "2026-01-01").exists()
    print("run_does_not_touch_output OK")


def test_compact_skips_zero_free_pages():
    """空闲页为 0 时跳过 VACUUM，不做无收益重写。"""
    d = _tmp(); logs, outs, data = _layout(d)
    with patch.object(M, "_free_bytes", return_value=0), \\
         patch.object(M.sqlite3, "connect", side_effect=AssertionError("不应连接数据库")):
        result = M.compact_databases(data, apply=True)
    assert len(result) == 1
    assert result[0]["可回收"] == 0
    assert result[0]["回收"] == 0
    assert result[0]["错误"] == ""
    print("compact_skips_zero_free_pages OK")
```

把其余 `M.scan(...)` / `M.run(...)` 调用从 `(logs, outs, data)` 改为 `(logs, data)`，并删除所有 `"输出目录"` 断言。`test_run_apply_then_scan_clean` 的预期删除条目从 3 改为 2，并保留 `outs/2026-01-01` 断言。

- [ ] **Step 2: 运行新测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import tests.test_maintenance as t; t.test_run_does_not_touch_output()"
.\.venv\Scripts\python.exe -c "import tests.test_maintenance as t; t.test_compact_skips_zero_free_pages()"
```

预期：第一个因旧签名仍调用 `prune_output` 而失败；第二个因零空闲页仍尝试连接/压缩而失败。

- [ ] **Step 3: 最小实现**

`collector/maintenance.py`：

- 删除 `_DAY_DIR`、`_remove_tree()`、`prune_output()`。
- `run()` / `scan()` 删除 `out_dir` 参数和 `"输出目录"` 字段。
- `run()` 的 `freed` 只统计日志；`"目录"` 只盘点 `logs/`、`data/`。
- `compact_databases()` 在 `apply and item["可回收"] > 0` 时才创建连接并执行 `VACUUM`。
- 更新模块 docstring 和返回结构说明。

`manager.py`：

- `format_maint_report()` 删除产物目录统计与列表。
- 维护页说明、确认框只描述过期日志和数据库压缩。
- `_run_maint()` 调用 `run(ROOT / "logs", self._data_dir(), ...)`。

同步修改 `AGENTS.md`、`README.md`、`config.example.yaml`、`config.yaml` 中“清理 output”的描述。

- [ ] **Step 4: 运行维护测试**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_maintenance.py
```

预期：所有维护测试通过，包含 `run_does_not_touch_output OK` 和 `compact_skips_zero_free_pages OK`。

- [ ] **Step 5: 检查残留引用**

```powershell
$env:PYTHONIOENCODING="utf-8"
rg -n "prune_output|输出目录|output 按日目录|产物目录.*清理" collector manager.py tests AGENTS.md README.md config*.yaml
```

预期：维护范围相关残留为 0；预测和导出页面对 `output/` 的正常输出描述保留。

---

### Task 2: 05:00 定时、当天补跑与静默执行

**Files:**
- Modify: `tests/test_manager.py`
- Modify: `manager.py`

**Interfaces:**
- Consumes: Task 1 的 `run(log_dir, data_dir, keep_days=..., apply=True)`。
- Produces:
  - `MAINT_STATE: Path`
  - `maintenance_due(now: dt.datetime, last_attempt: dt.date | None) -> bool`
  - `load_maintenance_state() -> dt.date | None`
  - `save_maintenance_state(day: dt.date) -> None`
  - `ManagerUI._check_maintenance(now: dt.datetime) -> None`

- [ ] **Step 1: 写失败测试**

在 `tests/test_manager.py` 的 manager 导入中增加：

```python
MAINT_STATE, maintenance_due, load_maintenance_state, save_maintenance_state
```

新增：

```python
def test_maintenance_due_catches_up_once_per_day():
    before = dt.datetime(2026, 9, 23, 4, 59, tzinfo=SH)
    at = dt.datetime(2026, 9, 23, 5, 0, tzinfo=SH)
    later = dt.datetime(2026, 9, 23, 8, 0, tzinfo=SH)
    yesterday = dt.date(2026, 9, 22)
    today = dt.date(2026, 9, 23)

    assert maintenance_due(before, None) is False
    assert maintenance_due(at, None) is True
    assert maintenance_due(later, None) is True, "05:00 后启动应补跑"
    assert maintenance_due(later, yesterday) is True
    assert maintenance_due(later, today) is False, "同日不得重复执行"
    print("maintenance_due_catches_up_once_per_day OK")


def test_maintenance_state_roundtrip():
    state_path = _TMP / "maintenance_state.json"
    state_path.unlink(missing_ok=True)
    try:
        with patch("manager.MAINT_STATE", state_path):
            assert load_maintenance_state() is None
            save_maintenance_state(dt.date(2026, 9, 23))
            assert load_maintenance_state() == dt.date(2026, 9, 23)
    finally:
        state_path.unlink(missing_ok=True)
    print("maintenance_state_roundtrip OK")
```

在 `main()` 中调用这两个测试。

- [ ] **Step 2: 运行测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import tests.test_manager as t; t.test_maintenance_due_catches_up_once_per_day(); t.test_maintenance_state_roundtrip()"
```

预期：导入 `maintenance_due` 时失败，原因是功能尚不存在。

- [ ] **Step 3: 实现状态与到期判断**

在 `manager.py` 的状态文件区域增加：

```python
MAINT_STATE = ROOT / "maintenance_state.json"
MAINT_AT = dt.time(5, 0)


def maintenance_due(now: dt.datetime, last_attempt: dt.date | None) -> bool:
    due_at = now.replace(hour=MAINT_AT.hour, minute=MAINT_AT.minute,
                         second=0, microsecond=0)
    return now >= due_at and last_attempt != now.date()


def load_maintenance_state() -> dt.date | None:
    try:
        value = json.loads(MAINT_STATE.read_text(encoding="utf-8")).get("last_attempt")
        return dt.date.fromisoformat(value) if value else None
    except Exception:
        return None


def save_maintenance_state(day: dt.date) -> None:
    tmp = MAINT_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"last_attempt": day.isoformat()}), encoding="utf-8")
    os.replace(tmp, MAINT_STATE)
```

- [ ] **Step 4: 接入管理器后台执行**

`ManagerUI.__init__()` 增加：

```python
self._maint_last_attempt = load_maintenance_state()
```

`_refresh()` 在 `_check_member_limit_schedules(now)` 前调用：

```python
self._check_maintenance(now)
```

新增方法：

```python
def _check_maintenance(self, now: dt.datetime) -> None:
    if self._maint_running or not maintenance_due(now, self._maint_last_attempt):
        return
    self._maint_running = True

    def fn():
        from collector.maintenance import run
        return run(ROOT / "logs", self._data_dir(),
                   keep_days=_maint_cfg(self.cfg)["keep_days"], apply=True)

    def done(res, err):
        self._maint_running = False
        self._maint_last_attempt = now.date()
        try:
            save_maintenance_state(self._maint_last_attempt)
        except Exception:
            log.exception("保存磁盘维护状态失败")
        if err is not None:
            log.error("静默磁盘维护失败: %s", err, exc_info=err)
        else:
            log.info("静默磁盘维护完成: 删除 %s 个日志归档, 数据库回收 %s",
                     res["已删条目数"], res["可回收文本"])

    self._run_bg(fn, done, "maintenance_auto")
```

保持手动维护现有确认框、报告和 `_on_maint_done()`；两种模式共用 `_maint_running`。

- [ ] **Step 5: 运行目标测试**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import tests.test_manager as t; t.test_maintenance_due_catches_up_once_per_day(); t.test_maintenance_state_roundtrip()"
```

预期：两个测试通过。

---

### Task 3: 集成验证

**Files:**
- 无新增修改；只运行验证，失败时回到对应任务修复。

- [ ] **Step 1: 运行维护专项测试**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_maintenance.py
```

- [ ] **Step 2: 运行管理器测试**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_manager.py
```

若本机仍因 Tcl/Tk 初始化失败，必须明确记录该环境限制，并至少运行所有不构造 `tk.Tk()` 的目标测试。

- [ ] **Step 3: 运行全量测试**

```powershell
$env:PYTHONIOENCODING="utf-8"
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

- [ ] **Step 4: 真实数据只读盘点**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "from pathlib import Path; from collector.maintenance import scan; r=scan(Path('logs'), Path('data'), keep_days=30); print(r['可清理文本'], r['可回收文本'], len(r['日志']), len(r['数据库']))"
```

预期：不修改真实 `output/`，报告仅包含日志与数据库。

- [ ] **Step 5: 核对需求**

- 05:00 到点触发：纯函数测试覆盖。
- 当天补跑：纯函数测试覆盖。
- 同日一次：状态 roundtrip + due 测试覆盖。
- 静默：自动回调不触碰 UI，仅写日志。
- 30 天：调用 `_maint_cfg()["keep_days"]`，当前配置为 30。
- 日志清理：现有轮转日志测试覆盖。
- `output/` 不操作：专项测试覆盖。
- SQLite 只压缩不删数据：现有行数断言 + 零空闲页测试覆盖。
