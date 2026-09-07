# manager.py 启停逻辑重构（目标状态收敛）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `ManagedTask` 的四套启停机制（每日自动启动/每日自动停止/崩溃重启/勾选框）收敛为单一规则——「自启动」勾选 + 运行时段 → 目标状态，每拍向目标收敛。

**Architecture:** `tick()` 重写为目标状态收敛：未勾选 → 不干预；勾选+窗口外 → 确保停止；勾选+窗口内 → 确保运行（3 次熔断暂停除外）。删除 `user_stopped`、`auto_started_today`、`auto_stopped_today` 三个状态字段；到点拉起、晚开机补拉、崩溃/手动停止后拉回全部走同一条「窗口内未运行则拉起」路径。设计规格见 `docs/superpowers/specs/2026-09-06-manager-start-stop-refactor-design.md`。

**Tech Stack:** Python 标准库（tkinter / subprocess / zoneinfo），plain-assert 测试（无 pytest）。

## Global Constraints

- 一律使用 `.venv` 解释器：`.\.venv\Scripts\python.exe`；运行前先 `$env:PYTHONIOENCODING="utf-8"`。
- 测试是 plain assert，不用 pytest；直接运行整个文件：`.\.venv\Scripts\python.exe tests\test_manager.py`。顺序执行，**首个断言失败即中断**。
- 不新增依赖；不改 `manager_state.json` 格式；不改 `config.yaml`；不改时间推导函数（`compute_auto_start` / `auto_stop_minutes` / `in_run_window` / `schedule_text`）。
- 不动：排班任务定义、`_find_external_pid` / `_pid_alive`、托盘、单实例守卫、进线量预测/数据补全/接待上限页面。
- 提交用 Conventional Commits，中文描述；每个任务一个提交。
- 测试在 Windows 本机与 Ubuntu CI 都要能跑（`_find_external_pid` 非 Windows 直接返回 None，现有测试已兼容该差异；本计划不引入新的平台差异）。
- 基线：当前 `tests/test_manager.py` 全绿；改动完成后必须全绿。
- 注意：Windows 本机上每次 `start()` 都会真实调用 PowerShell 做 `_find_external_pid` 探测（无匹配返回 None），测试因此偏慢（~10-30s），是既有现象，不要试图在测试里绕过它。

---

### Task 1: ManagedTask 状态机收敛 + manager.py 调用点适配

**Files:**
- Modify: `manager.py`（`ManagedTask.__init__` 字段 ~L215-241、`start` ~L306-340、`stop` ~L342-366、`restart` ~L368-373、`tick` ~L375-455、`_manual_stop` ~L617-619、`_update_status` ~L649-678）
- Modify: `tests/test_manager.py`

**Interfaces:**
- Consumes: 无（不依赖其他任务的产出）。
- Produces（Task 2 及调用方依赖的签名）:
  - `ManagedTask.stop(self) -> None` —— **删除了 `automatic` 参数**，所有调用点改为无参 `stop()`。
  - `ManagedTask.start(self, automatic: bool = False) -> bool` —— 签名不变；`automatic=True` 时日志动作名为「自动拉起」（原「自动重启」）。
  - `ManagedTask.tick(self, in_window: bool, now: dt.datetime) -> list[dict]` —— 签名不变；事件 msg：拉起=`"...: 运行时段内未运行,已自动拉起"`，停止=`"...: 超出运行时段,已自动停止"`，告警 type="alert"。
  - 新私有方法：`_process_died(self, now: dt.datetime, rc) -> list[dict]`、`_alert_if_tripped(self, events: list[dict]) -> None`。
  - **删除字段**：`user_stopped`、`auto_started_today`、`auto_stopped_today`——任何残留引用会在运行时 AttributeError，改完必须全局 grep 确认为零。

- [ ] **Step 1: 改写测试文件（先写失败测试）**

对 `tests/test_manager.py` 做以下修改。**删除**这 6 个函数（4 个语义消失 + 2 个测试对象消失）：

