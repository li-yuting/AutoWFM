# manager.py 排版重构设计

- 日期: 2026-08-01
- 范围: `manager.py` 中 `ManagerUI` 的 UI 外壳（`_build_ui` 及页面构建方法的父容器）
- 目标: 在保持原生 ttk 控件的前提下，让管理器排版更清爽协调、操作按钮分组清晰
- 不在范围: 任何业务行为逻辑（采集器/看板进程管理、预测、补全、告警、托盘等）

## 1. 背景与问题

当前 `ManagerUI._build_ui` 自上而下为：标题栏、两个任务状态 `LabelFrame`、`ttk.Notebook`（4 个 tab）、底部操作栏。存在两个问题：

1. **视觉不协调**：两个状态卡片用 `pack(side=LEFT)` 平铺，采集器/看板两行的「PID、失败数、按钮」位置对不齐；四个区域之间无分隔线，整体扁平无层次。
2. **操作按钮位置乱**：底部栏把「刷新日志」（日志相关）与「开机自启」「重启控制台」（系统相关）混在一排，无语义分组。

用户决策（经头脑风暴确认）：
- 视觉风格走「原生 ttk 但更清爽」，不引入深色主题或重度自定义样式。
- 用**左侧导航栏**替代顶部 `Notebook` 选项卡，每个功能一个导航按钮；「数据补全」作为独立功能也单独占一个导航入口。
- 任务状态（运行中 / PID / 失败数 / 启动·停止·重启按钮）**顶部常驻**，切到任何导航页都能看到。

## 2. 整体布局

四区结构，区间用 `ttk.Separator` 分隔：

```
┌─ AutoWFM 管理器 ────── 每日计划(工作日): 08:30 -> 21:00 ─┐
├──────────────────────────────────────────────────────┤  ttk.Separator
│ ● 采集器  运行中  PID:12345  失败0/3      [启动][停止][重启] │  状态条(常驻,无LabelFrame)
│ ● 看板    运行中  PID:12346  失败0/3      [启动][停止][重启] │
├──────┬───────────────────────────────────────────────┤  ttk.Separator
│采集器│                                               │
│日志  │                                               │
│看板  │           (当前选中导航的内容)                   │  主区 = 左导航 + 右内容
│日志  │                                               │
│进线量│                                               │
│预测  │                                               │
│数据  │                                               │
│补全  │                                               │
├──────┴───────────────────────────────────────────────┤  ttk.Separator
│ [刷新日志]   │   [开机自启]  [重启控制台]   │  日志:…/manager.log │  底栏(分组)
└──────────────────────────────────────────────────────┘
```

- 窗口尺寸保持 `980x680`，`minsize 820x560`。
- 顶部标题栏：「AutoWFM 管理器」+ `schedule_var`（每日计划文本），一行。

## 3. 状态条（顶部常驻）

- 去掉现有两个 `LabelFrame`（采集器 / 看板）的边框，合成**一个 Frame 两行**。
- 列用 `grid` 严格对齐，两行同列位置一致：

  | 列 | 内容 |
  |----|------|
  | 0 | 状态点（有色小 `Label`，见下） |
  | 1 | 任务名（采集器 / 看板） |
  | 2 | 状态文字（运行中 / 已停止 / 已暂停重启 等，`status_label`） |
  | 3 | `PID: …` |
  | 4 | `失败: n/3` |
  | 5 | 弹性间距（`columnconfigure(weight=1)`） |
  | 6 | 启动 / 停止 / 重启 三个 `ttk.Button` |

- 状态点颜色：运行中（含外部）= 绿 `#16803c`；已停止 / 未运行 = 灰 `#666666`；已暂停重启 = 红 `#aa2222`。与现有 `_update_status` 里 `status_label` 的 `fg` 颜色语义一致。
- `_update_status()` 仍更新同一批 `StringVar`（`status`/`pid`/`fail`/`src`）与 `status_label.configure(fg=…)`，控件换到 `grid` 布局；唯一新增是按同样的状态分支给状态点 `Label` 设 `fg`（复用现有颜色值），属视觉接线、不改变行为逻辑。

## 4. 左侧导航

- 左侧固定宽度（约 120px）导航 Frame，4 个导航按钮纵向排列，顺序：
  1. 采集器日志
  2. 看板日志
  3. 进线量预测
  4. 数据补全
