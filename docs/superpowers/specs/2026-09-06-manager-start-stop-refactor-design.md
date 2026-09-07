# manager.py 启停逻辑重构：目标状态收敛（desired-state reconciliation）

日期：2026-09-06
状态：已与用户确认的设计（brainstorming 产出）
范围：`manager.py`（`ManagedTask` 状态机 + 少量 UI/日志）与 `tests/test_manager.py`

## 1. 背景与动机

当前 `ManagedTask.tick()` 用四套分散的机制管理启停：每日一次自动启动（`auto_started_today`）、每日一次自动停止（`auto_stopped_today`）、崩溃自动重启（受 `user_stopped` 抑制）、「自启动」勾选框（`auto_start`，控制每日启动与崩溃重启）。手动停止会设置 `user_stopped=True`，即使勾选框勾着，当天也不再被拉回；未勾选的任务到窗口结束仍会被自动停止；Popen 启动失败会每 5 秒无限重试且不触发熔断。

用户需求：

1. **每日拉起**：每天到固定时间拉起一次；电脑晚开机也不错过（开机后 manager 发现今天还没拉起就补拉）。
2. **时段内保活**：进程意外退出**或手动停止**后，是否重新拉起**只看勾选框**，且只在运行时段内生效。

## 2. 已确认的决策（brainstorming 结论）

| 决策点 | 结论 |
|---|---|
| 手动停止（勾着 + 时段内） | 立即拉回（≤5s）；想真正停掉，先取消勾选再点停止 |
| 勾选框管辖范围 | 勾 = 全自动（到点拉起 + 时段内保活 + 时段外停止）；不勾 = manager 完全不碰，纯手动 |
| 固定时间来源 | 沿用现有推导：起点 = 采集子窗口最早值（工作日 08:30 / 周末 09:00），终点 = 全局 `window_end`（现为 21:04）；改时间 = 改 `config.yaml` |
| 实现方案 | 方案 A「目标状态收敛」；否决最小修补（残留不一致状态）与独立调度线程（过度设计） |

## 3. 核心设计：tick() 重写为向目标状态收敛

每拍（`MONITOR_INTERVAL_MS` = 5s）按序执行：

```
1. 跨天重置（所有任务，与现状同序）：只重置 restart_failures 与 popup_shown
2. if not auto_enabled → return（排班，无勾选框，完全手动；现状保留）
   （删除 user_stopped / auto_started_today / auto_stopped_today 三个字段及其重置）

3. 未勾「自启动」（task.auto_start == False）→ 直接返回，manager 零干预
   （新行为：不再被窗口结束自动停止）

4. 窗口外（not in_window）→ 目标 = 停止：
   在跑（含外部接管进程 external_pid）就 stop()，产生一条 info 事件；
   不在跑 → 无操作。stop 幂等，事件天然只发一次。

5. 窗口内 → 目标 = 运行（熔断未触发时）：
   a. self.process 存活        → 30s 宽限（GRACE_SECONDS）后清零失败计数（健康检查，保留现状）
   b. self.process 已退出      → 清句柄；uptime <30s → failures++，≥30s → failures 归零；
        failures ≥ MAX_FAILURES(3) → 弹一次置顶告警（popup_shown 当日去重），本拍不拉起；
        否则 start(automatic=True)；start 返回 False（启动异常）→ failures++
   c. external_pid 存在        → tasklist 探活；已死 → 清掉 external_pid，下一拍由 d 统一拉起
                                 （不计数：外部进程无 started_at，uptime 计数不适用）
   d. 没有任何进程             → 未熔断就 start()（启动失败 failures++）；已熔断 → 弹一次告警（去重），保持暂停
```

两条用户需求不再有专门分支：

- **每日固定时间拉起** = 进入窗口的第一拍命中 5d；晚开机首拍同样命中 5d（补拉）。拉起后保活接管，当天不会重复拉起。
- **终止后拉回** = 5b/5c/5d；崩溃与手动停止走完全相同的路径，勾选框是唯一开关。

事件去重机制说明：拉起后在跑 → 下拍无操作；停止后没跑 → 无操作，因此不需要每日标志位。

## 4. 周边代码变化