- `test_tick_manual_stop_blocks_auto_start`（手动停止不再阻止拉回）
- `test_tick_manual_start_outside_window`（行为反转，由改写后的 `test_tick_auto_stop` 后半段覆盖）
- `test_tick_user_stopped_reset_on_new_day`（`user_stopped` 字段删除；跨天复位由 `test_tick_popen_fail_breaker` 末段覆盖）
- `test_tick_checkbox_overrides_user_stopped`（`user_stopped` 字段删除，等价行为由 `test_tick_manual_stop_relaunch` 覆盖）
- `test_tick_no_crash_restart_outside_window`（场景消解：勾选状态下窗口外的进程会被直接停止，轮不到"崩溃重启"分支）
- `test_stop_automatic_parameter`（`stop()` 参数删除）

**替换**这 4 个函数为以下完整代码（`test_tick_auto_start` / `test_tick_auto_stop` / `test_tick_no_duplicate_auto_start` / `test_tick_auto_start_requires_checkbox`）：

```python
def test_tick_auto_start():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        now = dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH)
        events = task.tick(True, now)
    assert task.is_running(), "窗口内首次 tick 应自动启动"
    assert len(events) == 1 and "已自动拉起" in events[0]["msg"]
    print("tick_auto_start OK")


def test_tick_auto_stop():
    task = _make_task()
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        assert task.is_running()
        events = task.tick(False, dt.datetime(2026, 8, 1, 21, 30, tzinfo=SH))
    assert not task.is_running(), "窗口外 tick 应自动停止"
    assert len(events) == 1 and "已自动停止" in events[0]["msg"]
    # 勾选状态下窗口外手动再启 -> 收敛模型:再次被停止(原"只停一次"怪癖废除)
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.start()
        events2 = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert not task.is_running(), "勾选状态下窗口外手动启动应被自动停止"
    assert len(events2) == 1 and "已自动停止" in events2[0]["msg"]
    print("tick_auto_stop OK")


def test_tick_no_duplicate_auto_start():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()) as popen:
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        spawned = popen.call_count   # Windows: 探测+拉起=2;Linux: 仅拉起=1;只看增量
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
        assert popen.call_count == spawned, "第二拍不应再有任何拉起/探测"
    assert task.is_running()
    assert len(events) == 0, "运行中不应重复拉起"
    print("tick_no_duplicate_auto_start OK")

# 注:不要用 popen.assert_called_once()——Windows 上 subprocess.run 内部也调用模块级
# Popen,_find_external_pid 的探测会计入 mock,单次 start 即 2 次调用;Linux CI 只有 1 次。
# 快照对比增量才跨平台一致(Global Constraint:不引入平台差异)。


def test_tick_auto_start_requires_checkbox():
    """未勾选自启动:窗口内不自动拉起;中途勾选后窗口内立即拉起。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)  # 避开跨天重置
    task.auto_start = False
    with patch("manager.subprocess.Popen", return_value=_running_proc()) as popen:
        events = task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        popen.assert_not_called()
    assert not task.is_running()
    assert len(events) == 0
    task.auto_start = True
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
    assert task.is_running(), "勾选后处于运行时段应立即自启动"
    assert len(events) == 1 and "已自动拉起" in events[0]["msg"]
    print("tick_auto_start_requires_checkbox OK")
```

**修改**这 2 个保留函数的调用语法（`start(automatic=False)` → `start()`，语义不变）：

`test_tick_manual_only_no_auto_start_stop` 中 L214 的 `task.start(automatic=False)` 改为 `task.start()`；
`test_tick_no_crash_restart_when_unchecked` 中 L384 的 `task.start(automatic=False)` 改为 `task.start()`。

**新增**这 4 个测试函数（放在「ManagedTask.tick() 行为测试」区块末尾、`# ── 「自启动」勾选框行为` 注释之前）：

