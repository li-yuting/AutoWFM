# 修复采集缺口告警不复位 + 附带清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `collector/scheduler.py` 中缺口告警触发后永不复位、后续失败永不告警的 bug，并顺带清理 dashboard/queries.py 的重复死函数与 ws.py 的误导性注释。

**Architecture:** 主 bug 的根因是 `_track_gap(name, True)`（成功复位）只在「补采成功」路径被调用（scheduler.py:92），首轮直接成功的源从不走复位逻辑，导致 `_ws_gap_counters` 停在阈值、`_GAP_ALERTED` 永不清除。修复方式为在首轮成功分支（scheduler.py:76 之后）补一处 `_track_gap(s["name"], True, cfg)` 调用。附带两个纯删除/注释任务，无行为变化。

**Tech Stack:** Python 3.14（CI）、APScheduler、plain-assert 测试（无 pytest）、SQLite。

## Global Constraints

- 所有命令用 `.venv` 解释器运行，并设 `$env:PYTHONIOENCODING="utf-8"`。
- 测试用 plain `assert`，直接运行脚本，不用 pytest；文件名 `tests/test_*.py`。
- 测试临时目录用 `tests/.test_tmp/`（工作区内），避免沙箱对系统 temp 的写入限制（沿用 test_notify.py 的 `_WS_TMP` 约定）。
- 提交用 Conventional Commits（中文描述，如 `fix(scheduler): …`），一次一个逻辑改动。
- 本机 git 不在 PATH，用便携版：`$git = 'D:\ucredit\liyuting\PortableGit\cmd\git.exe'`。
- 提交 message 用 here-string：`& $git commit -m @'...'@`。

---

### Task 1: 修复 ws_job 缺口告警成功复位缺失

**Files:**
- Create: `tests/test_scheduler.py`
- Modify: `collector/scheduler.py:75-76`（在首轮成功分支加一行）

**Interfaces:**
- Consumes: 已有 `scheduler.ws_job(cfg, pool)`、`scheduler._track_gap(name, ok, cfg)`、`scheduler._ws_gap_counters`、`scheduler._GAP_ALERTED`、`scheduler.ws_mod.collect_one`、`scheduler.storage.insert`、`scheduler.notify.check_alerts`、`scheduler._send_gap_alert(cfg, msg)`。
- Produces: `ws_job` 在首轮成功时也会调用 `_track_gap(name, True, cfg)`，从而清零计数并 `_GAP_ALERTED.discard(name)`。

- [ ] **Step 1: 写失败测试（复现 bug）**

创建 `tests/test_scheduler.py`：

```python
# -*- coding: utf-8 -*-
import sys, os, shutil, time, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from collector import scheduler

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_notify.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")

def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d

def _cfg(data_dir):
    return {
        "schedule": {"timezone": "Asia/Shanghai", "window_start": "00:00", "window_end": "23:59"},
        "storage": {"dir": data_dir},
        "ws": {"gap_alert_threshold": 2, "backfill_retry_delay": 0},
        "subs": [{"name": "热线",
                  "schedule": {"weekday": {"start": "00:00", "end": "23:59"},
                               "weekend": {"start": "00:00", "end": "23:59"}}}],
        "notify": {"webhook": {"main_key": ""}},
    }

_OK_VAL = {"转人工量": 1, "接通量": 1, "排队量": 0, "累计呼入量": 1, "外呼量": 0, "外呼接通量": 0}

def test_ws_job_gap_alert_recovers():
    d = _tmp()
    cfg = _cfg(d)
    fixed = datetime.datetime(2026, 8, 25, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    alerts = []
    scheduler._ws_gap_counters.clear()
    scheduler._GAP_ALERTED.clear()
    scheduler._last_ws_cycle = None
    try:
        with patch.object(scheduler, "_now", return_value=fixed), \
             patch.object(scheduler, "time", MagicMock()), \
             patch.object(scheduler, "_send_gap_alert", side_effect=lambda c, m: alerts.append(m)), \
             patch.object(scheduler.notify, "check_alerts", return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                # 周期1: 首轮失败 + 补采失败 -> counter=1
                with patch.object(scheduler.ws_mod, "collect_one", return_value=None):
                    scheduler.ws_job(cfg, pool)
                assert scheduler._ws_gap_counters.get("热线", 0) == 1, "周期1 应计一次失败"
                assert len(alerts) == 0
                # 周期2: 再失败达阈值 -> 告警一次
                with patch.object(scheduler.ws_mod, "collect_one", return_value=None):
                    scheduler.ws_job(cfg, pool)
                assert len(alerts) == 1, "周期2 应触发一次告警"
                assert "热线" in scheduler._GAP_ALERTED
                # 周期3: 恢复正常, 首轮直接成功 -> 应复位
                with patch.object(scheduler.ws_mod, "collect_one", return_value=_OK_VAL):
                    scheduler.ws_job(cfg, pool)
                assert scheduler._ws_gap_counters.get("热线", 0) == 0, "恢复后计数应清零"
                assert "热线" not in scheduler._GAP_ALERTED, "恢复后应从 _GAP_ALERTED 移除"
                # 周期4-5: 再次连续失败达阈值 -> 应再次告警
                for _ in range(2):
                    with patch.object(scheduler.ws_mod, "collect_one", return_value=None):
                        scheduler.ws_job(cfg, pool)
                assert len(alerts) == 2, "恢复后再失败应再次告警"
    finally:
        scheduler._ws_gap_counters.clear()
        scheduler._GAP_ALERTED.clear()
        scheduler._last_ws_cycle = None
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    test_ws_job_gap_alert_recovers()
    print("test_scheduler OK")
```