- **实现方式**：自定义导航按钮（`ttk.Radiobutton` 带选中态，或 `tk.Button` + 选中 relief 切换）。选中项高亮（浅色底 + 左侧强调边），未选中默认态。
- **页面切换**：右侧内容区 4 个内容 Frame 用 `grid(row=0, column=0)` 叠放在同一 cell；点导航按钮调用对应 Frame 的 `raise_()`。默认选中「采集器日志」。
- **内容 Frame 复用**：
  - 采集器日志 / 看板日志：沿用现有两个 `scrolledtext.ScrolledText`（`self._log_boxes`），只换父容器为对应内容 Frame。`_load_logs()` 仍 `zip(self.tasks, self._log_boxes)` tail 日志，逻辑不动。
  - 进线量预测：`_build_forecast_page(page)` 传入对应内容 Frame，方法内部不动。
  - 数据补全：`_build_backfill_page(page)` 传入对应内容 Frame，方法内部不动。
- **不采用** `ttk.Notebook(tabposition='w')`：虽改动更小，但视觉是「左侧 tab」而非「导航按钮」，且样式受主题限制，不符合「清爽导航」诉求。

## 5. 底栏（分组）

- 一行 `ttk.Frame`，按语义分 3 组，组间竖向 `ttk.Separator`（`orient='vertical'`）隔开：
  1. `[刷新日志]`（日志相关，左）
  2. `[开机自启]` `[重启控制台]`（系统相关，中）
  3. 管理器日志路径标签（`fg="#777777"`，右对齐）
- 按钮统一改用 `ttk.Button`、等宽（现有「启动/停止/重启」也一并统一为 `ttk.Button`）。
- `刷新日志` 仍调 `_load_logs()`；`开机自启` 仍 `textvariable=self.autostart_var` + `_toggle_autostart`；`重启控制台` 仍 `_restart_manager`。回调全部不动。

## 6. 不变的部分

以下全部保持原样，本次重构不触碰：

- `ManagedTask` 类、`tick()`、自动启停、崩溃重启、外部 PID 接管。
- `_refresh()` 监控循环（仍调 `_update_status` / `_load_logs` / `_check_forecast`）。
- 21:05 次日预测量差异对比（`_check_forecast` / `_run_forecast_diff`）。
- 托盘（`_build_tray` / `_hide_to_tray` / `_restore_window`）、开机自启（`autostart_enabled` / `set_autostart`）、重启控制台（`_restart_manager`）、告警弹窗（`_show_alert`）。
- 进线量预测页业务逻辑（`_run_forecast` / `_on_forecast_done` / `_forecast_summary`）。
- 数据补全页业务逻辑（`_run_backfill` / `_on_backfill_progress` / `_on_backfill_done`）。
- 调度纯函数（`compute_auto_start` / `auto_stop_minutes` / `in_run_window` / `schedule_text`）。

## 7. 测试

- `tests/test_manager.py` 覆盖纯函数 + `ManagedTask.tick()` + `ManagerUI._forecast_summary`，均不涉及 UI 布局，重构后保持全绿、无需改动。
- Tk UI 本身按项目惯例不写自动化测试（`CLAUDE.md`：「The Tk UI itself is not tested」），靠手动验证：
  - 启动 `manager.py`，确认四区 + 左导航渲染正常，默认显示「采集器日志」。
  - 依次点 4 个导航按钮，确认右侧内容切换、选中态高亮正确。
  - 确认状态条两行列对齐、状态点颜色随采集器/看板状态变化。
  - 确认底栏三组分隔、各按钮回调正常（刷新日志、开机自启切换、重启控制台）。
  - 确认采集器/看板启动·停止·重启按钮、托盘最小化/恢复、告警弹窗仍正常。

## 8. 风险与回退

- 风险低：改动集中在 `_build_ui` 的容器结构与控件父容器，业务回调与数据流不变。
- 主要注意点：`_update_status` 里 `vars_["status_label"]` 等控件引用需在新的 `grid` 控件上正确建立（与现有 `self._vars` 结构保持一致）。
- 回退：因无 git，回退靠保留重构前 `manager.py` 副本（实现阶段先备份）。