```python
def test_tick_manual_stop_relaunch():
    """勾选+窗口内手动停止:收敛模型应在下一拍自动拉回(≤5s)。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        assert task.is_running()
        task.stop()
        assert not task.is_running()
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
    assert task.is_running(), "勾选状态下窗口内手动停止后应被自动拉回"
    assert len(events) == 1 and "已自动拉起" in events[0]["msg"]
    print("tick_manual_stop_relaunch OK")


def test_tick_unchecked_not_stopped_outside_window():
    """未勾选:窗口外在跑也不被自动停止(manager 零干预)。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    task.auto_start = False
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start()
        events = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert task.is_running(), "未勾选的任务窗口外不应被自动停止"
    assert len(events) == 0
    print("tick_unchecked_not_stopped_outside_window OK")


def test_tick_popen_fail_breaker():
    """Popen 启动失败计入熔断:第 3 次失败当拍告警并暂停拉起;跨天复位后恢复。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    with patch("manager.subprocess.Popen", side_effect=OSError("boom")):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))            # 失败 1/3
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))            # 失败 2/3
        events3 = task.tick(True, dt.datetime(2026, 8, 1, 9, 31, tzinfo=SH))  # 失败 3/3 -> 告警
        events4 = task.tick(True, dt.datetime(2026, 8, 1, 9, 32, tzinfo=SH))  # 已熔断 -> 去重
    assert task.restart_failures == 3
    assert not task.is_running()
    assert len(events3) == 1 and events3[0]["type"] == "alert", "第 3 次失败当拍应告警"
    assert len(events4) == 0, "告警当日去重"
    # 跨天复位熔断,恢复自动拉起
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 2, 8, 35, tzinfo=SH))
    assert task.is_running(), "跨天复位后应恢复自动拉起"
    assert task.restart_failures == 0
    print("tick_popen_fail_breaker OK")


def test_tick_quick_crash_trips_breaker():
    """启动后 <30s 崩溃连续 3 次:熔断告警一次并暂停拉起(uptime 计数路径)。"""
    task = _make_task()
    task._current_date = dt.date.today()
    now = dt.datetime.now().astimezone()
    proc = _running_proc()
    events_all = []
    with patch("manager.subprocess.Popen", return_value=proc):
        for _ in range(3):
            proc.poll.return_value = None      # 复位为运行中,先拉起
            task.tick(True, now)
            proc.poll.return_value = 1         # 5 秒后崩溃(< 30s 宽限)
            events_all.extend(task.tick(True, now + dt.timedelta(seconds=5)))
    assert task.restart_failures == 3, "连续 3 次秒退应计满失败"
    alerts = [e for e in events_all if e["type"] == "alert"]
    assert len(alerts) == 1, "熔断告警只弹一次"
    with patch("manager.subprocess.Popen", return_value=_running_proc()) as popen:
        task.tick(True, now + dt.timedelta(seconds=10))
        popen.assert_not_called(), "熔断后不应再拉起"
    print("tick_quick_crash_trips_breaker OK")
```

**替换** `main()` 为（删除 6 个旧函数调用，加入新函数）：

```python
def main():
    test_auto_start_weekday()
    test_auto_start_weekend()
    test_auto_stop()
    test_in_run_window_weekday()
    test_in_run_window_weekend()
    test_schedule_text()
    test_parse_schedule()
    test_schedule_action()
    test_forecast_summary()
    test_tick_auto_start()
    test_tick_auto_stop()
    test_tick_no_duplicate_auto_start()
    test_tick_manual_stop_relaunch()
    test_tick_unchecked_not_stopped_outside_window()
    test_tick_no_crash_restart_when_unchecked()
    test_tick_popen_fail_breaker()
    test_tick_quick_crash_trips_breaker()
    test_tick_manual_only_no_auto_start_stop()
    test_find_external_pid_matches_pythonw()
    test_tick_health_check_clears_failures()
    test_ui_constructs()
    test_update_status_sets_dot()
    test_member_limit_schedule_rearm()
    test_tick_auto_start_requires_checkbox()
    test_auto_start_state_roundtrip()
    test_ui_auto_start_checkboxes()
    print("ALL manager tests OK")
```

