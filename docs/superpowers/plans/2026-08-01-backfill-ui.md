# 数据补全（5 分钟颗粒度回填）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把会话记录/工单明细的历史回填从小时颗粒度升级为 5 分钟颗粒度，并集成进 manager.py 桌面控制器供手动触发。

**Architecture:** 把根目录 `backfill.py` 的核心逻辑沉入 `collector/backfill.py`（函数式 API，去 global/去 print），根目录脚本变薄 CLI，manager 新增「数据补全」页调用同一实现。回填按时间列 5 分钟分桶生成业务窗口（09:00-21:00）累计快照 + 23:59 全天总计，与实时采集器 5 分钟快照颗粒度一致、dashboard 方案D 兼容。

**Tech Stack:** Python 3.14（.venv）、pandas、requests、SQLite、Tkinter、PyYAML。

## Global Constraints

- Windows + PowerShell。Python 在 `.venv`，先 `.\.venv\Scripts\Activate.ps1` 再 `$env:PYTHONIOENCODING="utf-8"`（任何中文输出前必设）。
- **项目无 git**：不执行 `git commit`。每个 Task 末尾用「运行测试通过」作为检查点（Checkpoint）替代 commit。
- **测试用 plain assert，无 pytest**：运行 `python tests/test_xxx.py`；测试文件自行 `sys.path.insert` bootstrap 到项目根；`def main()` 包裹断言，末尾 `print("xxx OK")`，`if __name__ == "__main__": main()`。
- SQLite 列名为中文，UTF-8 存储，查询时双引号包裹（如 `"时间"`）。
- 复用现有 `collector.detail._parse_excel`、`collector.storage.insert` / `SCHEMAS`，不改动它们的口径。
- spec：`docs/superpowers/specs/2026-08-01-backfill-ui-design.md`。

---

### Task 1: collector/backfill.py 基础纯函数

**Files:**
- Create: `collector/backfill.py`
- Test: `tests/test_backfill.py`

**Interfaces:**
- Consumes: `collector.storage.insert`、`collector.storage.SCHEMAS`（来自现有模块）
- Produces: `TIME_COL`（dict）、`SLEEP`（int）、`iter_days(start, end) -> list[str]`、`day_row_count(source, day, data_dir) -> int`、`clear_day(source, day, data_dir) -> None`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_backfill.py`：

```python
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile
from pathlib import Path
from collector import backfill, storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_iter_days():
    assert backfill.iter_days("2026-07-15", "2026-07-15") == ["2026-07-15"]
    assert backfill.iter_days("2026-07-15", "2026-07-17") == ["2026-07-15", "2026-07-16", "2026-07-17"]
    assert backfill.iter_days("2026-07-31", "2026-08-02") == ["2026-07-31", "2026-08-01", "2026-08-02"]

def test_day_ops():
    d = tempfile.mkdtemp()
    storage.insert("工单明细", {"时间": "2026-07-15 09:00", "二线客诉处理组": 0,
        "常规工单处理组": 0, "回访组一组": 0, "贷后回访组": 0, "12378回访组": 0,
        "转接一组": 1, "转接二组": 0, "贷后转接组": 0}, d)
    assert backfill.day_row_count("工单明细", "2026-07-15", d) == 1
    assert backfill.day_row_count("工单明细", "2026-07-16", d) == 0
    backfill.clear_day("工单明细", "2026-07-15", d)
    assert backfill.day_row_count("工单明细", "2026-07-15", d) == 0

def main():
    test_iter_days()
    test_day_ops()
    print("backfill OK")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python tests/test_backfill.py`
Expected: `ModuleNotFoundError: No module named 'collector.backfill'`

- [ ] **Step 3: 写最小实现**

创建 `collector/backfill.py`：

```python
# -*- coding: utf-8 -*-
"""回填 工单明细/会话记录 历史缺失数据（5 分钟颗粒度）。

按时间列(工单明细=创建日期, 会话记录=开始时间)的 5 分钟分桶，生成业务窗口内
每 5 分钟的累计快照(值=该刻度之前创建/开始的累计) + 23:59 全天总计。dashboard
方案D 用 first[H+1]-first[H] 即得 H 小时新建量。

由根目录 backfill.py(薄 CLI) 和 manager.py(数据补全页) 共同调用。
"""
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from collector import storage

