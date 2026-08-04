# 2026-08-01 数据补全（会话记录 / 工单明细 5 分钟颗粒度回填）设计

## 1. 背景与目标

项目有 9 个 SQLite 数据源，其中「会话记录」和「工单明细」由 collector 的 `detail_job` 每 5 分钟下载当天 Excel 明细、`count_groups` 按组累计计数后写入（5 分钟颗粒度累计快照）。历史缺失日期需离线回填。

现状：根目录 `backfill.py` 是独立回填工具，但只生成**小时颗粒度**快照（24 个整点累计 + 23:59 总计，25 行/天），且只能命令行运行。

目标：
1. 把回填颗粒度从小时改为 **5 分钟**，与实时采集器一致。
2. 把回填能力集成进 `manager.py` 桌面控制器，支持手动输入日期/范围、点按钮补全。
3. 复用已验证的回填口径（按时间列分桶、累计快照、dashboard 方案D 兼容），不重写算法。

## 2. 关键决策（经 brainstorming 确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 集成方式 | 方案 B：核心逻辑沉入 `collector/backfill.py` | 分层清晰（写库归 collector），脚本与 UI 共用单一实现不分叉 |
| 覆盖策略 | 总是重新覆盖 | 用户指定；每天 download 成功后 `clear_day` 再写 |
| 含今天 | 允许 | 用户指定；进度框提示与采集器并发的时序 |
| 颗粒度 | 5 分钟 | 用户指定，与实时采集器一致 |
| 刻度范围 | 仅业务窗口 09:00-21:00 + 23:59 | 用户指定；工单明细/会话记录实时也 9:00 起写，不破坏 12378 的 8 点展示（其 8 点转人工量来自 12378.db 而非工单明细） |
| 列名 | 工单明细=`创建日期`，会话记录=`开始时间` | 沿用 backfill.py 已验证值（已补 07-01~07-23） |

## 3. 模块结构

```
collector/backfill.py   新建：函数式回填 API（从根目录 backfill.py 搬移 + 改造，去 global/去 print）
backfill.py             改薄 CLI：from collector.backfill import ...，用法不变
manager.py              新增「数据补全」Notebook 页
tests/test_backfill.py  新建：纯函数测试
CLAUDE.md               更新文档
```

## 4. collector/backfill.py API

去 global（`CFG_PATH`/`DATA_DIR`/`DEFAULT_START`/`DEFAULT_END`）、去 print，函数式签名：

```python
TIME_COL = {
    "工单明细": ("创建日期", "%Y-%m-%d %H:%M:%S"),
    "会话记录": ("开始时间", "%H:%M:%S"),
}

def download_day(mcfg, secrets, day, timeout=60) -> df
    # 复用 collector.detail._parse_excel；按 day 设 date_fields；POST 下载返回原始 df

def build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end) -> (rows, total)
    # 5 分钟分桶，见 §5

def iter_days(start: str, end: str) -> list[str]
    # 生成日期列表 YYYY-MM-DD，含首尾；start==end 返回单日

def day_row_count(source, day, data_dir) -> int
def clear_day(source, day, data_dir) -> None

def backfill_source(source, cfg, days, data_dir, overwrite=True, progress_cb=None) -> dict
    # 返回 {"成功": int, "失败": int, "失败日期": [str]}
```

`backfill_source` 逻辑：
```
mcfg = cfg["detail_modes"][source]
secrets = cfg["secrets"]
fcfg = mcfg["filter"]; groups = fcfg["groups"]
time_col, fmt = TIME_COL[source]
win_start = cfg["schedule"]["window_start"]   # "09:00"
win_end   = cfg["schedule"]["window_end"]     # "21:04"
for day in days:
    if not overwrite and day_row_count(source, day, data_dir) > 0:
        progress_cb(f"{source} {day}: 已有数据，跳过"); continue
    try:
        df = download_day(mcfg, secrets, day)
    except Exception as e:
        progress_cb(f"{source} {day}: 下载失败 {e}")
        记录失败; continue                     # 不 break，UI 友好
    rows, total = build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end)
    clear_day(source, day, data_dir)           # overwrite=True 时总是先清
    for vals in rows:
        storage.insert(source, vals, data_dir)
    progress_cb(f"{source} {day}: 写入 {len(rows)} 行 | " + " ".join(f"{g}={total[g]}" for g in groups))
    time.sleep(2)                              # 避免请求过快，保留
返回汇总
```