- [ ] **Step 2: 运行测试确认失败（红）**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_manager.py
```

预期：**FAIL**——顺序执行在第一个新断言处中断（`test_tick_auto_start` 的 `"已自动拉起" in ...` 断言，旧消息是「已自动启动」）。plain-assert 一次只报一个失败，无需看到全部 6 个红（`test_tick_auto_start` / `test_tick_auto_stop` / `test_tick_manual_stop_relaunch` / `test_tick_unchecked_not_stopped_outside_window` / `test_tick_popen_fail_breaker` / `test_tick_auto_start_requires_checkbox` 都会在旧实现上失败；其余为行为保留的钉子测试，旧实现也应通过）。

- [ ] **Step 3: 实现 manager.py 状态机收敛**

3a. `ManagedTask.__init__`：字段注释与删除。将

```python
        self.auto_enabled = auto_enabled          # False -> 仅手动启停,不自动启停/自动重启
        self.auto_start = True                    # 「自启动」勾选(UI 持久化);False 时到点不自启、崩溃不自动重启
```

改为

```python
        self.auto_enabled = auto_enabled          # False -> 仅手动启停,不自动启停/自动重启
        self.auto_start = True                    # 「自启动」勾选(UI 持久化);False -> manager 不干预该任务
```

将

```python
        self.process: subprocess.Popen | None = None
        self.external_pid: int | None = None
        self.user_stopped = False
        self.restart_failures = 0
        self.started_at: dt.datetime | None = None
        self.popup_shown = False                  # 3 次失败告警去重
        self._log_handle = None

        self._current_date: dt.date | None = None # 当前已重置到的日期
        self.auto_started_today = False           # 当天是否已触发过自动启动
        self.auto_stopped_today = False           # 当天是否已触发过自动停止
```

改为（删 `user_stopped` / `auto_started_today` / `auto_stopped_today` 三行）

```python
        self.process: subprocess.Popen | None = None
        self.external_pid: int | None = None
        self.restart_failures = 0
        self.started_at: dt.datetime | None = None
        self.popup_shown = False                  # 熔断告警去重(跨天重置)
        self._log_handle = None

        self._current_date: dt.date | None = None # 当前已重置到的日期(跨天重置熔断计数用)
```

3b. `start()`：删两处 `self.user_stopped = False`，日志措辞改「自动拉起」。完整新函数：

```python
    def start(self, automatic: bool = False) -> bool:
        if self.is_running():
            return True
        ext = self._find_external_pid()
        if ext:
            self.external_pid = ext
            self.restart_failures = 0
            self.popup_shown = False
            log.info("%s: 发现外部进程 pid=%s,接管", self.name, ext)
            return True

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            if self.capture_log:
                self._log_handle = open(self.log_path, "ab")
                stdout = self._log_handle
            else:
                stdout = subprocess.DEVNULL
            self.process = subprocess.Popen(
                self.cmd, cwd=str(self.cwd),
                stdout=stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=creationflags, env=self._env(),
            )
            self.started_at = dt.datetime.now().astimezone()
            self.popup_shown = False
            action = "自动拉起" if automatic else "手动启动"
            log.info("%s: %s pid=%s", self.name, action, self.process.pid)
            return True
        except Exception as exc:
            log.exception("%s: 启动失败: %s", self.name, exc)
            return False
```

3c. `stop()`：删 `automatic` 参数与 `user_stopped` 赋值。完整新函数：

```python
    def stop(self) -> None:
        if self.external_pid and not self.is_running():
            self._kill_external()
            self.external_pid = None
            return
        if not self.is_running():
            self._cleanup_process()
            self.external_pid = None
            return
        pid = self.process.pid
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            log.info("%s: 已停止 pid=%s", self.name, pid)
        except Exception:
            log.exception("%s: 停止失败 pid=%s", self.name, pid)
        finally:
            self._cleanup_process()
            self.external_pid = None
```

3d. `restart()`：删 `user_stopped` 行。完整新函数：

```python
    def restart(self) -> None:
        self.stop()
        self.restart_failures = 0
        self.popup_shown = False
        self.start()
```

3e. 用以下两个新方法 + 新 `tick()` **整体替换**旧 `tick()`（含其上方 `# ---- 监控一拍 ----` 注释之后到 `# ── 受管任务` 分隔线之前的全部内容）：

