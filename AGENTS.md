# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 快速命令参考

| 用途 | 命令 |
|------|------|
| 启动采集器（常驻） | `python -m collector.main` |
| 手工跑一次全量预测 | `python -m collector.forecast` |
| 启动看板 | `python -m dashboard.app` |
| 启动桌面管理器 | `.\.venv\Scripts\python.exe manager.py` |
| 一次性冒烟测试 | `python tests/smoke.py` |
| 运行单个测试 | `python tests/test_xxx.py` |
| 运行全部测试 | `Get-ChildItem tests\test_*.py \| ForEach-Object { python $_.FullName }` |

## Environment & Commands

Windows + PowerShell. Python 3.14 lives in `.venv` - always use its interpreter, never system Python:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING="utf-8"   # required before any output of Chinese

# Run the long-running collector (per-source windows, every 5 min, blocks)
python -m collector.main        # MUST use -m (same sys.path reason as dashboard below)

# One-shot: full 30-day 进线量预测 CSV to output/ (also auto-runs 21:05 via forecast_job)
python -m collector.forecast

# Run the dashboard (read-only Flask viewer over data/*.db) - MUST use -m,
# because app.py does `from dashboard import ...` and `python dashboard/app.py`
# puts dashboard/ (not the project root) on sys.path -> ModuleNotFoundError.
# Binds 0.0.0.0:8080; debug defaults ON. manager.py sets AUTOWFM_DEBUG=0 to kill
# the Flask reloader (single process -> clean crash-detect/stop).
python -m dashboard.app          # http://127.0.0.1:8080, auto-refresh 5 min

# Optional: desktop supervisor (Tkinter + tray) that runs collector + dashboard
# as supervised child processes. NOT -m (root script); pythonw.exe = no console.
.\.venv\Scripts\python.exe manager.py

# One-shot live smoke: hits all 7 WS + 2 requests, writes one row per db
python tests/smoke.py

# Tests - plain assert, NO pytest. Run each file directly:
python tests/test_storage.py
python tests/test_ws.py
python tests/test_detail.py
python tests/test_guard.py
python tests/test_notify.py
python tests/test_backfill.py
python tests/test_manager.py
python tests/test_dashboard_queries.py
python tests/test_dashboard_app.py