注：`clear_day` 在 download 成功后才执行（download 失败不清，安全）。

## 5. build_snapshots 5 分钟分桶逻辑

输入：df（当天原始明细）、day、fcfg（filter 配置）、groups、time_col/fmt、win_start/win_end。

步骤：
1. 按 fcfg 过滤：`channel_column`（会话记录有：渠道来源 ∈ channels）、`group_column`（处理组别/接收组 ∈ groups）。与 `detail.count_groups` 同口径。
2. 时间列转 minute_of_day：`ts = pd.to_datetime(d[time_col], format=fmt, errors='coerce')`；`mods = ts.dt.hour*60 + ts.dt.minute`（会话记录"开始时间"=`%H:%M:%S`，pd.to_datetime 得 1900-01-01+时间，`dt.hour`/`dt.minute` 可用）。
3. 全天分桶：`bucket[slot] = {g: count}` for slot in 0..287（5 分钟一刻，`slot = mods // 5`）。
4. 累计：`cum[slot] = sum(bucket[0..slot-1])` = 时间列 < slot*5 的累计。
5. 全天总计：`total = d[group_column].value_counts()`（全量，含时间列缺失记录，与 count_groups 口径一致）。
6. 写业务窗口刻度：win_start -> `start_slot`（09:00 = 108），win_end -> `end_slot = floor(win_end_minute / 5)`（21:04 -> 252 = 21:00）。写 slot in `[start_slot, end_slot]` 的 cum 行：`时间 = f"{day} {hh:02d}:{mm:02d}"`（hh,mm = divmod(slot*5, 60)）。
7. 写 23:59 全天总计行：`时间 = f"{day} 23:59"`，值 = total。

输出：rows（窗口刻度 + 23:59，共 `end_slot - start_slot + 1 + 1` 行）。

窗口刻度数：09:00..21:00 = 252-108+1 = 145 行 + 23:59 = **146 行/天**。

语义：`cum[slot]` = 该 5 分钟刻度之前的累计计数器，与实时采集器每 5 分钟写的"当天截至此刻累计"对齐。dashboard 方案D：`first[H]=H:00` 快照、`latest[H]=H:55` 快照、`inc[H]=first[H+1]-first[H]`，在 5 分钟颗粒度下照常工作，且历史日期也有了小时内 5 分钟进度（小时颗粒度时 `latest[H]=first[H]` 无进度）。

含 09:00 起点：实时采集器因 half-open 窗口 `(9:00, 21:04]` 首采 09:05；backfill 写 09:00（`cum[09:00]=<09:00`），`first[9]=09:00`，`inc[9]` 含 9:00-9:05，比实时更准（backfill 按创建时间重建的固有优势）。

## 6. 根目录 backfill.py 薄 CLI

```python
from collector.backfill import backfill_source, iter_days, TIME_COL
# load_cfg 保留在此（读 config.yaml -> detail_modes, secrets, schedule）
# main(): 解析 argv，调 backfill_source(source, cfg, days, data_dir, overwrite=False, progress_cb=print)
```

用法不变：`python backfill.py [会话记录|工单明细 [YYYY-MM-DD]]`，无参默认补 07-01~07-23（`DEFAULT_START`/`DEFAULT_END` 留 CLI）。行为：`overwrite=False`（保留原幂等跳过）、`progress_cb=print`、失败 continue（比原 break 更鲁棒；幂等 + 断点续跑仍成立：失败的天下次重试）。

## 7. manager「数据补全」页

### 7.1 UI（仿 `_build_forecast_page`）

`_build_ui` Notebook 新增：
```python
bf_page = tk.Frame(nb)
nb.add(bf_page, text="数据补全")
self._build_backfill_page(bf_page)
```