```python
    # ---- 监控一拍 ----
    def tick(self, in_window: bool, now: dt.datetime) -> list[dict]:
        """每 MONITOR_INTERVAL_MS 调一次;向目标状态收敛,返回事件列表({"type":"alert"|"info","msg":...})。

        目标状态由「自启动」勾选(auto_start)与运行时段唯一决定:
        未勾选 -> 不干预;勾选+窗口外 -> 确保停止;勾选+窗口内 -> 确保运行(熔断暂停除外)。
        到点拉起、晚开机补拉、崩溃/手动停止后拉回都由同一条"窗口内未运行则拉起"规则覆盖。
        """
        events: list[dict] = []
        today = now.date()

        # 1. 跨天重置(仅熔断相关)
        if self._current_date != today:
            self._current_date = today
            self.restart_failures = 0
            self.popup_shown = False
            log.info("%s: 跨天重置,日期=%s", self.name, today)

        # 2. 仅手动启停的任务:完全由手动控制
        if not self.auto_enabled:
            return events

        # 3. 未勾「自启动」:manager 不做任何干预(不自动启停、不自动重启)
        if not self.auto_start:
            return events

        # 4. 窗口外:目标 = 停止
        if not in_window:
            if self.is_running() or self.external_pid:
                self.stop()
                events.append({"type": "info", "msg": f"{self.name}: 超出运行时段,已自动停止"})
            return events

        # 5. 窗口内:目标 = 运行(熔断触发时暂停拉起)
        if self.process is not None:
            rc = self.process.poll()
            if rc is None:
                # 仍在运行;跑过 grace 即视为健康,清零失败计数
                if self.started_at and (now - self.started_at).total_seconds() >= GRACE_SECONDS:
                    if self.restart_failures:
                        self.restart_failures = 0
            else:
                # 进程已退出 -> 计数并重新拉起或熔断
                self._cleanup_process()
                events.extend(self._process_died(now, rc))
        elif self.external_pid is not None:
            # 接管的外部进程:周期探活;死亡则清理,下一拍由 5d 统一拉起
            if not _pid_alive(self.external_pid):
                log.warning("%s: 外部进程 pid=%s 已退出,清理", self.name, self.external_pid)
                self.external_pid = None
        else:
            # 没有任何进程:到点首拉 / 手动停止后保活拉回 / 上拍拉起失败重试
            if self.restart_failures >= MAX_FAILURES:
                self._alert_if_tripped(events)
            elif self.start(automatic=True):
                events.append({"type": "info", "msg": f"{self.name}: 运行时段内未运行,已自动拉起"})
            else:
                self.restart_failures += 1
                log.warning("%s: 拉起失败,失败 %d/%d", self.name, self.restart_failures, MAX_FAILURES)
                self._alert_if_tripped(events)
        return events

    def _process_died(self, now: dt.datetime, rc) -> list[dict]:
        """窗口内本 UI 拉起的进程退出:按 uptime 计数,熔断告警或重新拉起。"""
        events: list[dict] = []
        uptime = (now - self.started_at).total_seconds() if self.started_at else 0
        if uptime < GRACE_SECONDS:
            self.restart_failures += 1
            log.warning("%s: 启动后 %.0fs 即退出(rc=%s),失败 %d/%d",
                        self.name, uptime, rc, self.restart_failures, MAX_FAILURES)
        else:
            self.restart_failures = 0
            log.warning("%s: 运行 %.0fs 后退出(rc=%s),正常重启", self.name, uptime, rc)
        if self.restart_failures >= MAX_FAILURES:
            self._alert_if_tripped(events)
            return events
        if not self.start(automatic=True):
            self.restart_failures += 1
            log.warning("%s: 拉起失败,失败 %d/%d", self.name, self.restart_failures, MAX_FAILURES)
            self._alert_if_tripped(events)
        return events

    def _alert_if_tripped(self, events: list[dict]) -> None:
        """熔断告警(当日去重):append 一条 alert 事件。"""
        if self.restart_failures >= MAX_FAILURES and not self.popup_shown:
            self.popup_shown = True
            events.append({"type": "alert",
                           "msg": f"{self.name} 连续重启 {MAX_FAILURES} 次失败,已暂停自动重启。\n请检查日志:{self.log_path}"})
```

