# AutoWFM 项目代码审查报告

> 审查日期：2026-08-12 ｜ 模式：Plan（只读，未执行任何修改）
> 审查范围：collector / dashboard / api / manager 全部子系统
> 方法：两个 Explore agent 深度审查 + 人工核实所有 P0/P1 发现

共发现 **24 个真实 bug/缺陷**，按严重程度分级如下。每条均已核实触发条件真实存在。

---

## P0 阻断（必须立即修）

### #1 manager.py:644 `_show_alert` 引用未定义变量 `now`（告警机制失效 + UI 冻结）

- **位置**：`manager.py` 第 644 行
- **代码**：`tk.Label(popup, text=f"{message}\n\n时间: {now:%Y-%m-%d %H:%M:%S}", ...)`
- **触发**：任一任务触发告警（如采集器连续 3 次崩溃重启）。`now` 变量仅存在于 `_refresh` 的局部作用域，`_show_alert` 内未定义。
- **影响**：`popup.grab_set()`（642 行）已执行 → 弹出空白模态窗口、无"知道了"按钮、无法关闭；`NameError` 被 `_refresh` 的 try 吞掉继续轮询，但模态弹窗冻结整个 UI。**3 次失败告警机制彻底失效且锁死界面**。
- **修复方向**：`_show_alert` 内改用 `dt.datetime.now().astimezone()`，或把 `now` 作为参数传入。

---

## P1 严重（高频触发，影响数据正确性/可用性）

### #2 scheduler.py:66-96 缺口计数器首轮成功永不重置

- **位置**：`collector/scheduler.py` 第 66-96 行
- **触发**：某源首轮采集成功（line 76），不调用 `_track_gap(s["name"], True, cfg)`；只有补采成功（line 92）才调用。
- **影响**：历史失败计数器永不归零，`_GAP_ALERTED` 永不清除。一旦触发过缺口告警，后续任何失败都因 `name in _GAP_ALERTED` 而不再告警。
- **修复**：首轮成功时也调用 `_track_gap(s["name"], True, cfg)`。

### #3 notify.py check_alerts 无去重/节流（告警轰炸）

- **位置**：`collector/notify.py` `check_alerts`（被 `scheduler.py:100` 每个 ws_job 周期调用）
- **触发**：排队量持续超阈值时，每 5 分钟发一条企微 text 告警（每小时 12 条）。
- **影响**：告警刷屏，掩盖真实问题；企微群被噪音淹没。
- **修复**：维护模块级 `_ALERTED` 集合，仅在状态变化（未超阈→超阈）时发送，恢复时清零。

### #4 backfill.py:142,157-161 当天回填 cutoff 早于窗口起时刻时清空数据不写入

- **位置**：`collector/backfill.py` 第 142、157-161 行
- **触发**：`now=datetime.now()`（凌晨/清晨）；`day == today_str` 且 `now` 早于 `win_start`（09:00）时，`cutoff_slot < start_slot`，`build_snapshots` 返回空 `rows=[]`。但 `clear_day`（line 159）仍执行。
- **影响**：当天已有数据被删除，无新行写入，数据丢失。manager.py 调用未传 `now`，会触发此路径。
- **修复**：`rows` 为空时跳过 `clear_day`；或 `cutoff <= win_start` 时不处理当天。

### #5 _utils.py:49-51 sub 仅配 weekday 无 weekend 时周末 KeyError

- **位置**：`collector/_utils.py` 第 49-51 行
- **代码**：`if sch and ("weekday" in sch or "weekend" in sch): w = sch["weekday"] if now.weekday() < 5 else sch["weekend"]`
- **触发**：sub 只配了 `weekday`，周末访问 `sch["weekend"]` 直接 KeyError，ws_job 中 `in_window` 异常导致整个周期失败。
- **影响**：该源周末全天采集失败。
- **修复**：条件改为 `and`，或 `sch.get("weekend", sch["weekday"])`。

### #6 queries.py:400 空数据时 `_table_header(in_rows[0].keys())` IndexError

- **位置**：`dashboard/queries.py` 第 400 行
- **触发**：查看完全无采集的日期（节假日/未来日），所有 `hourly_*` 返回 `{}` → `_src_hours` 空 → `table_hours=[]` → `in_rows=[]` → `in_rows[0]` 崩溃。
- **影响**：`build_day` 抛异常，看板/API 返回 500。
- **修复**：`in_rows` 为空时返回占位 headers。

### #7 queries.py:235 `inc_im_zrg[h] - inc_im_fail[h]` TypeError

- **位置**：`dashboard/queries.py` 第 235 行
- **代码**：`inc_im_succ = {h: (inc_im_zrg[h] - inc_im_fail[h]) if inc_im_zrg[h] is not None else None ...}`
- **触发**：在线快照中 `转人工失败` 列为 NULL（`inc_im_fail[h]` 为 None）而 `转人工量` 有值时，`int - None` 抛 TypeError。guard 仅判 `inc_im_zrg[h] is not None`。
- **影响**：`build_day` 崩溃，看板 500。
- **修复**：fail 为 None 时按 0 处理，或整体返回 None。

### #8 queries.py:330-340 card 用合并 `cur` 取热线/在线值（落后组显示 0）

- **位置**：`dashboard/queries.py` card 构建段
- **触发**：`cur = max(rx∪im∪z 小时)`；采集器不同步时（如在线已采到 15:05 产生 hour=15，热线仍停在 14:55），`rx.get(cur)` 为 None → `rx_zrg=0`、`rx_cum=0`。12378 已用 `z_cur=max(z)` 独立取值，热线/在线未做同样处理。
- **影响**：落后组的卡片转人工量/时段预测量显示 0，流入率 None，全天累计卡片偏小。
- **修复**：每组用各自 `max(hourly)` 取值，与 12378 一致。