`_build_backfill_page(page)`：
- top Frame：
  - `Label "开始日期"` + `Entry`（YYYY-MM-DD）
  - `Label "结束日期"` + `Entry`（留空 = 单日 = 开始）
  - `Checkbutton "会话记录"`（默认勾选）+ `Checkbutton "工单明细"`（默认勾选）
  - `Button "开始补全"` command=`_run_backfill`
  - `Label` status（就绪/运行中.../完成/失败）
- `ScrolledText` 进度框（DISABLED，逐日追加）
- 初始提示文本

实例属性：`self._backfill_running = False`（去重锁）。

### 7.2 运行（仿 `_run_forecast`）

`_run_backfill`：
1. 校验：开始日期解析为 date；结束日期留空则 = 开始；开始 > 结束提示；至少勾一个源。若范围含今天，进度框起始追加提示："含今天；若采集器在跑，今天的整点快照与采集器 5 分钟快照混合（口径一致，不丢数据），建议先停采集器再补今天以避免 clear 与采集器 insert 的时序交叉"。
2. 设 status "运行中..."、禁用按钮、清空进度框。
3. 起 daemon Thread `worker`：
   - `from collector import backfill`
   - `days = backfill.iter_days(start, end)`
   - 对每个勾选源：`res = backfill.backfill_source(source, self.cfg, days, self.cfg["storage"]["dir"], overwrite=True, progress_cb=cb)`
   - `cb(text)`：`self.root.after(0, self._append_backfill_text, text)` 回主线程追加（不直接碰 Tk）
   - 汇总各源 res，`root.after(0, self._on_backfill_done, summary, None)`
   - 异常：`root.after(0, self._on_backfill_done, "", exc)`
4. `_append_backfill_text(text)`：进度框追加 `text + "\n"`。
5. `_on_backfill_done(summary, err)`：恢复按钮、设 status、追加汇总或错误（建议查 `logs/manager.log`）。

## 8. 行为规则

- **总是覆盖**：`overwrite=True`，每天 download 成功后 `clear_day` 再写。
- **含今天**：不阻止，进度框起始提示并发（见 7.2）。
- **失败**：某天下载失败 -> progress_cb 报告 + continue 下一天；汇总统计失败天数与日期。
- **进度**：逐日逐源报告。
- **并发安全**：`clear_day` 删 `WHERE 时间 LIKE day%`（仅当天），不影响其他日期；与采集器写今天仅当范围含今天时交叉，提示用户。

## 9. 测试

`tests/test_backfill.py`（plain assert，bootstrap sys.path，仿 `tests/test_*.py`）：
- `build_snapshots`：构造小 DataFrame（时间列 + 组列 + 渠道列），验证：
  - 窗口刻度行数 = `end_slot - start_slot + 1 + 1`（含 23:59）
  - `cum[slot]` = 时间列 < slot*5 的累计（单调递增）
  - 23:59 行 = 全量（含时间列缺失记录）
  - channel 过滤生效（会话记录）
- `iter_days`：同日、跨月、含首尾边界。
- `day_row_count`/`clear_day`：临时 db 写入 + 清除验证。
- 网络相关（`download_day`/`backfill_source`）不测。manager UI 不测（同预测页策略）。

## 10. 文档更新（CLAUDE.md）

- 根目录 `backfill.py` 描述改为"薄 CLI，调用 collector.backfill"。
- 新增 `collector/backfill.py` 条目（函数式回填 API，5 分钟颗粒度，业务窗口 09:00-21:00 + 23:59）。
- manager 段新增「数据补全页」描述。
- 命令参考表不变（`python backfill.py` 用法不变）。
- 「Known behaviors」段：历史数据由 backfill 5 分钟颗粒度补全的说明（替换原"小时分桶"描述）。

## 11. 非目标（YAGNI）

- 不做颗粒度参数化（5 分钟/小时可切换）：用户只要 5 分钟。
- 不做窗口按 source 区分：工单明细/会话记录都用全局窗口 9:00-21:00。
- 不做"强制覆盖"单独勾选：已统一为总是覆盖。
- 不自动重补 07-01~07-23：用户可用新功能手动跑一次范围覆盖为 5 分钟。
- 不补其他 7 个 WS 源：本次只补会话记录/工单明细两个 detail 源。