3f. `ManagerUI._manual_stop`：`task.stop(automatic=False)` 改为 `task.stop()`：

```python
    def _manual_stop(self, task: ManagedTask) -> None:
        task.stop()
        self._update_status()
```

3g. `ManagerUI._update_status`：删「已停止」分支（`elif task.user_stopped:` 四行），else 块变为：

```python
            else:
                if task.restart_failures >= MAX_FAILURES:
                    vars_["status"].set("已暂停重启")
                    vars_["status_label"].configure(fg="#aa2222")
                    vars_["status_dot"].configure(fg="#aa2222")
                else:
                    vars_["status"].set("未运行")
                    vars_["status_label"].configure(fg="#aa2222")
                    vars_["status_dot"].configure(fg="#aa2222")
                vars_["pid"].set("PID: -")
                vars_["src"].set("")
```

3h. 残留引用检查（必须零匹配）：

```powershell
Select-String -Path manager.py, tests\test_manager.py -Pattern "user_stopped|auto_started_today|auto_stopped_today|automatic=False|automatic=True" | Select-Object Path, LineNumber, Line
```

预期：`manager.py` 仅剩这些合法匹配——`start` 的签名定义行、tick 5d 与 `_process_died` 里的 `self.start(automatic=True)` 两处、`action = "自动拉起" if automatic ...` 行、以及 `_manual_start` 里的 `task.start(automatic=False)`（手动启动日志语义，保留）；**不得出现** `user_stopped` / `auto_started_today` / `auto_stopped_today` / `stop(automatic`。tests 文件不得出现 `automatic=` 与任何被删字段。

- [ ] **Step 4: 运行测试确认通过（绿）**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_manager.py
```

预期：输出最后一行 `ALL manager tests OK`，无 traceback。

- [ ] **Step 5: Commit**

```powershell
git add manager.py tests/test_manager.py
git commit -m "refactor(manager): tick 收敛为勾选框驱动的目标状态机"
```

---

### Task 2: 重新勾选自启动复位熔断 + 模块文档更新

**Files:**
- Modify: `manager.py`（模块 docstring L1-14、`_toggle_auto_start` ~L625-628）
- Modify: `tests/test_manager.py`（新增测试 + main() 追加一行）

**Interfaces:**
- Consumes: Task 1 的 `ManagedTask`（`restart_failures` / `popup_shown` 字段、无参 `stop()`）。
- Produces: `_toggle_auto_start(task)` 行为扩展——重新勾上（False→True）时复位 `restart_failures=0`、`popup_shown=False`；取消勾选不动计数。

- [ ] **Step 1: 写失败测试**

在 `tests/test_manager.py` 的 `test_ui_auto_start_checkboxes` 之后新增：

```python
def test_ui_toggle_resets_breaker():
    """熔断后重新勾选自启动:复位失败计数与告警去重,恢复自动调度;取消勾选不动计数。"""
    _TMP.mkdir(exist_ok=True)
    state_path = _TMP / "ui_toggle_state.json"
    state_path.unlink(missing_ok=True)
    with patch("manager.AUTO_START_STATE", state_path):
        with patch.object(ManagerUI, "_build_tray", lambda self: None), \
             patch.object(ManagerUI, "_refresh", lambda self: None):
            root = tk.Tk(); root.withdraw()
            ui = ManagerUI(root, _cfg())
            try:
                task = ui.tasks[0]
                task.restart_failures = 3
                task.popup_shown = True
                ui._auto_start_vars["采集器"].set(False)
                ui._toggle_auto_start(task)      # 取消勾选:不动计数
                assert task.restart_failures == 3, "取消勾选不应复位计数"
                ui._auto_start_vars["采集器"].set(True)
                ui._toggle_auto_start(task)      # 重新勾上:复位熔断
                assert task.auto_start is True
                assert task.restart_failures == 0, "重新勾选应复位失败计数"
                assert task.popup_shown is False, "重新勾选应复位告警去重"
            finally:
                root.destroy()
    state_path.unlink(missing_ok=True)
    print("ui_toggle_resets_breaker OK")