TIME_COL = {
    "工单明细": ("创建日期", "%Y-%m-%d %H:%M:%S"),
    "会话记录": ("开始时间", "%H:%M:%S"),
}

SLEEP = 2  # 每天下载后间隔秒数，避免请求过快；测试可置 0


def iter_days(start, end):
    """生成日期列表 YYYY-MM-DD，含首尾。start==end 返回单日。"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    out = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def day_row_count(source, day, data_dir):
    """该 source db 中某天的行数。无库返回 0。"""
    p = Path(data_dir) / f"{source}.db"
    if not p.exists():
        return 0
    c = sqlite3.connect(str(p))
    try:
        return c.execute('SELECT COUNT(*) FROM t WHERE "时间" LIKE ?', (f'{day}%',)).fetchone()[0]
    finally:
        c.close()


def clear_day(source, day, data_dir):
    """删除该 source db 中某天的所有行。"""
    c = sqlite3.connect(str(Path(data_dir) / f"{source}.db"))
    try:
        c.execute('DELETE FROM t WHERE "时间" LIKE ?', (f'{day}%',))
        c.commit()
    finally:
        c.close()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python tests/test_backfill.py`
Expected: `backfill OK`

- [ ] **Step 5: Checkpoint**

测试通过即本任务完成（项目无 git，不 commit）。

---

### Task 2: build_snapshots 5 分钟分桶

**Files:**
- Modify: `collector/backfill.py`（在 `clear_day` 之后追加 `build_snapshots`）
- Test: `tests/test_backfill.py`（追加两个测试函数）

**Interfaces:**
- Consumes: 无新依赖
- Produces: `build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end) -> (rows, total)`，其中 `rows` 是 dict 列表（每个含 `时间` + 各组累计值），`total` 是 `{组: 全天全量}`

- [ ] **Step 1: 写失败测试**

在 `tests/test_backfill.py` 的 `test_day_ops` 之后追加：

```python
def test_build_snapshots_gongdan():
    import pandas as pd
    df = pd.DataFrame({
        "创建日期": ["2026-07-15 09:02:00", "2026-07-15 09:07:00", "2026-07-15 09:12:00",
                  "2026-07-15 10:00:00", "2026-07-15 23:30:00"],
        "接收组": ["转接一组", "转接一组", "转接二组", "转接一组", "转接一组"],
    })
    fcfg = {"group_column": "接收组", "groups": ["转接一组", "转接二组"]}
    rows, total = backfill.build_snapshots(df, "2026-07-15", fcfg, ["转接一组", "转接二组"],
                                           "创建日期", "%Y-%m-%d %H:%M:%S", "09:00", "21:04")
    assert len(rows) == 146, len(rows)  # 09:00..21:00 共 145 刻度 + 23:59
    assert rows[0]["时间"] == "2026-07-15 09:00"
    assert rows[0]["转接一组"] == 0 and rows[0]["转接二组"] == 0
    assert rows[1]["时间"] == "2026-07-15 09:05" and rows[1]["转接一组"] == 1, rows[1]
    assert rows[2]["转接一组"] == 2, rows[2]              # <09:10 = 09:02+09:07
    assert rows[3]["转接一组"] == 2 and rows[3]["转接二组"] == 1, rows[3]  # <09:15 含 09:12
    assert rows[-1]["时间"] == "2026-07-15 23:59"
    assert rows[-1]["转接一组"] == 4 and rows[-1]["转接二组"] == 1, rows[-1]  # 全量含 23:30
    assert total == {"转接一组": 4, "转接二组": 1}, total

def test_build_snapshots_channel():
    import pandas as pd
    df = pd.DataFrame({
        "开始时间": ["09:02:00", "09:07:00"],
        "渠道来源": ["电话呼入呼入", "电话呼入"],  # 第二条被 channel 过滤
        "处理组别": ["转接一组", "转接一组"],
    })
    fcfg = {"channel_column": "渠道来源", "channels": ["电话呼入呼入"],
            "group_column": "处理组别", "groups": ["转接一组", "转接二组"]}
    rows, total = backfill.build_snapshots(df, "2026-07-15", fcfg, ["转接一组", "转接二组"],
                                           "开始时间", "%H:%M:%S", "09:00", "21:04")
    assert total == {"转接一组": 1, "转接二组": 0}, total
    assert rows[1]["转接一组"] == 1, rows[1]   # 09:05 刻度 cum = <09:05 = 09:02
```

并在 `main()` 中追加调用：

```python
    test_build_snapshots_gongdan()
    test_build_snapshots_channel()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python tests/test_backfill.py`
Expected: `AttributeError: module 'collector.backfill' has no attribute 'build_snapshots'`

- [ ] **Step 3: 写最小实现**

在 `collector/backfill.py` 的 `clear_day` 函数之后追加：

```python
def build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end):
    """5 分钟分桶 -> 业务窗口累计快照 + 23:59 全天总计。
    返回 (rows, total)：rows=窗口刻度+23:59 的 dict 列表，total={组:全天全量}。
    全天总计用 filter 后全量(含时间列缺失的记录)，与实时 count_groups 口径一致；
    累计快照仅含能分桶的记录(时间列缺失的不计入任何刻度)。"""
    import pandas as pd
    d = df
    if fcfg.get("channel_column"):
        d = d[d[fcfg["channel_column"]].isin(fcfg["channels"])]
    d = d[d[fcfg["group_column"]].isin(groups)]
    gc = fcfg["group_column"]
    ts = pd.to_datetime(d[time_col], format=fmt, errors="coerce")
    mods = ts.dt.hour * 60 + ts.dt.minute          # minute_of_day, NaN for 缺失
    slots = mods // 5                                # 0..287, NaN for 缺失
    bucket = {}
    for slot in range(288):
        cnt = d[slots == slot][gc].value_counts()
        bucket[slot] = {g: int(cnt.get(g, 0)) for g in groups}
    cum = {}
    running = {g: 0 for g in groups}
    for slot in range(288):
        cum[slot] = dict(running)
        for g in groups:
            running[g] += bucket[slot][g]
    full_cnt = d[gc].value_counts()
    total = {g: int(full_cnt.get(g, 0)) for g in groups}
    sh, sm = (int(x) for x in win_start.split(":"))
    eh, em = (int(x) for x in win_end.split(":"))
    start_slot = (sh * 60 + sm) // 5
    end_slot = (eh * 60 + em) // 5
    rows = []
    for slot in range(start_slot, end_slot + 1):
        hh, mm = divmod(slot * 5, 60)
        vals = {"时间": f"{day} {hh:02d}:{mm:02d}"}
        vals.update(cum[slot])
        rows.append(vals)
    vals = {"时间": f"{day} 23:59"}
    vals.update(total)
    rows.append(vals)
    return rows, total
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python tests/test_backfill.py`
Expected: `backfill OK`

- [ ] **Step 5: Checkpoint**

测试通过即本任务完成。

---

### Task 3: download_day + backfill_source

**Files:**
- Modify: `collector/backfill.py`（在 `build_snapshots` 之后追加 `download_day` 和 `backfill_source`）
- Test: `tests/test_backfill.py`（追加 `test_backfill_source`）

**Interfaces:**
- Consumes: `collector.detail._parse_excel`、Task 1/2 的函数
- Produces: `download_day(mcfg, secrets, day, timeout=60) -> df`、`backfill_source(source, cfg, days, data_dir, overwrite=True, progress_cb=None) -> {"成功":int,"失败":int,"失败日期":[str]}`

- [ ] **Step 1: 写失败测试**

在 `tests/test_backfill.py` 追加（注意用 ROOT 定位 config.yaml，不依赖 cwd）：

```python
def test_backfill_source():
    import yaml, pandas as pd
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    d = tempfile.mkdtemp()
    backfill.SLEEP = 0
    fake = pd.DataFrame({"创建日期": ["2026-07-15 09:02:00"], "接收组": ["转接一组"]})
    backfill.download_day = lambda mcfg, secrets, day, timeout=60: fake
    msgs = []
    res = backfill.backfill_source("工单明细", cfg, ["2026-07-15"], d,
                                   overwrite=True, progress_cb=msgs.append)
    assert res["成功"] == 1 and res["失败"] == 0, res
    assert backfill.day_row_count("工单明细", "2026-07-15", d) == 146, \
        backfill.day_row_count("工单明细", "2026-07-15", d)
    # 失败 continue：07-16 下载失败，不写、不计成功
    def _fail(*a, **k):
        raise RuntimeError("net")
    backfill.download_day = _fail
    res2 = backfill.backfill_source("工单明细", cfg, ["2026-07-16"], d,
                                    overwrite=True, progress_cb=msgs.append)
    assert res2["失败"] == 1 and res2["成功"] == 0, res2
    assert backfill.day_row_count("工单明细", "2026-07-16", d) == 0
    assert any("下载失败" in m for m in msgs), msgs
```

并在 `main()` 追加 `test_backfill_source()`。

- [ ] **Step 2: 运行测试验证失败**

Run: `python tests/test_backfill.py`
Expected: `AttributeError: module 'collector.backfill' has no attribute 'backfill_source'`

- [ ] **Step 3: 写最小实现**

在 `collector/backfill.py` 的 `build_snapshots` 之后追加：

```python
def download_day(mcfg, secrets, day, timeout=60):
    """下载某天明细 Excel，返回原始 df。"""
    import requests
    from collector.detail import _parse_excel
    data = dict(mcfg["data"])
    data["token"] = secrets["token"]
    data["tenementId"] = secrets["tenementId"]
    dv = day if mcfg["date_format"] == "%Y-%m-%d" else f"{day} 00:00:00"
    data[mcfg["date_fields"]["start"]] = dv
    data[mcfg["date_fields"]["end"]] = dv
    resp = requests.post(mcfg["url"], json=data, timeout=timeout)
    resp.raise_for_status()
    return _parse_excel(resp.content)


def backfill_source(source, cfg, days, data_dir, overwrite=True, progress_cb=None):
    """回填某 source 的若干天。返回 {"成功":int,"失败":int,"失败日期":[str]}。
    overwrite=True：每天下载成功后先 clear_day 再写；下载失败则 continue 下一天。"""
    mcfg = cfg["detail_modes"][source]
    secrets = cfg["secrets"]
    fcfg = mcfg["filter"]
    groups = fcfg["groups"]
    time_col, fmt = TIME_COL[source]
    win_start = cfg["schedule"]["window_start"]
    win_end = cfg["schedule"]["window_end"]
    if progress_cb is None:
        progress_cb = lambda s: None
    ok = fail = 0
    fail_days = []
    for day in days:
        if not overwrite and day_row_count(source, day, data_dir) > 0:
            progress_cb(f"{source} {day}: 已有数据，跳过")
            continue
        try:
            df = download_day(mcfg, secrets, day)
        except Exception as e:
            progress_cb(f"{source} {day}: 下载失败 {e}")
            fail += 1
            fail_days.append(day)
            continue
        rows, total = build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end)
        clear_day(source, day, data_dir)
        for vals in rows:
            storage.insert(source, vals, data_dir)
        progress_cb(f"{source} {day}: 写入 {len(rows)} 行 | "
                    + " ".join(f"{g}={total[g]}" for g in groups))
        ok += 1
        time.sleep(SLEEP)
    return {"成功": ok, "失败": fail, "失败日期": fail_days}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python tests/test_backfill.py`
Expected: `backfill OK`

- [ ] **Step 5: Checkpoint**

测试通过即本任务完成。`collector/backfill.py` 至此完整。

---

### Task 4: 根目录 backfill.py 改薄 CLI

**Files:**
- Modify: `backfill.py`（整体替换为薄 CLI，逻辑改用 `collector.backfill`）

**Interfaces:**
- Consumes: `collector.backfill.backfill_source`
- Produces: 命令行入口 `python backfill.py [会话记录|工单明细 [YYYY-MM-DD]]`（用法不变）

- [ ] **Step 1: 整体替换 backfill.py**

用以下内容整体替换根目录 `backfill.py`：

```python
# -*- coding: utf-8 -*-
"""回填 工单明细/会话记录 历史缺失数据（薄 CLI，逻辑在 collector.backfill）。

