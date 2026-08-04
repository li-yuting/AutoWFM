# Task 6 Report — `send_report` 入口 + `build_secondline_msg` 空数据守卫

## 概要

实现 WeChat-notify 特性的 Task 6：为 `collector/notify.py` 追加定时推送入口 `send_report`，并修掉 Task 2 的 `build_secondline_msg` 在无任何数据时返回"仅表头"字符串的缺陷。全程 TDD（先写失败测试 → 实现 → 转绿 → 全量 checkpoint）。

## 改动文件

| 文件 | 改动 |
|------|------|
| `collector/notify.py` | (3a) 在 `build_secondline_msg` 的 `return` 前插入空数据守卫；(3b) 文件末尾追加 `send_report` 函数 |
| `tests/test_notify.py` | 追加 `_seed_full` 辅助 + `test_send_report` + `test_build_secondline_msg_empty`，并在 `main()` 末尾登记这两个测试 |

## 实现细节

### 3a — `build_secondline_msg` 空数据守卫

原函数末尾两行直接 `return _render_secondline(...)`，当 `groups` 为空且 `z12378` 为 `None` 时，`_render_secondline` 仍会产出 `# 当前时间：...    \n` 这种仅含表头的无意义消息。在 `return` 前插入：

```python
    z12378 = latest_snapshot(data_dir, "12378", date_str)
    z12378_seat = latest_snapshot(data_dir, "12378明细", date_str)
    if not groups and not z12378:
        return ""
    return _render_secondline(now_str, groups, z12378, z12378_seat)
```

语义：仅当既无二线分组数据、又无 12378 快照时返回空串（调用方据此跳过推送）；任一存在则照常渲染（12378 段或分组段由 `_render_secondline` 内部 `if z12378:` 控制）。

### 3b — `send_report`（追加于文件末尾）

```python
def send_report(cfg, now=None):
    """定时推送入口:两条 markdown -> 各自 webhook -> 一张截图发两路。窗口由 push_job 挡。"""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.datetime.now(tz)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    date_str = now.strftime("%Y-%m-%d")
    data_dir = cfg["storage"]["dir"]
    wh = cfg["notify"]["webhook"]
    try:
        msg1 = build_firstline_msg(data_dir, now_str, date_str)
        if msg1:
            log.info(_send_md(msg1, wh["main_key"]))
        msg2 = build_secondline_msg(data_dir, now_str, date_str)
        if msg2:
            log.info(_send_md(msg2, wh["secondary_key"]))
        ss = take_screenshot(cfg["notify"]["screenshot_url"])
        if ss:
            log.info(_send_img(ss, wh["main_key"]))
            log.info(_send_img(ss, wh["secondary_key"]))
    except Exception:
        log.exception("send_report 异常")
```

要点：
- `now` 可注入（测试用），缺省取 `datetime.datetime.now(tz)`。
- `now_str` 格式 `%Y-%m-%d %H:%M` 与 `forecast_at` 在 CSV 中的 `时间` 列精确匹配（如 `2026-07-28 11:00`）。
- 两条 markdown 各走各的 webhook key：firstline → `main_key`，secondline → `secondary_key`；空消息跳过。
- 截图成功后同一张图发两路（main + secondary）。
- 外层 `try/except` 兜底 `log.exception("send_report 异常")`，通知失败绝不抛穿 `push_job`。
- **不做窗口判断**——窗口由 Task 7 的 `push_job` 在调用前挡；`send_report` 无条件执行。
- 不 import `dashboard`；无顶层 `collector.scheduler` import（`check_alerts` 内的惰性 import 是 Task 5 既有内容，不在本次改动范围）。
- `datetime`/`ZoneInfo` 已在 notify.py 顶部导入，无需新增 import。

## TDD 证据

### RED（实现前）

命令：
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_send_report, test_build_secondline_msg_empty; test_send_report(); test_build_secondline_msg_empty()"
```
输出（`test_send_report` 先挂）：
```
AttributeError: module 'collector.notify' has no attribute 'send_report'
```
单独跑 `test_build_secondline_msg_empty` 的输出：
```
AssertionError
```
（`build_secondline_msg` 返回仅表头字符串 `# 当前时间：2026-07-28 11:15    \n`，不等于 `""`。）两处失败均与 brief 预期一致。

### GREEN（实现后）

同一条命令，输出：
```
send_report OK
build_secondline_msg_empty OK
```

### Checkpoint（全量）

命令：
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
输出：
```
webhook errcode=93000: bad
截图失败: no browser
latest_snapshot OK
forecast_at OK
render_firstline OK
render_secondline OK
take_screenshot_failure OK
check_alerts_hotline OK
check_alerts_online OK
check_alerts_12378 OK
check_alerts_12378_window OK
send_report OK
build_secondline_msg_empty OK
ALL notify tests OK
```
共 18 个测试全绿（`webhook errcode=93000` / `截图失败` 是既有失败模式测试的预期日志，非错误）。

## 自审

- 守卫条件 `not groups and not z12378`：分组空但 12378 有数据 → 照常渲染 12378 段；12378 无数据但分组有数据 → 照常渲染分组段；两者皆空 → 返回 `""`。符合"不产出仅表头消息"的目标，且不改变既有有数据时的输出。
- `send_report` 的 md 调用顺序为 firstline(MAIN) → secondline(SECOND)，与测试 `md_calls[0]` 含 `统计监控\`热线\``、`md_calls[1]` 含 `统计监控\`12378\`` 一致；img 调用顺序 MAIN → SECOND，与 `[c[1] for c in img_calls] == ["MAIN","SECOND"]` 一致。
- `forecast_at(d,"热线","2026-07-28 11:00")` 命中 CSV `累计预估量=1187`，故 firstline 含 `>预测量: 1187, 转人工量：1108`，断言通过。
- 全部改动为"追加 + 一处守卫插入"，未触碰其他 Task 的函数体。`storage.SCHEMAS` 与 `_seed_full` 注入列名逐项核对一致。

## 关注点

- `send_report` 依赖 `cfg["notify"]["screenshot_url"]` 与 `cfg["notify"]["webhook"]` 的 `main_key`/`secondary_key`，Task 7 的 `push_job` 配置须保证这些键存在（与 `_cfg` 测试桩结构一致）。
- `take_screenshot` 失败返回 `None` 时仅跳过图片推送、不阻断；markdown 已发出。这是有意行为（截图非关键路径）。
- 截图当前固定写入 `data/screenshot.png`（`take_screenshot` 既有实现），并发场景下若多进程同时推送会覆盖；当前架构为单 `push_job`，暂无问题。
- 未提交 git（按约束不 commit）。