```

并在 `main()` 的 `test_ui_auto_start_checkboxes()` 之后追加一行：

```python
    test_ui_toggle_resets_breaker()
```

- [ ] **Step 2: 运行测试确认失败（红）**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_manager.py
```

预期：**FAIL** 于 `test_ui_toggle_resets_breaker` 内 `assert task.restart_failures == 0, "重新勾选应复位失败计数"`——前一处 `assert task.restart_failures == 3`（取消勾选不动计数）旧实现也满足；旧的 `_toggle_auto_start` 勾上后不复位计数，故第二处断言失败。

- [ ] **Step 3: 实现**

3a. `_toggle_auto_start` 替换为：

```python
    def _toggle_auto_start(self, task: ManagedTask) -> None:
        task.auto_start = self._auto_start_vars[task.name].get()
        if task.auto_start:
            # 重新勾上=明确要求恢复自动调度,解除熔断暂停
            task.restart_failures = 0
            task.popup_shown = False
        save_auto_start_state({name: v.get() for name, v in self._auto_start_vars.items()})
        log.info("%s: 自启动勾选=%s", task.name, task.auto_start)
```

3b. 模块 docstring（L1-14）替换为：

```python
"""AutoWFM 桌面管理器。

管理采集器(`collector.main`)、API(`api.app`)、看板(`dashboard.app`)与排班(`shift/app.py`)子进程:
- 目标状态收敛:勾选「自启动」的任务,运行时段内保持运行(意外退出或手动停止后 ≤5s 自动拉回,
  连续 3 次启动失败熔断并弹窗告警),时段外保持停止;到点拉起与晚开机补拉由同一规则覆盖。
- 时间从 config.yaml 推导:起点=最早采集窗口(工作日 08:30/周末 09:00),终点=全局 window_end。
- 未勾选「自启动」:manager 不做任何干预,纯手动启停;勾选状态持久化到 manager_state.json。
  排班无此开关,始终纯手动。
- 手动启动/重启/重新勾选都会复位熔断计数,是解除「已暂停重启」的手段。

运行:
    .\\.venv\\Scripts\\python.exe manager.py
"""
```

- [ ] **Step 4: 运行测试确认通过（绿）**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe tests\test_manager.py
```

预期：`ALL manager tests OK`。

- [ ] **Step 5: Commit**

```powershell
git add manager.py tests/test_manager.py
git commit -m "refactor(manager): 重新勾选自启动复位熔断,更新模块文档"
```

---

### Task 3: 全量回归

**Files:** 无新增/修改（纯验证）。

- [ ] **Step 1: 跑全部测试**

```powershell
$env:PYTHONIOENCODING="utf-8"
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

预期：每个文件输出各自的 `... OK` / `ALL ... OK` 行，无 traceback、无 `[exit code: N]`。若某个与 manager 无关的测试失败，先用 `git stash && git checkout main` 之外的方式核对基线：`git stash; .\.venv\Scripts\python.exe tests\<该文件>; git stash pop` 确认失败是否在本改动之前就存在（存在则为预存问题，记录并跳过；不存在则回退排查本改动）。

- [ ] **Step 2: 冒烟导入检查**

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import manager; print('import ok')"
```

预期：`import ok`（Tk UI 构造已由 test_ui_constructs 覆盖，这里只验证模块级语法/导入）。

无代码变更，不需要提交。

---

## 行为变化备忘（验收对照）

实施完成后，以下行为与旧版不同，属预期（规格 §5）：

1. 勾着自启动 + 窗口内手动停止 → ≤5s 被拉回（想停先取消勾选）。
2. 勾着自启动 + 窗口外手动启动 → ≤5s 被停止。
3. 未勾选的任务不再被窗口结束自动停止。
4. Popen 启动失败计入 3 次熔断（旧版无限重试不熔断）。
5. 状态显示只剩：运行中 / 运行中(外部) / 已暂停重启 / 未运行。