# All tests in one shot (no pytest):
Get-ChildItem tests\test_*.py | ForEach-Object { python $_.FullName }
```

- No build step. Git is initialized (`.git/`); `config.yaml`、`*密钥.yaml`、`.env`、`data/*.db`、`logs/`、`output/`、`.venv/` 均被 `.gitignore` 忽略。
- **密钥管理(Phase 1)**: token/tenementId/webhook key/dash_token 明文已从 `config.yaml` 移除(置空),改由 `.env` 文件(被 git 忽略)经 `python-dotenv` 注入。`collector/_utils.py load_cfg()` 调 `load_dotenv()` 后用 `AUTOWFM_*` 环境变量覆盖 cfg 的空占位。`.env.example` 为入库模板。
- **看板认证(Phase 1)**: `dashboard/app.py` `before_request` 校验 `Authorization: Bearer <AUTOWFM_DASH_TOKEN>`;token 留空则不启用(本地开发兼容)。`/health` 端点免认证返回 `{"status":"ok"}`。`notify.take_screenshot` 截图时带 Authorization header(`dash_token` 从 cfg 注入)。
- **结构化日志(Phase 1)**: `collector/main.py` 用 `JsonFormatter` 输出每行一个 JSON 对象 `{ts,level,logger,message,exc_info?}`;apscheduler 压噪逻辑不变。
- **CI(Phase 1)**: `.github/workflows/ci.yml` 在 Ubuntu + Python 3.14 串行跑 `tests/test_*.py`(无 pytest);`smoke.py` 需 WS 网络不纳入 CI。
- Tests bootstrap `sys.path` to the project root themselves, so run them from anywhere via `python tests/<file>.py`.
- `writeforecast/` 是一个独立工具，将周度预测 Excel（`writeforecast/data/量级预估*.xlsx`）转为 `data/预估流入量.csv` 的 15 分钟粒度时段预估量。运行 `python writeforecast/writeforecast.py`，不依赖 AutoWFM 其他模块。`writeforecast/时段人力数架构准备_v2.py` 是另一个独立脚本（时段人力数准备），也不属于主系统。
- 工单明细/会话记录 历史回填走 manager.py「数据补全」页（底层 `collector.backfill`，`overwrite=True` 覆盖、失败 continue）；无独立 CLI。

## Architecture

Two cohabiting subsystems share `data/` but run as **separate processes**: a collector that writes, and a dashboard that only reads. An optional desktop supervisor (`manager.py`) can run both as supervised child processes - it touches no `data/` itself, only starts/stops/restarts the two.

### Collector (writes data/*.db)

A long-running scheduler collects 7 WebSocket monitoring sources and 2 requests detail exports every 5 minutes, each source during its own window, storing each into its own SQLite db. The big picture spans several files (all under `collector/`):

**Two independent scheduled jobs** (`collector/scheduler.py`): `ws_job` (ThreadPool 7) and `detail_job` (ThreadPool 2) run on the same `IntervalTrigger` but are separate APScheduler jobs with `max_instances=1` + `coalesce=True`. They're deliberately split so a slow requests download can't block WS collection. Windows are **per-source**: `_in_window(cfg, sub, now)` uses a sub's own `schedule` if present (weekday/weekend `start`/`end`, half-open `(start, end]`, picked by `weekday() < 5`), else falls back to the global `window_start`/`window_end` (config.yaml: `(9:00, 21:04]` daily - the `21:04` end keeps the 21:00 snapshot in-window, excludes 21:05). `ws_job` collects only subs in-window at that tick; `detail_job` uses the global window. The `12378` + `12378明细` subs override with weekday `(8:30, 21:00]` / weekend `(9:00, 18:00]`; other sources stay on the global window. The trigger fires 24/7 every 5 min (`start_date` anchored 08:35, so ticks land on `:00/:05/…/:55` marks; first in-window tick is 09:05) - out-of-window subs are skipped, not the whole tick.

**WS collection is per-cycle, not persistent** (`collector/ws.py`): each cycle does connect -> send subscribe cmd -> recv the first frame where `screen` matches and `data` is non-empty -> extract -> close. No auth needed (IP-whitelisted). This is a deliberate choice for 5-min cadence; do not "fix" it to persistent connections unless cadence drops to ≤1 min (then port v14's `Sub` pattern from `D:\PythonProject\hfqwfm\everyday\hfq_spider_v14.py`).

**Extraction ↔ storage contract** (`collector/ws.py` ↔ `collector/storage.py`): each extractor (`_extract_statics` / `_extract_seat(skill)` / `_extract_im`) returns a flat dict whose keys **must exactly match** `storage.SCHEMAS[source]` minus `时间`. If you add a metric, update both the extractor and the SCHEMA entry, or the insert will KeyError. Column names are Chinese (SQLite stores them UTF-8, quoted).

**9 independent SQLite dbs** (`collector/storage.py` + `data/`): one `<源名>.db` per source, one table `t` each, 5-min time series. `insert()` opens/closes a connection per call - safe because each source is written by exactly one thread, so the 9 dbs never contend. Do not collapse into one db without considering the write-lock serialization.

**requests detail** (`collector/detail.py`): POSTs to the export API with today's date, detects xlsx/xls by magic bytes, parses with `pd.read_excel(header=2)` (first 2 rows blank, 3rd is the Chinese header), then `count_groups` filters + groupbys per the config's `filter` rules. `token`/`tenementId` are injected at runtime from `secrets` (the per-mode `data` has them as `""`).

**Config-driven** (`config.yaml`): all 7 `subs`, 2 `detail_modes` (with full `data` dicts), filter rules, secrets, timeouts, window. SEAT subs merge a `seat_data` YAML anchor (`<<: *seat_data`) - PyYAML resolves it at load. The subscribe message is `{"cmd":1, "screen":..., "data":<sub.data>}`. A sub's optional `schedule` (weekday/weekend) overrides the global window.

**进线量预测 + 次日差异告警** (`collector/forecast.py`): 集成自 `D:\PythonProject\进线量预估\forecast.py`。`run_forecast(days=…)` 读 `热线.db`/`在线.db` 每日 21:00 累计转人工量 -> OLS(`log_v ~ C(weekday)+C(collection)+C(post_mkt)`) 锚定预测未来 N 天。`check_next_day_diff(cfg)` 取**次日**预测转人工量，对比 `data/预估流入量.csv` 次日该线路"累计预估量"最大值(=全天预估总量)，相对差 `|forecast-csv|/csv` 超 `forecast.diff_threshold`(默认 0.10) 则发企微 text 告警走 `webhook.main_key`、@ `forecast.alert_recipient`(默认仅 17629050914)。**该功能由 manager.py 在 21:05 自动触发，不依赖采集器进程。**`python -m collector.forecast` 可手动跑全量 CSV 留档到 `output/`；`run_forecast(days, write_csv)` 供 manager.py 手动触发。节假日文件 `holidays.txt` 在项目根。依赖 statsmodels(numpy/pandas 已装)。

**collector/notify.py** pushes WeChat Work messages + alerts. `send_report(cfg)` — called by `push_job`, a `CronTrigger(minute="0,15,30,45")` job gated by the global window — first `push_job` waits for the same-tick `ws_job`+`detail_job` to finish (`_wait_cycle` in `scheduler.py` polls module-level `_last_ws_cycle`/`_last_detail_cycle`, each set to its `now_str` at job end; timeout `notify.push_wait_timeout` default 30s then degrades to last cycle - fixes the 5-min-stale race where push ticks coincide with ws ticks), then builds two markdown reports from latest db snapshots + `预估流入量.csv` forecast (一线 热线+在线 -> `webhook.main_key`; 二线 常规+贷后+12378 -> `webhook.secondary_key`), then takes one Playwright screenshot of `notify.screenshot_url` (the dashboard) and sends the image to both webhooks. `check_alerts(cfg)` — called from `ws_job` after each 5-min collection — sends a text alert (`@recipients`) when 热线/在线/12378 排队 crosses its threshold (`>=`; 热线/12378 also require 空闲<排队; 12378 is gated by its own `schedule` window to avoid stale weekend-after-18:00 alerts): 热线/在线 -> main_key, 12378 -> secondary_key. All webhook/screenshot failures are caught (never crash the collector). Config is the `notify:` block (webhook keys, alert thresholds/recipients, `screenshot_url`). `notify.py` must not import `dashboard` (collector writes / dashboard reads) nor `collector.scheduler` at top level — `check_alerts` lazily imports `_in_window` inside the function to avoid the circular import. **次日预测量差异对比(`check_next_day_diff`)已移出 collector，由 manager.py 在 21:05 自动触发（见下）。**

**5 分钟颗粒度回填** (`collector/backfill.py`)：函数式 API，被 `manager.py`(数据补全页) 调用。`build_snapshots` 按时间列(工单明细=创建日期, 会话记录=开始时间)的 5 分钟分桶，生成业务窗口(`window_start`~`window_end` -> 刻度 09:00..21:00)每 5 分钟累计快照 + 23:59 全天总计（146 行/天），`cum[slot]=时间列<slot*5 的累计`，与实时采集器 5 分钟快照语义对齐、dashboard 方案D 兼容。`backfill_source(source, cfg, days, data_dir, overwrite=True, progress_cb=None, now=None)` 总是覆盖（download 成功后 `clear_day` 再写）、失败 continue、返回 `{成功,失败,失败日期}`。**补全「当天」时**（`now` 默认当前时间，`day==今天`）`build_snapshots` 传 `cutoff=当前时刻`，只输出 ≤ 当前时刻的刻度、不写未来时段、不写 23:59 全天总计行（避免污染未来数据/全0壳行）；补全过去日期行为不变（整天 146 行）。`TIME_COL`/`SLEEP` 常量。

### Dashboard (reads data/*.db + 预估流入量.csv)

A read-only Flask + Chart.js viewer (pyecharts removed) (`dashboard/`, run via `python -m dashboard.app`) that renders the 9 dbs as a Web 看板. It runs as a **separate process** alongside the collector; it only ever reads, so it never contends with the writer threads (SQLite allows concurrent reads). The page auto-refreshes every 5 min via a `<meta>` tag; each request re-queries live, so newly collected rows appear on next refresh.

**Routes** (`dashboard/app.py`): `/` redirects to today's day view; `/?view=day&date=YYYY-MM-DD` (hourly) and `/?view=month&date=YYYY-MM` (daily). `queries.build_day` / `build_month` assemble a four-part payload (cards, inbound charts, outbound charts, two detail tables) of **raw data**; `app.py` passes it straight to `templates/dashboard.html`, which renders cards/tables server-side (Jinja) and builds the charts **client-side with Chart.js** (`BAR_PALETTE` + `themeColors()` read the same CSS variables as the shell; `buildChart()` mirrors the removed `charts.py`'s semantics). `app.rate_class` highlights 流入率/接通率 cells server-side via `_RATE_THRESHOLDS` (流入率 <90 red / >105 green; 接通率 <92 red / >95 green), exposed as the Jinja filter `rate_class`.

**Day-view metric model** (`queries.py` - the key non-obvious contract). Counters reset overnight, so the day-view charts/tables (Parts 2-4) show **per-hour increments** (data generated in [H, H+1)), not running totals; the card (Part 1) still shows cumulative day-so-far totals. Month view uses daily close (= daily total, since counters reset) so it is already per-day.
- **Cumulative metrics** (转人工量, 接通量/转人工成功量, 转接量, 工单量, 12378回访组): per-hour increment = `end[H] − start[H]` via `_inc_col_d` (方案D). `start[H]`=`first[H]` (H 小时首条快照, `hourly_first`); `end[H]`=`first[H+1]` (下一小时首条 = (H+1):00 快照) 若存在, 否则 `latest[H]` (H 小时最新, 当前小时实时进度). 已完成小时精确覆盖 [H:00,(H+1):00); 当前小时显示整点至今的实时增量 (整点刚采完 first=latest -> 0); `first[H]` 缺失 (缺口/未来小时) -> None. 计数器隔夜归零; 首小时少算开头 (首采非整点, 如热线 9:05, 因窗口 (9:00,21:04] 排除 9:00). 旧 `cum[H]−cum[H-1]` 模型 (`_inc_col`/`_increments`, 已移除) 每行跨小时边界 (含上一小时末 5 分钟、漏自己末 5 分钟), 且整点采完瞬间下一小时显示 5 分钟错位增量 - 2026-07-30 替换为方案D.
- **Instantaneous metrics** (签入, 空闲, 在线): arithmetic mean of the hour's snapshots via `hourly_avg`, **rounded to int**; month view uses `daily_avg`.
- **预测量 (chart)**: per-hour forecast increment - 热线/在线 = `forecast_increment` (H 点 = H:15+H:30+H:45+(H+1):00 四行时段预估量之和; CSV 时间戳为时段结束点 09:15=[09:00,09:15), 故 H:00 行计入 H-1 点, 覆盖 [H:00,H+1:00) 整小时); 12378 = 7-days-ago 转人工量 的方案D增量 (`forecast_12378_first`+`forecast_12378` -> `_inc_d`; 7 天前无数据 -> None).
- **12378** uses the same `_inc_col_d` model as 热线/在线 for 转人工量/转人工成功量. 12378's window starts 8:30 (weekday) / 9:00 (weekend), so a weekday 8点 row appears (`start[8]=first[8]`, the 8:35+ 首采); 12378回访组 (from 工单明细) uses the same model. (历史: 曾用 `:00`-snapshot 模型 `_inc_col_first` 即 `cum_end[H]=first[H+1]`, 2026-07-29 因「当前小时永远为 0」被移除; 2026-07-30 方案D 恢复该整点差值语义, 但当前小时用 `latest[H]` 给实时进度, 不再永远 0.)

**Card (Part 1)** uses **cumulative latest**, not increments. 热线/在线 read at `current_hour` = max data hour across 热线/在线/12378; **12378 reads at its own `z_cur = max(z)`** because its window differs (on a weekend evening the global `current_hour` is past 12378's close, so it must use 12378's own latest hour or it reads 0). Card 预测量 = full-day max cumulative forecast; 时段预测量 = for 热线/在线, cumulative forecast at the latest `:00/:15/:30/:45` CSV slot ≤ that source's latest WS timestamp (`forecast_cum_up_to`, so it tracks 15-min granularity instead of jumping to the current hour's `:45` future value); 12378 时段预测量 still uses `fc_z.get(z_cur)` (no 15-min CSV forecast); 流入率 = 转人工量/时段预测量; 接通率 = 转人工成功量/转人工量.

**Derived metrics** (in `build_day`/`build_month`, not stored): 转人工成功量 = `接通量` for 热线/12378, but = `转人工量 − 转人工失败` for 在线; 接通率 = 转人工成功量/转人工量; 流入率 = 转人工量/时段预测量.

**Forecast sources** (预测量) - three provenances, do not assume one:
- 热线 / 在线: `data/预估流入量.csv` (15-min; cols `时间,线路,时段预估量,累计预估量`; lines `热线`/`在线`; 时间戳为时段结束点 09:15=[09:00,09:15), 最早 09:15 最晚 21:00). `load_forecast` = latest 累计预估量 per hour (card 预测量/时段预测量); `forecast_increment` = H 点 = H:15+H:30+H:45+(H+1):00 四行时段预估量之和 (chart 预测量; H:00 行计入 H-1 点, 覆盖 [H:00,H+1:00) 整小时).
- 12378: **no CSV**. Forecast = **7-days-ago same-hour 转人工量** from `12378.db`. Card (累计) 用 `forecast_12378` / `_forecast_12378_daily` (每小时/每日最新); chart (增量) 用 `forecast_12378_first`+`forecast_12378` 经 `_inc_d` (方案D).
- Outside CSV range (2026-06-01…2026-08-15) or before 7 days of history, 预测量 is 0/None.

**Group → source mapping** is many-to-one and spread across `build_day`/`build_month` (e.g. 常规二线 = 工单明细.回访组一组 + 会话记录.(转接一组+转接二组) + 常规.db seats). Reference `build_day`/`build_month` in `dashboard/queries.py` before remapping a group.

**Layout** (`templates/dashboard.html`; charts built client-side from `data.inbound`/`data.outbound` via `buildChart()`): Part 1 = per-group cards in a flex row (合计 card removed). Part 2 = 3 inbound charts in one flex row; Part 3 = 4 outbound charts in one flex row (each `.chart-cell` `flex:1 1 0`, Chart.js mounts into a 360px `.chart` canvas). Part 4 = two tables (接听/外呼) with a **two-row header** (`_table_header`: row 1 = group `colspan`, row 2 = value name, 小时/日 `rowspan=2`); rows are trimmed to **only collected hours** (`range(8,21)` filtered by which sources have data - uncollected future hours are omitted; an 8点 row appears when 12378 has 8:30+ data). **Theme**: dark-first CSS variables (`:root`) + `[data-theme="light"]` override (toggle button, persisted to localStorage); dimension colors 热线/在线/12378/二线 = blue/cyan/purple/amber (`--c-hotline/--c-online/--c-12378/--c-second`); bar palette 预测量/转人工量/转人工成功量/12378回访组/转接量/工单量 = #3b82f6/#06b6d4/#22c55e/#a855f7/#6366f1/#14b8a6; lines (签入 solid / 空闲 dashed / 在线 solid) on the right axis read `--line-solid`/`--line-dash`. Chart X-axis (`_hours_for`): 12378 weekday 8-20 / weekend 9-17; all others 9-20.

### Manager (optional desktop supervisor, `manager.py`)

A Tkinter GUI + system tray (`pystray`/`PIL`, optional - degrades to a plain window if the deps are missing) that supervises `collector.main` and `dashboard.app` as child `python -m <module>` subprocesses. It does **not** read/write `data/` - it only manages the two processes above. Run directly (not `-m`, it's a root script): `.\.venv\Scripts\python.exe manager.py` (or `pythonw.exe` for no console; an in-UI toggle creates/removes a Startup-folder `.lnk` → `pythonw.exe manager.py` for boot autostart).

- **Auto start/stop** (pure fns `compute_auto_start` / `auto_stop_minutes` / `in_run_window` / `schedule_text`, tested in `tests/test_manager.py`): `compute_auto_start(cfg, now)` = min of all sub window starts (weekday 08:30 / weekend 09:00, driven by 12378); `auto_stop_minutes` = global `window_end` + `manager_stop_buffer`（默认 10 分钟，给 forecast_job 21:05 留执行窗口）。The run window is `[auto_start, auto_stop)` - **closed-open, the opposite boundary convention from the collector's `(start, end]`**. Outside it, running tasks are stopped and failure counters reset (primed for next day).
- **Crash-restart**: `_refresh` ticks every `MONITOR_INTERVAL_MS=5000`. A process that exits within `GRACE_SECONDS=30` of start counts as one failure; `MAX_FAILURES=3` consecutive -> auto-restart paused + a topmost alert popup (`popup_shown` dedups). Surviving the grace period resets the counter.
- **External-PID adoption**: before spawning, `_find_external_pid` (PowerShell `Get-CimInstance Win32_Process` matching the module name) detects a `python.exe` already running the same module and *adopts* it rather than spawning a duplicate - avoids double-binding the dashboard port. `stop()` on an adopted PID uses `taskkill /PID /T /F`.
- **Logs**: collector keeps writing its own `logs/autowfm.log` (`capture_log=False`); the dashboard's stdout is captured to `logs/dashboard.log` (`capture_log=True`, since `dashboard.app` logs to stdout); the manager logs to `logs/manager.log`. The dashboard is launched with `AUTOWFM_DEBUG=0` so Flask's reloader is off (single process for clean crash-detect/stop).
- **排班子项目（第三行任务「排班」）**: `ManagedTask` 支持 `script`/`cwd`/`match_key`/`auto_enabled` 参数，从 `-m module` 扩展为运行任意脚本（`script` 非 None 时 `cmd=[PYTHON, script]`、`cwd` 指向脚本目录）。排班任务通过 `shift_manager.py`（AutoWFM 侧包装脚本）把 `SHIFT_DIR`（`D:\PythonProject\AutoShift`）加入 `sys.path` 后以 `__main__` 执行其 `app.py`，Flask 监听 `127.0.0.1:5000`；受管时（`AUTOWFM_MANAGED=1`）屏蔽 `webbrowser.open` 避免自动弹浏览器。`_find_external_pid` 在脚本模式下按 `match_key`（排班设为 `app.py`）匹配，可接管外部直跑的 AutoShift 进程。排班日志写 `logs/shift.log`。**排班任务 `auto_enabled=False`：仅手动启停，不做自动启停、不做崩溃自动重启，默认不运行**（采集器/看板仍自动启停）。改动均在 AutoWFM 侧，未触碰 AutoShift 原始代码。
- **UI 布局** (`_build_ui`): 四区结构,区间 `ttk.Separator` 分隔--标题栏(标题+每日计划)/常驻状态条/主区/底栏。**状态条**单 Frame 两行 `grid`(`●状态点 任务名 状态 PID 失败 来源 … [启动][停止][重启]`,两行对齐;状态点 `fg` 由 `_update_status` 按状态设色:运行绿 `#16803c`/停止灰 `#666666`/暂停红 `#aa2222`)。**主区** = 左侧 120px 导航栏(`tk.Button`+relief 选中态,`_show_page(idx)` 用 `tkraise` 切换右侧 `grid` 叠放内容页)替代原 `ttk.Notebook`;4 页:采集器日志/看板日志/排班日志/进线量预测/数据补全,默认采集器日志。**底栏**分 3 组(`[刷新日志]`｜`[开机自启][重启控制台]`｜日志路径右对齐)。按钮一律 `tk.Button`(开机自启用 `textvariable`,`ttk.Button` 不支持)。
- **进线量预测页**: 左侧导航「进线量预测」页 = `Spinbox`(默认 7,1-30)+「运行预测」按钮 + 文本摘要。点按钮在后台线程惰性 `from collector import forecast; forecast.run_forecast(days)`(不阻塞 UI,不在启动时加载 statsmodels),摘要(纯函数 `_forecast_summary`:各业务 N 天合计/日均/超界日期 + CSV 路径)显示在只读文本框,结果也写 `output/`。
- **次日预测量差异对比**: 每天 **21:05** 自动触发 `check_next_day_diff`（管理器进程内，不依赖采集器存活）。对比 forecast 次日预测 vs `data/预估流入量.csv` 全天累计预估量，差异超 `forecast.diff_threshold`(10%) 则发企微 @17629050914。结果写入 `manager.log`。
- **数据补全页**: 左侧导航「数据补全」页 = 开始/结束日期 Entry(YYYY-MM-DD，结束留空=单日) + 会话记录/工单明细 Checkbutton(默认都勾) + 「开始补全」按钮 + 进度 ScrolledText。点按钮在后台线程调 `collector.backfill.backfill_source(..., overwrite=True, progress_cb=...)`，逐日进度经 `root.after` 回主线程追加（不直接碰 Tk）。去重锁 `_backfill_running`。含今天时进度框提示并发（建议先停采集器）。汇总显示各源成功/失败数。
- **重启控制台**: 底栏「重启控制台」按钮(带确认)-> 拉起新 manager 进程(`sys.executable manager.py`)、退出当前,**不停**采集器/看板子进程;新 manager 通过 external-PID 接管它们,采集不中断。
- Tk UI 测试(`tests/test_manager.py`):测调度纯函数 + `ManagedTask.tick()` + `_forecast_summary`(静态);另有 2 个**构造冒烟测试** `test_ui_constructs`/`test_update_status_sets_dot`,mock 掉 `_build_tray`/`_refresh`(不起托盘线程、不拉起子进程,headless 安全),验证 `_build_ui` 不崩溃 + 左导航结构 + 状态点接线。视觉外观仍靠人工验证。

## Known behaviors (not bugs)

- **IM seat status**: `seatStatus` is only `free`/`rest`/`notReady`/`offline`. The 话后/就餐/培训/回访 buckets come from `seatRestReason` under `rest`, mapped via `REASON_MAP` in `collector/ws.py` (currently `{"meal":"就餐","training":"培训","arrange":"话后"}`). Unmapped reasons default to 小休 and are logged - extend `REASON_MAP` when a new reason appears in logs.
- **外呼量/外呼接通量 are global** (`hcAnalysisData`, not per-numberType): stored in 热线 only - 12378 dropped these two on 2026-07-24 (redundant). `_extract_statics(keep_hc=False)` for `name=="12378"`, set in `_make_extractor(screen, skill, name)`.
- WS `:7000` connection sometimes pushes other `skillCode` SEAT frames; `_extract_seat` filters by `afterOverTimeStatics[0].agentSplit == skill`.
- The two jobs can take different intervals (e.g. WS 1-min, requests 5-min) by giving them separate triggers - relevant if cadence changes, since requests re-downloads the full day's Excel each cycle.
- **Dashboard data gaps render as "无数据", not errors**: 工单明细.db / 会话记录.db 的 2026-07-01~07-23 历史数据由 `collector.backfill` 按「创建日期/开始时间」**5 分钟**分桶补全（业务窗口 09:00-21:00 每 5 分钟累计快照 + 23:59 总计，146 行/天，单调递增、不受接收组转派影响）；07-24 起为实时 5 分钟快照（按当前接收组 count，转派可能导致某组累计偶发下降，如 07-24 工单明细 19:20->19:25 的 112->105）。12378 forecast needs 7-day-old data, so 07-01…07-07 has no 12378 预测量; CSV forecast covers 2026-06-01…2026-08-15, outside which 热线/在线 have no 预测量.