### #9 manager.py:339-376 external_pid 进程无健康检查（僵尸显示）

- **位置**：`manager.py` 第 339-376 行
- **触发**：`_find_external_pid` 接管外部进程后 `self.process=None`、`self.external_pid=pid`；tick 中 `if self.process is not None`（False）→ `elif self.external_pid is None`（False）→ **两个分支都不执行**。
- **影响**：外部进程死亡后 `external_pid` 陈旧，UI 永远显示"运行中(外部)"，不重启、不告警；自动启动也不触发。仅手动停止再启动可恢复。
- **修复**：周期性探活 external_pid（`os.kill(pid,0)` 或 tasklist 查询），死亡则清空 `external_pid` 并走重启逻辑。

---

## 安全（P1 级）

### #10 dashboard/app.py:105 默认 debug=True + 0.0.0.0 + Werkzeug debugger RCE

- **位置**：`dashboard/app.py` 第 105 行
- **代码**：`app.run(host="0.0.0.0", port=8080, debug=os.environ.get("AUTOWFM_DEBUG", "1") == "1")`
- **触发**：直接 `python -m dashboard.app` 启动（未经 manager），`AUTOWFM_DEBUG` 默认 "1"=True，绑 0.0.0.0:8080；若 `AUTOWFM_DASH_TOKEN` 未设则 before_request 放行。
- **影响**：网络可达时 Werkzeug `/console` 可执行任意代码（RCE）。manager 启动会设 `AUTOWFM_DEBUG=0`，但直接启动路径不安全。
- **修复**：默认 `debug=False`；或默认绑 127.0.0.1；debug 模式强制要求 token。

---

## P2 一般（影响稳定性/边界场景）

| # | 位置 | 问题 | 修复方向 |
|---|------|------|----------|
| 11 | repository.py:54 / notify.py:26 | SQLite 无 `busy_timeout`，并发读写 "database is locked" | `sqlite3.connect(path, timeout=30)` |
| 12 | ws.py:11-12 | `REASON_MAP` 无任何 reason 映射到"回访"，回访人数被算入小休 | 补全映射（如 `"callback":"回访"`） |
| 13 | forecast.py:159 | 空历史时 `pd.date_range(NaT)` 崩溃，`run_forecast`/`main()` 未捕获 | 入口校验 `history.empty` |
| 14 | detail.py:33-34 | `channel_column`/`group_column` 不在 DataFrame 时 KeyError | 访问前校验列存在 |
| 15 | _utils.py:62 | `load_dotenv()` 无路径，CWD 非项目根时 .env 不加载，密钥静默为空 | `load_dotenv(Path(__file__).resolve().parent.parent / ".env")` |
| 16 | main.py:37 | `basicConfig` 若 root logger 已有 handler 则 no-op，JsonFormatter 不生效 | 加 `force=True` |
| 17 | notify.py:192 | `networkidle` 对长连接看板永不 settle，30s 超时后截图恒返回 None | 改 `domcontentloaded` + `wait_for_selector` |
| 18 | scheduler.py:84 | `time.sleep(delay)` 串行补采 7 源，叠加首轮可能 > 60s misfire_grace | 补采也用 `pool.submit` 并行 |
| 19 | queries.py:242/246/249/252 | 求和 guard 不一致，guard 列有值而其他子列 None 时返回部分和 | 任一子列 None 则整体 None |
| 20 | manager.py:395 | 排班 `match_key="app.py"` 过宽，匹配任意跑 app.py 的 python 进程 | 用 `shift\\app.py` 全路径片段 |
| 21 | api/app.py:65 | API 绑 0.0.0.0:8081，token 未设则无认证 | 默认绑 127.0.0.1 |
| 22 | queries.py `_inc_d` | 末小时 h=20 依赖 `first[21]` 闭合，若 21:00 快照缺失则漏算 20:55-21:00 | 确保 21:00 末采或 window_end 闭区间 |
| 23 | api/app.py | 无全局异常处理，`queries.build_day` 抛错直接 500 + 明文 traceback 泄露 | 加 `@app.exception_handler` |

---

## P3 改进（可选优化）

| # | 位置 | 问题 | 修复方向 |
|---|------|------|----------|
| 24a | detail.py:60 / backfill.py:123 | `requests` 响应未 `close()`，高并发连接池可能耗尽 | `with requests.post(...) as resp:` |
| 24b | backfill.py:76 | `pd.to_datetime(errors="coerce")` 格式不匹配时静默全零 | 校验 `ts.isna().all()` 时 raise |
| 24c | forecast.py:50-52 | `forecast_at` 失败统一返回 0，与"无预测"不可区分 | 返回 None，渲染时显示 N/A |
| 24d | queries.py:69/95 | `hourly_avg`/`daily_avg` 用 `r[c] or 0` 把 NULL 当 0 参与均值 | 跳过 None |

---

## 建议执行顺序

1. **第一批（P0 + 安全 + 高频 P1）**：#1、#10、#6、#7、#5、#9 — 修完即消除"界面冻结/RCE/看板 500/周末采集失败/僵尸进程"。
2. **第二批（数据正确性 P1）**：#2、#3、#4、#8 — 数据/告警准确性。
3. **第三批（P2 稳定性）**：#11-#23 — 边界与并发。
4. **第四批（P3 优化）**：#24 — 可选。

每批修完跑一遍 `Get-ChildItem tests\test_*.py | ForEach-Object { python $_.FullName }` 回归。修改遵循项目约定：纯 assert 测试、增量提交、不动数据契约。