用法：
  python backfill.py                         # 补两个 source 的 07-01 ~ 07-23
  python backfill.py 会话记录                 # 只补会话记录
  python backfill.py 会话记录 2026-07-15      # 指定单日
"""
import sys
from datetime import datetime, timedelta
import yaml
from collector import backfill

CFG_PATH = 'config.yaml'
DATA_DIR = 'data'
DEFAULT_START = datetime(2026, 7, 1)
DEFAULT_END = datetime(2026, 7, 23)


def load_cfg():
    return yaml.safe_load(open(CFG_PATH, encoding='utf-8'))


def main():
    cfg = load_cfg()
    modes = cfg['detail_modes']
    args = sys.argv[1:]
    if args and args[0] in modes:
        sources = [args[0]]
        rest = args[1:]
    else:
        sources = list(modes.keys())
        rest = args
    if rest:
        days = [rest[0]]
    else:
        days = []
        d = DEFAULT_START
        while d <= DEFAULT_END:
            days.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)
    for source in sources:
        print(f'=== {source} ===')
        res = backfill.backfill_source(source, cfg, days, DATA_DIR,
                                       overwrite=False, progress_cb=print)
        print(f'  成功 {res["成功"]} 失败 {res["失败"]}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证可 import（不触发下载）**

Run: `python -c "import backfill; print('backfill cli OK')"`
Expected: `backfill cli OK`

- [ ] **Step 3: Checkpoint**

import 成功即本任务完成（命令行端到端需真实网络，不在自动测试范围；用法与原脚本一致）。

---

### Task 5: manager.py 数据补全页

**Files:**
- Modify: `manager.py`（`__init__` 加属性、`_build_ui` 加 Notebook 页、追加 5 个方法）

**Interfaces:**
- Consumes: `collector.backfill.iter_days`、`collector.backfill.backfill_source`、`self.cfg`
- Produces: manager「数据补全」页（UI 不测，同进线量预测页策略）

- [ ] **Step 1: __init__ 加去重锁属性**

在 `manager.py` 的 `__init__` 中，找到 `self._forecast_running = False` 那一行（约 line 365），在其后插入：

```python
        self._backfill_running = False  # 数据补全去重锁
```

- [ ] **Step 2: _build_ui 加 Notebook 页**

在 `manager.py` 的 `_build_ui` 中，找到这三行：

```python
        fc_page = tk.Frame(nb)
        nb.add(fc_page, text="进线量预测")
        self._build_forecast_page(fc_page)
```

在其后插入：

```python
        bf_page = tk.Frame(nb)
        nb.add(bf_page, text="数据补全")
        self._build_backfill_page(bf_page)
```

- [ ] **Step 3: 追加数据补全页方法**

在 `manager.py` 的 `_forecast_summary` 方法之后（即「进线量预测」相关方法块的末尾，约 line 717 之后），追加以下方法：

```python
    # ---- 数据补全(手动触发,5 分钟颗粒度回填)----
    def _build_backfill_page(self, page: tk.Frame) -> None:
        top = tk.Frame(page, padx=10, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="开始日期:").pack(side=tk.LEFT)
        self.bf_start_var = tk.StringVar()
        tk.Entry(top, width=12, textvariable=self.bf_start_var).pack(side=tk.LEFT, padx=4)
        tk.Label(top, text="结束日期:").pack(side=tk.LEFT)
        self.bf_end_var = tk.StringVar()
        tk.Entry(top, width=12, textvariable=self.bf_end_var).pack(side=tk.LEFT, padx=4)
        self.bf_src_hl = tk.BooleanVar(value=True)
        self.bf_src_gd = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="会话记录", variable=self.bf_src_hl).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(top, text="工单明细", variable=self.bf_src_gd).pack(side=tk.LEFT, padx=4)
        self.btn_backfill = tk.Button(top, text="开始补全", width=10, command=self._run_backfill)
        self.btn_backfill.pack(side=tk.LEFT, padx=6)
        self.bf_status_var = tk.StringVar(value="就绪")
        tk.Label(top, textvariable=self.bf_status_var, fg="#555555").pack(side=tk.LEFT, padx=10)
        self.bf_box = scrolledtext.ScrolledText(page, wrap=tk.WORD, font=("Consolas", 10))
        self.bf_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self.bf_box.configure(state=tk.DISABLED)
        self._set_backfill_text(
            "输入开始/结束日期(YYYY-MM-DD，结束留空=单日)，勾选源后点「开始补全」。\n"
            "按 5 分钟颗粒度回填历史数据(总是覆盖)。含今天时建议先停采集器。")

    def _set_backfill_text(self, text: str) -> None:
        self.bf_box.configure(state=tk.NORMAL)
        self.bf_box.delete("1.0", tk.END)
        self.bf_box.insert(tk.END, text)
        self.bf_box.configure(state=tk.DISABLED)

    def _append_backfill_text(self, text: str) -> None:
        self.bf_box.configure(state=tk.NORMAL)
        self.bf_box.insert(tk.END, text + "\n")
        self.bf_box.see(tk.END)
        self.bf_box.configure(state=tk.DISABLED)

    def _set_backfill_status(self, s: str) -> None:
        self.bf_status_var.set(s)

    def _run_backfill(self) -> None:
        if self._backfill_running:
            return
        from datetime import datetime as _dt
        start = self.bf_start_var.get().strip()
        end = self.bf_end_var.get().strip() or start
        try:
            _dt.strptime(start, "%Y-%m-%d")
            _dt.strptime(end, "%Y-%m-%d")
        except ValueError:
            self._set_backfill_status("日期格式错误"); return
        if start > end:
            self._set_backfill_status("开始>结束"); return
        sources = []
        if self.bf_src_hl.get(): sources.append("会话记录")
        if self.bf_src_gd.get(): sources.append("工单明细")
        if not sources:
            self._set_backfill_status("请至少勾一个源"); return
        today = _dt.now().strftime("%Y-%m-%d")
        warn = ("\n\n⚠ 含今天；若采集器在跑，今天的快照会与采集器 5 分钟快照混合(口径一致)，"
                "建议先停采集器再补今天。") if start <= today <= end else ""
        self._set_backfill_status("运行中...")
        self._set_backfill_text("正在补全，请稍候..." + warn)
        self.btn_backfill.configure(state=tk.DISABLED)
        self._backfill_running = True

        def worker():
            try:
                from collector import backfill
                days = backfill.iter_days(start, end)
                data_dir = self.cfg["storage"]["dir"]
                parts = []
                for src in sources:
                    res = backfill.backfill_source(src, self.cfg, days, data_dir,
                                                   overwrite=True, progress_cb=self._on_backfill_progress)
                    parts.append(f"{src}: 成功 {res['成功']} 失败 {res['失败']}"
                                 + (f"({','.join(res['失败日期'])})" if res['失败日期'] else ""))
                self.root.after(0, self._on_backfill_done, "\n".join(parts), None)
            except Exception as exc:
                log.exception("手动补全失败")
                self.root.after(0, self._on_backfill_done, "", exc)

        threading.Thread(target=worker, daemon=True, name="backfill").start()

    def _on_backfill_progress(self, text: str) -> None:
        self.root.after(0, self._append_backfill_text, text)

    def _on_backfill_done(self, summary: str, err: Exception | None) -> None:
        self._backfill_running = False
        self.btn_backfill.configure(state=tk.NORMAL)
        if err is not None:
            self._set_backfill_status("失败")
            self._append_backfill_text(f"\n补全失败: {err}\n详见 logs/manager.log")
        else:
            self._set_backfill_status("完成")
            self._append_backfill_text("\n=== 汇总 ===\n" + summary)
```

- [ ] **Step 4: 验证可 import**

Run: `python -c "import manager; print('manager OK')"`
Expected: `manager OK`

- [ ] **Step 5: Checkpoint**

import 成功即本任务完成。UI 交互不自动测（同进线量预测页策略）；可手动 `python manager.py` 启动确认「数据补全」页出现、输入日期点按钮触发后台线程。

---

### Task 6: 更新 CLAUDE.md 文档

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新 backfill.py 描述**

在 `CLAUDE.md` 中找到 `backfill.py` 的描述段（「`backfill.py` 是一个独立回填工具，通过修改 detail 请求日期...」整段），替换为：

```markdown
- `backfill.py` 是薄 CLI，调用 `collector.backfill` 回填 工单明细/会话记录 历史。运行 `python backfill.py [工单明细|会话记录 [YYYY-MM-DD]]`，无参默认补 07-01~07-23。幂等（`overwrite=False`，已补日期跳过）、失败 continue（断点续跑，失败日期下次重试）。
```

- [ ] **Step 2: 在 collector 段补 collector/backfill.py 条目**

在 `CLAUDE.md` 的 collector 架构段（`collector/notify.py` 段之后或 `collector/forecast.py` 段附近），插入：

```markdown
**5 分钟颗粒度回填** (`collector/backfill.py`)：函数式 API，被根目录 `backfill.py`(薄 CLI) 和 `manager.py`(数据补全页) 共用。`build_snapshots` 按时间列(工单明细=创建日期, 会话记录=开始时间)的 5 分钟分桶，生成业务窗口(09:00-21:04 -> 刻度 09:00..21:00)每 5 分钟累计快照 + 23:59 全天总计（146 行/天），`cum[slot]=时间列<slot*5 的累计`，与实时采集器 5 分钟快照语义对齐、dashboard 方案D 兼容。`backfill_source(source, cfg, days, data_dir, overwrite=True, progress_cb=None)` 总是覆盖（download 成功后 clear_day 再写）、失败 continue。`TIME_COL`/`SLEEP` 常量。
```

- [ ] **Step 3: manager 段补「数据补全页」描述**

在 `CLAUDE.md` 的 Manager 段（「进线量预测页」描述之后），插入：

```markdown
- **数据补全页**: Notebook 第四页「数据补全」= 开始/结束日期 Entry(YYYY-MM-DD，结束留空=单日) + 会话记录/工单明细 Checkbutton(默认都勾) + 「开始补全」按钮 + 进度 ScrolledText。点按钮在后台线程调 `collector.backfill.backfill_source(..., overwrite=True, progress_cb=...)`，逐日进度经 `root.after` 回主线程追加（不直接碰 Tk）。去重锁 `_backfill_running`。含今天时进度框提示并发（建议先停采集器）。汇总显示各源成功/失败数。
```

- [ ] **Step 4: 更新「Known behaviors」段**

在 `CLAUDE.md` 的「Dashboard data gaps render as 无数据」条目中，把「按「创建日期/开始时间」小时分桶补全（整点累计快照，单调递增...）」改为「按「创建日期/开始时间」**5 分钟**分桶补全（业务窗口 09:00-21:00 每 5 分钟累计快照 + 23:59 总计，146 行/天，单调递增、不受接收组转派影响）」。

- [ ] **Step 5: Checkpoint**

文档更新完成。快速命令参考表不变（`python backfill.py` 用法不变）。

---

## Self-Review

**1. Spec 覆盖**：§3 模块结构 -> Task 1-5；§4 API -> Task 1-3（iter_days/day_row_count/clear_day/build_snapshots/download_day/backfill_source 全覆盖）；§5 build_snapshots 5 分钟分桶 -> Task 2；§6 薄 CLI -> Task 4；§7 manager 页 -> Task 5；§8 行为规则（总是覆盖/失败 continue/含今天提示）-> Task 3+5；§9 测试 -> Task 1-3 测试；§10 文档 -> Task 6。无遗漏。

**2. Placeholder 扫描**：无 TBD/TODO/「适当处理」/「类似 Task N」；每个代码步骤都有完整代码。

**3. 类型一致**：`backfill_source` 返回 `{"成功","失败","失败日期"}` 在 Task 3 定义、Task 4 CLI 与 Task 5 manager 消费处键名一致；`build_snapshots` 签名 `(df, day, fcfg, groups, time_col, fmt, win_start, win_end)` 在 Task 2 定义、Task 3 调用处一致；`iter_days(start,end)->list[str]` 在 Task 1 定义、Task 5 调用处一致；`SLEEP` 在 Task 1 定义、Task 3 使用、Task 3 测试置 0。一致。