| 位置 | 变化 |
|---|---|
| `ManagedTask.__init__` | 删除 `user_stopped`、`auto_started_today`、`auto_stopped_today` 字段 |
| `stop()` | 删除 `automatic` 参数（唯一用途是设 `user_stopped`）；所有调用点统一 `stop()` |
| `start()` | 删除 2 处 `user_stopped = False` 赋值；外部进程接管、Popen 拉起逻辑不变 |
| `restart()` | 删除 `user_stopped = False` 行；其余不变（清零计数 → start） |
| `_toggle_auto_start()` | 新增：重新勾上时复位 `restart_failures` / `popup_shown`（取消再勾 = 明确意图，解除熔断暂停） |
| `_update_status()` | 删除「已停止」分支；剩 4 态：运行中 / 运行中(外部) / 已暂停重启 / 未运行 |
| `_manual_start` / `_manual_restart` | 不变（仍复位熔断计数，是解除「已暂停重启」的手段） |
| 模块 docstring | 更新为新语义；修掉「21:00」过时描述（时间由 config 推导，现为 21:04） |
| 日志 | `start(automatic=True)` 的动作名统一为「自动拉起」（现「自动重启」对首拉有误导） |

不动：`manager_state.json` 格式（零迁移）、时间推导函数（`compute_auto_start` / `auto_stop_minutes` / `in_run_window` / `schedule_text`）、外部进程接管（`_find_external_pid`）与探活（`_pid_alive`）、3 次熔断 + 30s 宽限、告警弹窗、排班任务定义、单实例守卫、托盘。

## 5. 行为变化清单（相对当前实现）

1. 勾着 + 时段内手动停止 → ≤5 秒被拉回（想停先取消勾选）。
2. 勾着 + 时段外手动启动 → ≤5 秒被停止（想晚上跑先取消勾选；当前行为是「当天自动停止已触发过则可存活」，时序依赖的怪癖，废除）。
3. 未勾选的任务不再被窗口结束自动停止（当前会被停）——「不勾 = 不碰」的一致性代价。
4. Popen 启动失败计入 3 次熔断（当前每 5 秒无限重试且不熔断不告警）。
5. 状态显示：「已停止」与「未运行」合并（区别本只靠 `user_stopped`）。

## 6. 关键场景推演

| 场景 | 行为 |
|---|---|
| 08:30 到点，任务没跑 | 5d → 拉起，日志「自动拉起」 |
| 10:00 才开机，manager 自启 | 首拍 5d → 补拉（需求 1 ✓） |
| 22:00 才开机 | 窗口外 → 不拉起；次日 08:30 拉起 |
| 窗口内崩溃 / 手动停止（勾着） | ≤5s 拉回（需求 2 ✓）；连崩 3 次熔断 + 置顶告警一次 |
| 21:04 在跑（勾着） | 停止；之后每拍无操作 |
| 22:00 手动启动（勾着） | ≤5s 被停止 |
| 未勾选，任意操作 | manager 零干预；手动按钮全可用；跑通宵也不动 |
| 熔断后手动启动 / 重启 / 重新勾选 | 复位计数，恢复自动调度 |
| 排班（auto_enabled=False） | 完全不变（无勾选框、纯手动） |
| 外部已启动的进程（如手动命令行起的看板） | start() 时接管（现状保留）；窗口外勾着 → 停止它 |

## 7. 测试计划（tests/test_manager.py）

- **删除**（语义消失）：`test_tick_manual_stop_blocks_auto_start`、`test_tick_user_stopped_reset_on_new_day`、`test_tick_checkbox_overrides_user_stopped`、`test_tick_manual_start_outside_window`
- **改写**（flag 断言 → 行为断言）：
  - `test_tick_auto_start`：不再断言 `auto_started_today`
  - `test_tick_no_duplicate_auto_start`：第二拍在跑 → 不再调用 Popen
  - `test_tick_auto_stop`：自动停止后手动再启 → 又被停
  - `test_tick_auto_start_requires_checkbox`：未勾不拉，当日勾回即拉
- **新增**：
  - 手动停止 + 勾选 + 窗口内 → 拉回
  - 未勾选 + 在跑 + 窗口外 → 不停止
  - Popen 失败 ×3 → 熔断 + 告警事件恰好一次
  - 勾选切换（取消→再勾）复位熔断计数
- **不动**：时间推导、state 持久化 roundtrip、UI 勾选框、排班 manual-only、健康检查清零、外部进程接管

## 8. 错误处理与回滚

- `_refresh()` 外层 try/except 保留（单拍异常不影响下拍）。
- 探活命令异常保守认为存活（`_pid_alive` 现状保留，避免误杀）。
- 单文件重构，git revert 即可整体回滚；`manager_state.json` 无格式变化，新旧版本互通。