- [ ] **Step 2: 运行测试，确认失败**

运行：
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_scheduler.py
```
Expected: `AssertionError: 恢复后计数应清零`（或后续任一 assert）——证明 bug 存在。注意：若测试文件因 `MagicMock` 未导入而报 `NameError`，把 Step 1 中 `from unittest.mock import patch` 改为 `from unittest.mock import patch, MagicMock`。

- [ ] **Step 3: 最小实现修复**

修改 `collector/scheduler.py:75-76`，在首轮成功写入后补复位调用：

```python
            storage.insert(s["name"], {"时间": now_str, **val}, cfg["storage"]["dir"])
            ok.append(s["name"])
            _track_gap(s["name"], True, cfg)
```

- [ ] **Step 4: 运行测试，确认通过**

运行：
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_scheduler.py
```
Expected: 输出 `test_scheduler OK`。

- [ ] **Step 5: 回归：运行相关测试**

运行：
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: 无断言失败（scheduler 改动不涉及 notify，验证未破坏依赖 collector 的既有测试）。

- [ ] **Step 6: 提交**

```powershell
$git = 'D:\ucredit\liyuting\PortableGit\cmd\git.exe'
& $git add tests/test_scheduler.py collector/scheduler.py
& $git commit -m @'
fix(scheduler): 首轮成功时复位缺口计数与告警标记

原实现只在补采成功路径调用 _track_gap(ok=True),首轮直接成功的源
不复位 _ws_gap_counters/_GAP_ALERTED,导致告警一次后永不再告警。
@'
```

---

### Task 2: 删除 dashboard/queries.py 重复死函数 `_rows_in_month`

**Files:**
- Modify: `dashboard/queries.py:35-36`（删除第一个定义）

**Interfaces:**
- Consumes: 无（纯删除）。
- Produces: 文件中仅保留第 72-74 行的 `_rows_in_month`（委托 `_repo_for(data_dir).rows_in(source, ym)`），与 35 行删除版功能完全等价（35 行经 `_rows_in` 委托同一实现）。删除后 `daily_latest`/`daily_avg`（76-96 行）调用不受影响。

- [ ] **Step 1: 删除死代码**

编辑 `dashboard/queries.py`，删除第 35-36 行：

```python
def _rows_in_month(data_dir, source, ym):
    return _rows_in(data_dir, source, ym)
```

保留第 72-74 行（带 docstring 的委托版本）。

- [ ] **Step 2: 验证**

运行：
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_queries.py
```
Expected: 无断言失败（`daily_latest`/`daily_avg` 等用 `_rows_in_month` 的用例仍通过）。

- [ ] **Step 3: 提交**

```powershell
$git = 'D:\ucredit\liyuting\PortableGit\cmd\git.exe'
& $git add dashboard/queries.py
& $git commit -m @'
refactor(dashboard): 删除重复的死函数 _rows_in_month

第 35 行定义与第 72 行委托同一 SQLiteReadOnlyRepository.rows_in,
保留带 docstring 的版本。
@'
```

---

### Task 3: 修正 collector/ws.py 误导性退避注释

**Files:**
- Modify: `collector/ws.py:117`

**Interfaces:**
- Consumes: 无（纯注释）。
- Produces: 注释如实反映 `backoff ** attempt` 的实际序列（`2^0,2^1,...`），且受 `ws.retry` 次数截断（例：默认 `retry: 1` 实际仅 `1` 秒）。

- [ ] **Step 1: 修正注释**

编辑 `collector/ws.py:117`：

```python
time.sleep(backoff ** attempt)  # 指数退避: backoff**attempt 秒,次数受 ws.retry 截断(默认 backoff=2,retry=1 时实际仅 1 秒)
```

- [ ] **Step 2: 提交**

```powershell
$git = 'D:\ucredit\liyuting\PortableGit\cmd\git.exe'
& $git add collector/ws.py
& $git commit -m @'
docs(ws): 修正指数退避注释与实际序列不一致

注释写 1,2,4,8...,实际 backoff**attempt 序列受 ws.retry 截断。
@'
```

---

## 执行收尾

- 运行全部测试确认无回归：
```powershell
$env:PYTHONIOENCODING="utf-8"
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

## Self-Review

- **Spec coverage**：主 bug（Task 1，含 TDD 测试）✅；重复 `_rows_in_month`（Task 2）✅；ws.py 注释（Task 3）✅。之前对话确认过的 3 个问题全部有对应任务。
- **Placeholder scan**：无 TBD/TODO；每个代码/命令步骤都含完整内容。
- **Type consistency**：Task 1 测试中 `scheduler._track_gap(name, ok, cfg)`、`_send_gap_alert(cfg, msg)`、`ws_job(cfg, pool)` 签名与现有实现一致；`_OK_VAL` 列名与 `storage.SCHEMAS` 中 热线 源的列匹配（取自 test_notify.py 既有用例）；Task 2 保留的函数名 `_rows_in_month` 与 `daily_latest`/`daily_avg` 调用一致。
