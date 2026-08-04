# 承接情况看板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 `data/` 下 9 个 SQLite 库 + `data/预估流入量.csv`，用 Flask + pyecharts 构建承接情况看板，支持日视图（小时粒度）与月视图（按日粒度）。

**Architecture:** 新增独立 `dashboard/` 包，Flask 进程只读 `data/*.db`，与采集器分进程并行。`queries.py` 负责读库+聚合（每小时取最新 5 分钟快照；月视图累积量取每日最新、瞬时量取每日均值），`app.py` 用 pyecharts 构图并渲染单页模板，展示 4 部分（整体累计卡片 / 接听图表 / 外呼图表 / 时段明细表）。

**Tech Stack:** Python 3 (venv `.venv`)、Flask、pyecharts (ECharts)、sqlite3、csv、Jinja2。

## Global Constraints

- 平台 Windows + PowerShell。Python 一律用 venv 解释器 `.\.venv\Scripts\python.exe`，绝不用系统 Python。
- 任何中文输出前设 `$env:PYTHONIOENCODING="utf-8"`。
- **测试约定：纯 assert 脚本，禁止 pytest。** 每个测试是带 `main()` 的脚本，`sys.path` 自举到项目根，用 `assert`，结尾 `print("xxx OK")`，通过 `python tests/test_xxx.py` 直接运行。
- **无 git（未安装）：不做任何 commit/branch 操作。** 每个任务以"运行测试、期望输出 OK"收尾。
- 测试可从任意目录运行（脚本自举 `sys.path`）。
- 中文列名/表名：SQLite 以 UTF-8 存储，查询时加双引号包裹。
- 数据库 `时间` 列格式 `"YYYY-MM-DD HH:MM"`（5 分钟颗粒度，:05 整数倍）。
- CSV `data/预估流入量.csv` 列：`时间,线路,时段预估量,累计预估量`；`时间` 格式 `"YYYY-MM-DD HH:MM"`（15 分钟颗粒度）；`线路` 取值 `热线`/`在线`。
- 不改动现有采集器代码（`main.py`/`scheduler.py`/`ws.py`/`detail.py`/`storage.py`/`config.yaml`）；看板是新增只读组件。
- 设计依据：`docs/superpowers/specs/2026-07-27-capacity-dashboard-design.md`。

## File Structure

- `dashboard/__init__.py` - 空包标记。
- `dashboard/queries.py` - 数据层：DB 连接、`hourly_latest`/`daily_latest`/`daily_avg`、CSV 预测量 `load_forecast`/`forecast_increment`、`forecast_12378`、`build_day`/`build_month`。
- `dashboard/charts.py` - pyecharts 图表构建：`inbound_chart(group_data)`、`outbound_chart(group_data)`，返回 `pyecharts.charts.Bar` 对象。
- `dashboard/app.py` - Flask 应用：路由 `/`、`/?view=day&date=`、`/?view=month&date=`，组装数据+图+模板。
- `dashboard/templates/dashboard.html` - 单页模板：顶部控件 + 4 部分 + 图容器 + 两张明细表。
- `tests/test_dashboard_queries.py` - queries.py 测试（纯 assert）。
- `tests/test_dashboard_charts.py` - charts.py 测试（纯 assert）。
- `tests/test_dashboard_app.py` - app.py 路由/模板测试（纯 assert）。

---

### Task 1: 依赖安装与包骨架

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/app.py`
- Create: `dashboard/templates/` (目录)
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Produces: `dashboard.app.create_app()` -> Flask app 对象；`dashboard.app.app` 模块级 Flask 实例。

- [ ] **Step 1: 安装依赖到 venv**

Run:
```powershell
.\.venv\Scripts\python.exe -m pip install flask pyecharts
```
Expected: `Successfully installed flask-... pyecharts-...`

- [ ] **Step 2: 创建包标记**

`dashboard/__init__.py`:
```python
# -*- coding: utf-8 -*-
```

- [ ] **Step 3: 创建最小 Flask 应用**

`dashboard/app.py`:
```python
# -*- coding: utf-8 -*-
"""承接情况看板 Flask 应用。"""
from flask import Flask

app = Flask(__name__)

def create_app():
    return app

@app.route("/")
def index():
    return "dashboard ok"

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
```

- [ ] **Step 4: 写失败测试**

`tests/test_dashboard_app.py`:
```python
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    from dashboard.app import app
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200, resp.status_code
    assert b"dashboard ok" in resp.data, resp.data
    print("dashboard_app OK")

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行测试**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_app.py
```
Expected: `dashboard_app OK`

---

### Task 2: 每小时最新快照 `hourly_latest`

**Files:**
- Create: `dashboard/queries.py`
- Test: `tests/test_dashboard_queries.py`

**Interfaces:**
- Produces: `hourly_latest(data_dir, source, date_str) -> dict[int, dict]`。返回 `{小时: {列名: 值}}`，仅含 `date_str` 当天行，每小时取时间戳最大的一条。空库/无数据返回 `{}`。`source` 为库名（如 `"热线"`），`date_str` 为 `"YYYY-MM-DD"`。

- [ ] **Step 1: 写失败测试**

`tests/test_dashboard_queries.py`（本任务起逐步追加 `test_*` 函数到 `main()` 调用列表）:
```python
# -*- coding: utf-8 -*-
import sys, os, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
import dashboard.queries as Q

def _seed(d, source, cols, rows):
    con = sqlite3.connect(Path(d) / f"{source}.db")
    quoted = ",".join(f'"{c}"' for c in cols)
    ph = ",".join("?" * len(cols))
    con.execute(f'CREATE TABLE t ({quoted})')
    con.executemany(f'INSERT INTO t ({quoted}) VALUES ({ph})', rows)
    con.commit(); con.close()

def test_hourly_latest():
    d = tempfile.mkdtemp()
    cols = ["时间", "转人工量", "接通量"]
    rows = [
        ("2026-07-27 09:05", 10, 9),
        ("2026-07-27 09:30", 20, 18),   # 9 点最新
        ("2026-07-27 10:05", 25, 23),
        ("2026-07-27 10:35", 30, 28),   # 10 点最新
        ("2026-07-26 10:35", 99, 99),   # 前一天，应排除
    ]
    _seed(d, "热线", cols, rows)
    out = Q.hourly_latest(d, "热线", "2026-07-27")
    assert set(out.keys()) == {9, 10}, out.keys()
    assert out[9]["转人工量"] == 20, out[9]
    assert out[10]["转人工量"] == 30, out[10]
    # 空库
    d2 = tempfile.mkdtemp()
    assert Q.hourly_latest(d2, "热线", "2026-07-27") == {}

def main():
    test_hourly_latest()
    print("dashboard_queries OK")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试，期望失败**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_queries.py
```
Expected: FAIL（`ModuleNotFoundError` 或 `AttributeError: hourly_latest`）

- [ ] **Step 3: 实现 `hourly_latest`**

`dashboard/queries.py`:
```python
# -*- coding: utf-8 -*-
"""看板数据层：只读 data/*.db + 预估流入量.csv，做小时/按日聚合。"""
import sqlite3
from pathlib import Path

def _connect(data_dir, source):
    return sqlite3.connect(str(Path(data_dir) / f"{source}.db"))

def _cols(con):
    return [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]

def hourly_latest(data_dir, source, date_str):
    """{小时: {列: 值}}，当天每小时取时间戳最大的一条。无表/无数据返回 {}。"""
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return {}
    con = _connect(data_dir, source)
    try:
        cols = _cols(con)
        if not cols:
            return {}
        rows = con.execute(
            f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM t '
            f'WHERE "时间" LIKE ? ORDER BY "时间"', (f"{date_str}%",)
        ).fetchall()
    finally:
        con.close()
    out = {}
    for r in rows:
        row = dict(zip(cols, r))
        hh = int(row["时间"][11:13])
        out[hh] = row  # 已按时间升序，后者覆盖前者 -> 保留最大
    return out
```

- [ ] **Step 4: 运行测试，期望通过**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_queries.py
```
Expected: `dashboard_queries OK`

---

### Task 3: 月视图按日聚合 `daily_latest` / `daily_avg`

**Files:**
- Modify: `dashboard/queries.py`（追加函数）
- Test: `tests/test_dashboard_queries.py`（追加测试）

**Interfaces:**
- Produces:
  - `daily_latest(data_dir, source, ym) -> dict[int, dict]`：`{日: {列:值}}`，该月每日取时间戳最大一条（累积量用）。`ym` 为 `"YYYY-MM"`。
  - `daily_avg(data_dir, source, ym) -> dict[int, dict]`：`{日: {列:均值}}`，该月每日全部行求算术平均（瞬时量用）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_dashboard_queries.py`（在 `test_hourly_latest` 之后）:
```python
def test_daily_latest_and_avg():
    d = tempfile.mkdtemp()
    cols = ["时间", "签入", "转人工量"]
    rows = [
        ("2026-07-01 09:05", 10, 100),
        ("2026-07-01 09:30", 20, 150),   # 7/1 最新 -> 累积量取此；瞬时签入 avg=(10+20)/2=15
        ("2026-07-02 09:05", 30, 200),
        ("2026-07-02 21:00", 40, 300),   # 7/2 最新
        ("2026-06-30 21:00", 99, 999),   # 前月排除
    ]
    _seed(d, "常规", cols, rows)
    latest = Q.daily_latest(d, "常规", "2026-07")
    assert set(latest.keys()) == {1, 2}, latest.keys()
    assert latest[1]["转人工量"] == 150, latest[1]
    assert latest[2]["转人工量"] == 300, latest[2]
    avg = Q.daily_avg(d, "常规", "2026-07")
    assert avg[1]["签入"] == 15.0, avg[1]
    assert avg[2]["签入"] == 35.0, avg[2]
    # 转人工量(累积)在 daily_avg 里也会被平均，但月视图只对瞬时列用 daily_avg，调用方负责
```
并在 `main()` 中 `test_hourly_latest()` 之后加 `test_daily_latest_and_avg()`。

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: FAIL（`AttributeError: daily_latest`）

- [ ] **Step 3: 实现**

追加到 `dashboard/queries.py`:
```python
def _rows_in_month(data_dir, source, ym):
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return [], []
    con = _connect(data_dir, source)
    try:
        cols = _cols(con)
        if not cols:
            return [], cols
        rows = con.execute(
            f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM t '
            f'WHERE "时间" LIKE ? ORDER BY "时间"', (f"{ym}%",)
        ).fetchall()
    finally:
        con.close()
    return rows, cols

def daily_latest(data_dir, source, ym):
    rows, cols = _rows_in_month(data_dir, source, ym)
    out = {}
    for r in rows:
        row = dict(zip(cols, r))
        day = int(row["时间"][8:10])
        out[day] = row  # 升序，保留最大
    return out

def daily_avg(data_dir, source, ym):
    rows, cols = _rows_in_month(data_dir, source, ym)
    buckets = {}
    for r in rows:
        row = dict(zip(cols, r))
        day = int(row["时间"][8:10])
        buckets.setdefault(day, []).append(row)
    out = {}
    for day, rs in buckets.items():
        n = len(rs)
        out[day] = {c: (sum(r[c] for r in rs) / n) for c in cols if c != "时间"}
    return out
```

- [ ] **Step 4: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: `dashboard_queries OK`

---

### Task 4: CSV 预测量 `load_forecast` / `forecast_increment`

**Files:**
- Modify: `dashboard/queries.py`（追加函数）
- Test: `tests/test_dashboard_queries.py`（追加测试）

**Interfaces:**
- Produces:
  - `load_forecast(data_dir, line, date_str) -> dict[int, int]`：`{小时: 累计预估量}`，`line` 为 `"热线"`/`"在线"`。每小时取该小时内时间戳最大的 CSV 行的 `累计预估量`。无数据返回 `{}`。
  - `forecast_increment(data_dir, line, date_str) -> dict[int, int]`：`{小时: 该小时时段预估量之和}`，用于"时段预测量"。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_dashboard_queries.py`:
```python
def _seed_csv(d, rows):
    import csv
    with open(Path(d) / "预估流入量.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "线路", "时段预估量", "累计预估量"])
        for r in rows:
            w.writerow(r)

def test_forecast():
    d = tempfile.mkdtemp()
    _seed_csv(d, [
        ("2026-07-27 09:15", "热线", "100", "100"),
        ("2026-07-27 09:30", "热线", "50", "150"),   # 9 点最新累计=150；增量和=100+50=150
        ("2026-07-27 09:45", "热线", "30", "180"),
        ("2026-07-27 10:00", "热线", "20", "200"),   # 10 点（floor 归 10 点）
        ("2026-07-27 10:15", "热线", "10", "210"),   # 10 点最新累计=210；增量和=20+10=30
        ("2026-07-27 09:15", "在线", "5", "5"),
    ])
    fc = Q.load_forecast(d, "热线", "2026-07-27")
    assert fc[9] == 180, fc   # 9 点最大时间戳 09:45 -> 累计 180
    assert fc[10] == 210, fc
    assert "在线" not in fc or fc.get(9) == 5  # load_forecast 只取热线
    inc = Q.forecast_increment(d, "热线", "2026-07-27")
    assert inc[9] == 180, inc   # 100+50+30
    assert inc[10] == 30, inc   # 20+10
    # 在线单独
    fc2 = Q.load_forecast(d, "在线", "2026-07-27")
    assert fc2[9] == 5, fc2
```
在 `main()` 中追加 `test_forecast()`。

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: FAIL（`AttributeError: load_forecast`）

- [ ] **Step 3: 实现**

追加到 `dashboard/queries.py`:
```python
import csv

def _forecast_rows(data_dir, line, date_str):
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["线路"] == line and r["时间"].startswith(date_str):
                out.append((r["时间"], int(r["时段预估量"]), int(r["累计预估量"])))
    out.sort(key=lambda x: x[0])
    return out

def load_forecast(data_dir, line, date_str):
    """{小时: 累计预估量}，每小时取时间戳最大一行。"""
    out = {}
    for ts, _inc, cum in _forecast_rows(data_dir, line, date_str):
        out[int(ts[11:13])] = cum  # 升序覆盖 -> 最大
    return out

def forecast_increment(data_dir, line, date_str):
    """{小时: 该小时时段预估量之和}。"""
    out = {}
    for ts, inc, _cum in _forecast_rows(data_dir, line, date_str):
        hh = int(ts[11:13])
        out[hh] = out.get(hh, 0) + inc
    return out
```

- [ ] **Step 4: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: `dashboard_queries OK`

---

### Task 5: 12378 预测量 `forecast_12378`

**Files:**
- Modify: `dashboard/queries.py`（追加函数）
- Test: `tests/test_dashboard_queries.py`（追加测试）

**Interfaces:**
- Produces: `forecast_12378(data_dir, date_str) -> dict[int, int]`：`{小时: 累计转人工量}`，取 `date_str` 前 7 天当天 12378.db 每小时最新一条的 `转人工量`。7 天前无数据返回 `{}`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_dashboard_queries.py`:
```python
def test_forecast_12378():
    import datetime
    d = tempfile.mkdtemp()
    cols = ["时间", "转人工量", "接通量"]
    rows = [
        ("2026-07-20 09:05", 10, 9),
        ("2026-07-20 09:30", 20, 18),   # 7/20 9 点最新 -> 20
        ("2026-07-20 10:05", 25, 23),   # 7/20 10 点最新 -> 25
        ("2026-07-27 09:05", 999, 999), # 当天，应排除
    ]
    _seed(d, "12378", cols, rows)
    fc = Q.forecast_12378(d, "2026-07-27")
    assert fc[9] == 20, fc
    assert fc[10] == 25, fc
    # 7 天前无数据
    d2 = tempfile.mkdtemp()
    assert Q.forecast_12378(d2, "2026-07-27") == {}
```
在 `main()` 中追加 `test_forecast_12378()`。

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: FAIL（`AttributeError: forecast_12378`）

- [ ] **Step 3: 实现**

追加到 `dashboard/queries.py`:
```python
def forecast_12378(data_dir, date_str):
    """{小时: 累计转人工量}，取 date_str 前 7 天当天 12378.db 每小时最新一条。"""
    from datetime import date, timedelta
    y, m, dd = (int(x) for x in date_str.split("-"))
    prev = (date(y, m, dd) - timedelta(days=7)).strftime("%Y-%m-%d")
    snap = hourly_latest(data_dir, "12378", prev)
    return {hh: row["转人工量"] for hh, row in snap.items()}
```

- [ ] **Step 4: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: `dashboard_queries OK`

---

### Task 6: 日视图组装 `build_day`

**Files:**
- Modify: `dashboard/queries.py`（追加 `build_day` 及私有助手）
- Test: `tests/test_dashboard_queries.py`（追加测试）

**Interfaces:**
- Consumes: `hourly_latest`、`load_forecast`、`forecast_increment`、`forecast_12378`（Task 2/4/5）。
- Produces: `build_day(date_str, data_dir="data") -> dict`，结构见下，供 `charts.py` 与模板消费。

返回结构：
```python
{
  "date": "2026-07-27", "current_hour": 10,            # 当天有数据的最大小时；无数据则 None
  "inbound": {                                           # 每组：hours 列表 + 各指标 {小时:值}
    "热线":   {"hours":[9..21], "预测量":{h:v}, "转人工量":{h:v}, "转人工成功量":{h:v}, "签入":{h:v}, "空闲":{h:v}},
    "在线":   {"hours":[9..21], "预测量":{...}, "转人工量":{...}, "转人工成功量":{...}, "在线":{...}},
    "12378":  {"hours":[8..21 或 9..18], "预测量":{...}, "转人工量":{...}, "转人工成功量":{...},
               "签入":{...}, "空闲":{...}, "12378回访组":{...}},
  },
  "outbound": {
    "常规二线": {"hours":[9..21], "工单量":{...}, "转接量":{...}, "签入":{...}, "空闲":{...}},
    "贷后二线": {"hours":[9..21], "工单量":{...}, "转接量":{...}, "签入":{...}, "空闲":{...}},
    "二线客诉": {"hours":[9..21], "工单量":{...}},
    "常规工单": {"hours":[9..21], "工单量":{...}},
  },
  "card": {"inbound": {"预测量":..,"时段预测量":..,"流入率":..,"转人工量":..,"转人工成功量":..,"接通率":..},
           "outbound": {"工单量":..,"转接量":..,"签入":..,"空闲":..}},
  "tables": {"inbound": [ {"小时":9, "热线_转人工量":.., ...}, ... ], "outbound": [ ... ]},
}
```

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_dashboard_queries.py`:
```python
def test_build_day():
    d = tempfile.mkdtemp()
    # 热线(量) + 热线明细(坐席)
    _seed(d, "热线", ["时间","转人工量","接通量","排队量","累计呼入量","外呼量","外呼接通量"],
          [("2026-07-27 10:35", 100, 95, 0, 200, 0, 0)])
    _seed(d, "热线明细", ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
          [("2026-07-27 10:35", 50, 20, 10, 5, 5, 0, 0)])
    # 在线
    _seed(d, "在线", ["时间","转人工量","转人工失败","排队","咨询","在线","小休","示忙","话后","就餐","培训","回访"],
          [("2026-07-27 10:35", 200, 5, 0, 0, 30, 2, 1, 0, 0, 0, 0)])
    # 12378(量) + 12378明细(坐席)
    _seed(d, "12378", ["时间","转人工量","接通量","排队量","累计呼入量"],
          [("2026-07-27 10:35", 40, 38, 0, 50)])
    _seed(d, "12378明细", ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
          [("2026-07-27 10:35", 5, 2, 1, 0, 1, 0, 0)])
    # 工单明细 + 会话记录 + 常规 + 贷后
    _seed(d, "工单明细", ["时间","二线客诉处理组","常规工单处理组","回访组一组","贷后回访组","12378回访组"],
          [("2026-07-27 10:35", 10, 20, 30, 40, 5)])
    _seed(d, "会话记录", ["时间","转接一组","转接二组","贷后转接组"],
          [("2026-07-27 10:35", 7, 3, 8)])
    _seed(d, "常规", ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
          [("2026-07-27 10:35", 12, 6, 0, 0, 6, 0, 0)])
    _seed(d, "贷后", ["时间","签入","通话","空闲","离席","话后","振铃","置忙"],
          [("2026-07-27 10:35", 15, 9, 4, 0, 2, 0, 0)])
    _seed_csv(d, [
        ("2026-07-27 10:00", "热线", "80", "800"),
        ("2026-07-27 10:15", "热线", "20", "820"),   # 10 点累计 820，增量 100
        ("2026-07-27 10:00", "在线", "60", "600"),
        ("2026-07-27 10:15", "在线", "15", "615"),   # 10 点累计 615，增量 75
    ])
    # 12378.db 仅有 07-27 数据，无 07-20 -> forecast_12378 返回 {}
    res = Q.build_day("2026-07-27", d)
    assert res["current_hour"] == 10, res["current_hour"]
    # 接听量
    assert res["inbound"]["热线"]["转人工量"][10] == 100
    assert res["inbound"]["热线"]["转人工成功量"][10] == 95
    assert res["inbound"]["在线"]["转人工成功量"][10] == 195  # 200-5
    assert res["inbound"]["在线"]["在线"][10] == 30
    assert res["inbound"]["12378"]["转人工量"][10] == 40
    assert res["inbound"]["12378"]["12378回访组"][10] == 5
    # 外呼
    assert res["outbound"]["常规二线"]["工单量"][10] == 30
    assert res["outbound"]["常规二线"]["转接量"][10] == 10  # 7+3
    assert res["outbound"]["贷后二线"]["转接量"][10] == 8
    assert res["outbound"]["二线客诉"]["工单量"][10] == 10
    assert res["outbound"]["常规工单"]["工单量"][10] == 20
    # 卡片
    c = res["card"]["inbound"]
    assert c["转人工量"] == 340  # 100+200+40
    assert c["转人工成功量"] == 328  # 95+195+38
    assert c["预测量"] is not None  # 热线820+在线615+12378(7天前，本目录12378为空->0/无)
    # 时段预测量 = 热线100 + 在线75 + 12378(0)
    assert c["时段预测量"] == 175, c
    # 表格
    assert res["tables"]["inbound"][0]["小时"] == 9
    assert any(r["小时"] == 10 for r in res["tables"]["inbound"])
```
在 `main()` 中追加 `test_build_day()`。

> 注：上例中 12378 预测量因 7 天前 12378.db 在同一临时目录为空，按"无数据返回 {}"取 0；独立验证 7 天前映射已在 Task 5 覆盖。

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: FAIL（`AttributeError: build_day`）

- [ ] **Step 3: 实现 `build_day` 及助手**

追加到 `dashboard/queries.py`:
```python
from datetime import date as _date, timedelta as _td

def _val(row, col):
    return row[col] if row else None

def _hours_for(name, date_str):
    """12378 工作日 8-21、周末 9-18；其余 9-21。返回小时列表。"""
    y, m, dd = (int(x) for x in date_str.split("-"))
    if name == "12378" and _date(y, m, dd).weekday() >= 5:
        return list(range(9, 19))
    if name == "12378":
        return list(range(8, 22))
    return list(range(9, 22))

def build_day(date_str, data_dir="data"):
    # --- 接听各组 hourly 快照 ---
    rx = hourly_latest(data_dir, "热线", date_str)
    rx_seat = hourly_latest(data_dir, "热线明细", date_str)
    im = hourly_latest(data_dir, "在线", date_str)
    z = hourly_latest(data_dir, "12378", date_str)
    z_seat = hourly_latest(data_dir, "12378明细", date_str)
    gd = hourly_latest(data_dir, "工单明细", date_str)
    hl = hourly_latest(data_dir, "会话记录", date_str)

    fc_rx = load_forecast(data_dir, "热线", date_str)
    fc_im = load_forecast(data_dir, "在线", date_str)
    fc_z = forecast_12378(data_dir, date_str)
    inc_rx = forecast_increment(data_dir, "热线", date_str)
    inc_im = forecast_increment(data_dir, "在线", date_str)

    h_other = list(range(9, 22))
    h_12378 = _hours_for("12378", date_str)

    inbound = {
        "热线": {
            "hours": h_other,
            "预测量": fc_rx, "转人工量": {h: _val(rx.get(h), "转人工量") for h in h_other},
            "转人工成功量": {h: _val(rx.get(h), "接通量") for h in h_other},
            "签入": {h: _val(rx_seat.get(h), "签入") for h in h_other},
            "空闲": {h: _val(rx_seat.get(h), "空闲") for h in h_other},
        },
        "在线": {
            "hours": h_other,
            "预测量": fc_im,
            "转人工量": {h: _val(im.get(h), "转人工量") for h in h_other},
            "转人工成功量": {h: (_val(im.get(h), "转人工量") or 0) - (_val(im.get(h), "转人工失败") or 0)
                          if im.get(h) else None for h in h_other},
            "在线": {h: _val(im.get(h), "在线") for h in h_other},
        },
        "12378": {
            "hours": h_12378,
            "预测量": fc_z, "转人工量": {h: _val(z.get(h), "转人工量") for h in h_12378},
            "转人工成功量": {h: _val(z.get(h), "接通量") for h in h_12378},
            "签入": {h: _val(z_seat.get(h), "签入") for h in h_12378},
            "空闲": {h: _val(z_seat.get(h), "空闲") for h in h_12378},
            "12378回访组": {h: _val(gd.get(h), "12378回访组") for h in h_12378},
        },
    }

    # --- 外呼各组 ---
    cg = hourly_latest(data_dir, "常规", date_str)
    dh = hourly_latest(data_dir, "贷后", date_str)
    outbound = {
        "常规二线": {
            "hours": h_other,
            "工单量": {h: _val(gd.get(h), "回访组一组") for h in h_other},
            "转接量": {h: (_val(hl.get(h), "转接一组") or 0) + (_val(hl.get(h), "转接二组") or 0)
                       if hl.get(h) else None for h in h_other},
            "签入": {h: _val(cg.get(h), "签入") for h in h_other},
            "空闲": {h: _val(cg.get(h), "空闲") for h in h_other},
        },
        "贷后二线": {
            "hours": h_other,
            "工单量": {h: _val(gd.get(h), "贷后回访组") for h in h_other},
            "转接量": {h: _val(hl.get(h), "贷后转接组") for h in h_other},
            "签入": {h: _val(dh.get(h), "签入") for h in h_other},
            "空闲": {h: _val(dh.get(h), "空闲") for h in h_other},
        },
        "二线客诉": {"hours": h_other, "工单量": {h: _val(gd.get(h), "二线客诉处理组") for h in h_other}},
        "常规工单": {"hours": h_other, "工单量": {h: _val(gd.get(h), "常规工单处理组") for h in h_other}},
    }

    # --- current_hour：接听三组实际数据的最大小时 ---
    data_hours = sorted(set(list(rx) + list(im) + list(z)))
    cur = data_hours[-1] if data_hours else None

    def at(d, h):
        return d.get(h)

    card_in = None
    if cur is not None:
        zj_prev = fc_z.get(cur - 1)
        zj_inc = (fc_z.get(cur) - zj_prev) if (fc_z.get(cur) is not None and zj_prev is not None) \
            else fc_z.get(cur)
        pred = (at(fc_rx, cur) or 0) + (at(fc_im, cur) or 0) + (at(fc_z, cur) or 0)
        slot = (at(inc_rx, cur) or 0) + (at(inc_im, cur) or 0) + (zj_inc or 0)
        zrg = (at(rx[cur], "转人工量") if rx.get(cur) else 0) + \
              (at(im[cur], "转人工量") if im.get(cur) else 0) + \
              (at(z[cur], "转人工量") if z.get(cur) else 0)
        succ = (at(rx[cur], "接通量") if rx.get(cur) else 0) + \
               ((at(im[cur], "转人工量") or 0) - (at(im[cur], "转人工失败") or 0) if im.get(cur) else 0) + \
               (at(z[cur], "接通量") if z.get(cur) else 0)
        card_in = {
            "预测量": pred, "时段预测量": slot,
            "流入率": round(zrg / pred, 4) if pred else None,
            "转人工量": zrg, "转人工成功量": succ,
            "接通率": round(succ / zrg, 4) if zrg else None,
        }
        gd_sum = (at(gd.get(cur), "回访组一组") or 0) + (at(gd.get(cur), "贷后回访组") or 0) + \
                 (at(gd.get(cur), "二线客诉处理组") or 0) + (at(gd.get(cur), "常规工单处理组") or 0) \
                 if gd.get(cur) else 0
        hl_sum = (((at(hl.get(cur), "转接一组") or 0) + (at(hl.get(cur), "转接二组") or 0) +
                   (at(hl.get(cur), "贷后转接组") or 0)) if hl.get(cur) else 0)
        card_out = {
            "工单量": gd_sum, "转接量": hl_sum,
            "签入": (at(cg.get(cur), "签入") or 0) + (at(dh.get(cur), "签入") or 0),
            "空闲": (at(cg.get(cur), "空闲") or 0) + (at(dh.get(cur), "空闲") or 0),
        }
    else:
        card_out = None

    # --- 两张明细表（行=9..21）---
    def _row(h):
        def g(grp, col):
            return inbound[grp].get(col, {}).get(h) if grp in inbound else outbound[grp].get(col, {}).get(h)
        return h
    in_rows, out_rows = [], []
    for h in h_other:
        in_rows.append({
            "小时": h,
            "热线_转人工量": inbound["热线"]["转人工量"].get(h),
            "热线_接通率": _rate(inbound["热线"]["转人工成功量"].get(h), inbound["热线"]["转人工量"].get(h)),
            "热线_签入": inbound["热线"]["签入"].get(h), "热线_空闲": inbound["热线"]["空闲"].get(h),
            "在线_转人工量": inbound["在线"]["转人工量"].get(h),
            "在线_接通率": _rate(inbound["在线"]["转人工成功量"].get(h), inbound["在线"]["转人工量"].get(h)),
            "在线_在线": inbound["在线"]["在线"].get(h),
            "12378_转人工量": inbound["12378"]["转人工量"].get(h),
            "12378_接通率": _rate(inbound["12378"]["转人工成功量"].get(h), inbound["12378"]["转人工量"].get(h)),
            "12378_签入": inbound["12378"]["签入"].get(h), "12378_空闲": inbound["12378"]["空闲"].get(h),
            "12378_12378回访组": inbound["12378"]["12378回访组"].get(h),
        })
        out_rows.append({
            "小时": h,
            "常规二线_工单量": outbound["常规二线"]["工单量"].get(h), "常规二线_转接量": outbound["常规二线"]["转接量"].get(h),
            "常规二线_签入": outbound["常规二线"]["签入"].get(h), "常规二线_空闲": outbound["常规二线"]["空闲"].get(h),
            "贷后二线_工单量": outbound["贷后二线"]["工单量"].get(h), "贷后二线_转接量": outbound["贷后二线"]["转接量"].get(h),
            "贷后二线_签入": outbound["贷后二线"]["签入"].get(h), "贷后二线_空闲": outbound["贷后二线"]["空闲"].get(h),
            "二线客诉_工单量": outbound["二线客诉"]["工单量"].get(h),
            "常规工单_工单量": outbound["常规工单"]["工单量"].get(h),
        })

    return {
        "date": date_str, "current_hour": cur,
        "inbound": inbound, "outbound": outbound,
        "card": {"inbound": card_in, "outbound": card_out},
        "tables": {"inbound": in_rows, "outbound": out_rows},
    }

def _rate(num, den):
    if num is None or den is None or den == 0:
        return None
    return round(num / den, 4)
```

- [ ] **Step 4: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: `dashboard_queries OK`

---

### Task 7: 月视图组装 `build_month`

**Files:**
- Modify: `dashboard/queries.py`（追加 `build_month`）
- Test: `tests/test_dashboard_queries.py`（追加测试）

**Interfaces:**
- Consumes: `daily_latest`、`daily_avg`（Task 3）、CSV 预测量（按日）、`forecast_12378`（按日复用）。
- Produces: `build_month(ym, data_dir="data") -> dict`，结构同 `build_day` 但 `hours` 换成 `days`（该月各日），累积量取每日最新、瞬时量取每日均值；卡片累积量求和、瞬时量取日均。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_dashboard_queries.py`:
```python
def test_build_month():
    d = tempfile.mkdtemp()
    _seed(d, "热线", ["时间","转人工量","接通量","排队量","累计呼入量","外呼量","外呼接通量"], [
        ("2026-07-01 21:00", 100, 95, 0, 200, 0, 0),
        ("2026-07-02 21:00", 200, 190, 0, 400, 0, 0),
    ])
    _seed(d, "热线明细", ["时间","签入","通话","空闲","离席","话后","振铃","置忙"], [
        ("2026-07-01 09:05", 10, 0, 0, 0, 0, 0, 0),
        ("2026-07-01 21:00", 50, 0, 0, 0, 0, 0, 0),   # 7/1 瞬时均值 (10+50)/2=30，收盘累计用 latest
        ("2026-07-02 21:00", 60, 0, 0, 0, 0, 0, 0),
    ])
    res = Q.build_month("2026-07", d)
    # 累积量取每日最新
    assert res["inbound"]["热线"]["转人工量"][1] == 100, res["inbound"]["热线"]["转人工量"]
    assert res["inbound"]["热线"]["转人工量"][2] == 200
    # 瞬时量取每日均值
    assert res["inbound"]["热线"]["签入"][1] == 30.0, res["inbound"]["热线"]["签入"]
    # 卡片：累积量求和
    assert res["card"]["inbound"]["转人工量"] == 300, res["card"]["inbound"]
    # days 列表
    assert 1 in res["inbound"]["热线"]["days"] and 2 in res["inbound"]["热线"]["days"]
```
在 `main()` 中追加 `test_build_month()`。

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: FAIL（`AttributeError: build_month`）

- [ ] **Step 3: 实现 `build_month`**

追加到 `dashboard/queries.py`:
```python
def _forecast_daily(data_dir, line, ym):
    """{日: 当日最大累计预估量}。"""
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["线路"] == line and r["时间"][:7] == ym:
                out[int(r["时间"][8:10])] = int(r["累计预估量"])  # 升序覆盖
    return out

def _forecast_12378_daily(data_dir, ym):
    """{日: 7天前当日收盘累计转人工量}。"""
    from datetime import date as _d, timedelta as _t
    y, m = (int(x) for x in ym.split("-"))
    out = {}
    for dd in range(1, 32):
        try:
            cur = _d(y, m, dd)
        except ValueError:
            break
        prev = (cur - _t(days=7)).strftime("%Y-%m-%d")
        snap = hourly_latest(data_dir, "12378", prev)
        if snap:
            mx = max(snap.values(), key=lambda r: r["时间"])
            out[dd] = mx["转人工量"]
    return out

def build_month(ym, data_dir="data"):
    days = list(range(1, 32))  # 模板按实际有数据展示；缺失日为 None

    def latest(name):
        return daily_latest(data_dir, name, ym)

    def avg(name):
        return daily_avg(data_dir, name, ym)

    rx_l, rx_a = latest("热线"), avg("热线明细")
    im_l = latest("在线")
    z_l, z_a = latest("12378"), avg("12378明细")
    gd_l = latest("工单明细"); hl_l = latest("会话记录")
    cg_a, dh_a = avg("常规"), avg("贷后")

    fc_rx = _forecast_daily(data_dir, "热线", ym)
    fc_im = _forecast_daily(data_dir, "在线", ym)
    fc_z = _forecast_12378_daily(data_dir, ym)

    def col(src, c):
        return {dd: (src.get(dd, {}) or {}).get(c) for dd in days}

    inbound = {
        "热线": {"days": days, "预测量": fc_rx,
                 "转人工量": col(rx_l, "转人工量"), "转人工成功量": col(rx_l, "接通量"),
                 "签入": col(rx_a, "签入"), "空闲": col(rx_a, "空闲")},
        "在线": {"days": days, "预测量": fc_im,
                 "转人工量": col(im_l, "转人工量"),
                 "转人工成功量": {dd: ((im_l.get(dd, {}) or {}).get("转人工量") or 0) -
                                   ((im_l.get(dd, {}) or {}).get("转人工失败") or 0)
                                   if im_l.get(dd) else None for dd in days},
                 "在线": {dd: (im_l.get(dd, {}) or {}).get("在线") for dd in days}},
        "12378": {"days": days, "预测量": fc_z,
                  "转人工量": col(z_l, "转人工量"), "转人工成功量": col(z_l, "接通量"),
                  "签入": col(z_a, "签入"), "空闲": col(z_a, "空闲"),
                  "12378回访组": col(gd_l, "12378回访组")},
    }
    outbound = {
        "常规二线": {"days": days, "工单量": col(gd_l, "回访组一组"),
                   "转接量": {dd: ((hl_l.get(dd, {}) or {}).get("转接一组") or 0) +
                                 ((hl_l.get(dd, {}) or {}).get("转接二组") or 0)
                                 if hl_l.get(dd) else None for dd in days},
                   "签入": col(cg_a, "签入"), "空闲": col(cg_a, "空闲")},
        "贷后二线": {"days": days, "工单量": col(gd_l, "贷后回访组"),
                   "转接量": col(hl_l, "贷后转接组"), "签入": col(dh_a, "签入"), "空闲": col(dh_a, "空闲")},
        "二线客诉": {"days": days, "工单量": col(gd_l, "二线客诉处理组")},
        "常规工单": {"days": days, "工单量": col(gd_l, "常规工单处理组")},
    }

    def s(d):
        vals = [v for v in d.values() if v is not None]
        return sum(vals) if vals else None

    def avg_of(d):
        vals = [v for v in d.values() if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    pred = s({dd: (fc_rx.get(dd, 0) or 0) + (fc_im.get(dd, 0) or 0) + (fc_z.get(dd, 0) or 0) for dd in days})
    zrg = (s(inbound["热线"]["转人工量"]) or 0) + (s(inbound["在线"]["转人工量"]) or 0) + (s(inbound["12378"]["转人工量"]) or 0)
    succ = (s(inbound["热线"]["转人工成功量"]) or 0) + (s(inbound["在线"]["转人工成功量"]) or 0) + (s(inbound["12378"]["转人工成功量"]) or 0)
    card_in = {
        "预测量": pred, "时段预测量": None,
        "流入率": round(zrg / pred, 4) if pred else None,
        "转人工量": zrg, "转人工成功量": succ,
        "接通率": round(succ / zrg, 4) if zrg else None,
    }
    card_out = {
        "工单量": (s(outbound["常规二线"]["工单量"]) or 0) + (s(outbound["贷后二线"]["工单量"]) or 0) +
                 (s(outbound["二线客诉"]["工单量"]) or 0) + (s(outbound["常规工单"]["工单量"]) or 0),
        "转接量": (s(outbound["常规二线"]["转接量"]) or 0) + (s(outbound["贷后二线"]["转接量"]) or 0),
        "签入": avg_of(outbound["常规二线"]["签入"]), "空闲": avg_of(outbound["常规二线"]["空闲"]),
    }

    in_rows = [{"小时": dd, **{f"热线_{k}": inbound["热线"][k].get(dd) for k in ["转人工量","签入","空闲"]},
                **{f"在线_在线": inbound["在线"]["在线"].get(dd)}} for dd in days]
    out_rows = [{"小时": dd, **{f"{g}_工单量": outbound[g]["工单量"].get(dd) for g in outbound}} for dd in days]

    return {"date": ym, "current_hour": None, "days": days,
            "inbound": inbound, "outbound": outbound,
            "card": {"inbound": card_in, "outbound": card_out},
            "tables": {"inbound": in_rows, "outbound": out_rows}}
```

- [ ] **Step 4: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_queries.py`
Expected: `dashboard_queries OK`

---

### Task 8: 图表构建 `charts.py`

**Files:**
- Create: `dashboard/charts.py`
- Test: `tests/test_dashboard_charts.py`

**Interfaces:**
- Consumes: `build_day`/`build_month` 返回的 group dict（含 `hours` 或 `days` + 各指标 {键:值}）。
- Produces:
  - `inbound_chart(name, group) -> pyecharts.charts.Bar`：柱=预测量/转人工量/转人工成功量（12378 加"12378回访组"），折线=签入(实线)/空闲(虚线)；在线仅"在线"一根线。双 Y 轴（量左、人数右）。
  - `outbound_chart(name, group) -> pyecharts.charts.Bar`：堆积柱下=转接量、上=工单量；有坐席组加签入(实线)/空闲(虚线)。

- [ ] **Step 1: 写失败测试**

`tests/test_dashboard_charts.py`:
```python
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import charts as C

def _names(bar):
    return [s["name"] for s in bar.options["series"]]

def test_inbound_charts():
    g = {"hours": [9, 10], "预测量": {9: 100, 10: 200}, "转人工量": {9: 80, 10: 150},
         "转人工成功量": {9: 75, 10: 140}, "签入": {9: 50, 10: 55}, "空闲": {9: 10, 10: 12}}
    bar = C.inbound_chart("热线", g)
    n = _names(bar)
    assert "预测量" in n and "转人工量" in n and "转人工成功量" in n, n
    assert "签入" in n and "空闲" in n, n
    # 在线只一根"在线"线
    g2 = {"hours": [9, 10], "预测量": {9: 1}, "转人工量": {9: 1}, "转人工成功量": {9: 1}, "在线": {9: 5}}
    n2 = _names(C.inbound_chart("在线", g2))
    assert "在线" in n2 and "签入" not in n2 and "空闲" not in n2, n2
    # 12378 多 12378回访组
    g3 = {"hours": [8, 9], "预测量": {8: 1}, "转人工量": {8: 1}, "转人工成功量": {8: 1},
          "签入": {8: 1}, "空闲": {8: 1}, "12378回访组": {8: 3}}
    n3 = _names(C.inbound_chart("12378", g3))
    assert "12378回访组" in n3, n3

def test_outbound_charts():
    g = {"hours": [9, 10], "工单量": {9: 10, 10: 20}, "转接量": {9: 5, 10: 8},
         "签入": {9: 12, 10: 12}, "空闲": {9: 0, 10: 1}}
    bar = C.outbound_chart("常规二线", g)
    n = _names(bar)
    assert "转接量" in n and "工单量" in n and "签入" in n and "空闲" in n, n
    # 二线客诉：仅工单量，无转接量/折线
    g2 = {"hours": [9, 10], "工单量": {9: 1, 10: 2}}
    n2 = _names(C.outbound_chart("二线客诉", g2))
    assert n2 == ["工单量"], n2

def main():
    test_inbound_charts()
    test_outbound_charts()
    print("dashboard_charts OK")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_charts.py`
Expected: FAIL（`ModuleNotFoundError: No module named 'dashboard.charts'`）

- [ ] **Step 3: 实现 `charts.py`**

`dashboard/charts.py`:
```python
# -*- coding: utf-8 -*-
"""pyecharts 图表：接听(柱+折线) 与 外呼(堆积柱+折线)。"""
from pyecharts.charts import Bar, Line
from pyecharts import options as opts

def _xs(group):
    xs = group.get("hours") or group.get("days") or []
    if "hours" in group:
        return xs, [f"{h}点" for h in xs]
    return xs, [f"{d}日" for d in xs]

def _ser(group, key, xs):
    m = group.get(key, {})
    return [m.get(x) for x in xs]

def _base_bar(xlabels):
    return Bar(init_opts=opts.InitOpts(width="100%", height="360px")).add_xaxis(xlabels)

def _dual_axis(bar):
    bar.set_global_opts(
        legend_opts=opts.LegendOpts(pos_top="2%"),
        yaxis_opts=opts.AxisOpts(name="量"),
        xaxis_opts=opts.AxisOpts(axislabel_opts=opts.LabelOpts(rotate=30)),
    )
    bar.extend_axis(yaxis=opts.AxisOpts(name="人数"))

def _line_series(line, name, vals, idx, dash):
    line.add_yaxis(name, vals, yaxis_index=idx,
                   linestyle_opts=opts.LineStyleOpts(type_="dashed" if dash else "solid"),
                   label_opts=opts.LabelOpts(is_show=False), symbol="circle", symbol_size=6)

def inbound_chart(name, group):
    xs, xlabels = _xs(group)
    bar = _base_bar(xlabels)
    bar.add_yaxis("预测量", _ser(group, "预测量", xs), label_opts=opts.LabelOpts(is_show=False))
    bar.add_yaxis("转人工量", _ser(group, "转人工量", xs), label_opts=opts.LabelOpts(is_show=False))
    bar.add_yaxis("转人工成功量", _ser(group, "转人工成功量", xs), label_opts=opts.LabelOpts(is_show=False))
    if name == "12378":
        bar.add_yaxis("12378回访组", _ser(group, "12378回访组", xs), label_opts=opts.LabelOpts(is_show=False))
    _dual_axis(bar)
    line = Line().add_xaxis(xlabels)
    if name == "在线":
        _line_series(line, "在线", _ser(group, "在线", xs), 1, False)
    else:
        _line_series(line, "签入", _ser(group, "签入", xs), 1, False)
        _line_series(line, "空闲", _ser(group, "空闲", xs), 1, True)
    bar.overlap(line)
    return bar

def outbound_chart(name, group):
    xs, xlabels = _xs(group)
    bar = _base_bar(xlabels)
    has_transfer = "转接量" in group
    if has_transfer:
        bar.add_yaxis("转接量", _ser(group, "转接量", xs), stack="total", label_opts=opts.LabelOpts(is_show=False))
        bar.add_yaxis("工单量", _ser(group, "工单量", xs), stack="total", label_opts=opts.LabelOpts(is_show=False))
    else:
        bar.add_yaxis("工单量", _ser(group, "工单量", xs), label_opts=opts.LabelOpts(is_show=False))
    has_seat = "签入" in group
    if has_seat:
        _dual_axis(bar)
        line = Line().add_xaxis(xlabels)
        _line_series(line, "签入", _ser(group, "签入", xs), 1, False)
        _line_series(line, "空闲", _ser(group, "空闲", xs), 1, True)
        bar.overlap(line)
    else:
        bar.set_global_opts(legend_opts=opts.LegendOpts(pos_top="2%"),
                            yaxis_opts=opts.AxisOpts(name="量"))
    return bar
```

- [ ] **Step 4: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_charts.py`
Expected: `dashboard_charts OK`

---

### Task 9: Flask 路由与模板

**Files:**
- Modify: `dashboard/app.py`（替换 Task 1 的最小版本）
- Create: `dashboard/templates/dashboard.html`
- Test: `tests/test_dashboard_app.py`（追加路由测试）

**Interfaces:**
- Consumes: `queries.build_day`/`build_month`、`charts.inbound_chart`/`outbound_chart`。
- Produces: 路由 `/`（重定向当天日视图）、`/?view=day&date=`、`/?view=month&date=`，渲染 `dashboard.html`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_dashboard_app.py`（保留 Task 1 的 `index` 测试或替换）:
```python
def test_routes():
    from dashboard.app import app
    client = app.test_client()
    r = client.get("/")
    assert r.status_code in (301, 302), r.status_code
    r = client.get("/?view=day&date=2026-07-27")
    assert r.status_code == 200, r.status_code
    assert b"chart_in_12378" in r.data, "缺少 12378 图容器"
    assert b"chart_out_" in r.data
    r = client.get("/?view=month&date=2026-07")
    assert r.status_code == 200, r.status_code

def main():
    test_routes()
    print("dashboard_app OK")
```
（删除 Task 1 中的旧 `main()`/`index` 断言，改用上述 `main`。）

- [ ] **Step 2: 运行测试，期望失败**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_app.py`
Expected: FAIL（`b"chart_in_12378" not in data` 或路由未实现）

- [ ] **Step 3: 实现 `app.py`**

替换 `dashboard/app.py`:
```python
# -*- coding: utf-8 -*-
"""承接情况看板 Flask 应用。"""
import datetime
from flask import Flask, render_template, request, redirect, url_for
from dashboard import queries, charts as ch

app = Flask(__name__)
DATA_DIR = "data"

def _today():
    return datetime.date.today().strftime("%Y-%m-%d")

@app.route("/")
def index():
    view = request.args.get("view", "day")
    date = request.args.get("date")
    if view == "month":
        date = date or datetime.date.today().strftime("%Y-%m")
        data = queries.build_month(date, DATA_DIR)
    else:
        date = date or _today()
        data = queries.build_day(date, DATA_DIR)
    chart_list = []
    for name, g in data["inbound"].items():
        chart_list.append(("in_" + name, ch.inbound_chart(name, g).dump_options_with_quotes()))
    for name, g in data["outbound"].items():
        chart_list.append(("out_" + name, ch.outbound_chart(name, g).dump_options_with_quotes()))
    return render_template("dashboard.html", view=view, date=date, data=data, charts=chart_list)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
```

- [ ] **Step 4: 实现模板**

`dashboard/templates/dashboard.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="300">
<title>承接情况看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
body { font-family: "Microsoft YaHei", sans-serif; margin: 16px; background: #f5f6fa; }
h2 { border-left: 4px solid #409eff; padding-left: 8px; }
.card { display: inline-block; background: #fff; border-radius: 6px; padding: 12px 16px;
        margin: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .v { font-size: 22px; font-weight: bold; color: #303133; }
.card .l { font-size: 12px; color: #909399; }
.chart { background: #fff; border-radius: 6px; padding: 8px; margin: 8px 0; height: 400px; }
table { border-collapse: collapse; background: #fff; width: 100%; font-size: 12px; }
th, td { border: 1px solid #ebeef5; padding: 4px 6px; text-align: center; }
th { background: #f0f2f5; position: sticky; top: 0; }
td:first-child, th:first-child { position: sticky; left: 0; background: #fff; font-weight: bold; }
.controls { background: #fff; padding: 10px; border-radius: 6px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="controls">
  <form method="get">
    <select name="view">
      <option value="day" {% if view=='day' %}selected{% endif %}>日视图</option>
      <option value="month" {% if view=='month' %}selected{% endif %}>月视图</option>
    </select>
    <input type="{% if view=='month' %}month{% else %}date{% endif %}" name="date" value="{{ date }}">
    <button type="submit">查看</button>
    <span style="margin-left:16px;color:#909399;">数据时间：{{ date }}{% if data.current_hour is not none %} {{ data.current_hour }}点{% endif %}</span>
  </form>
</div>

<h2>第一部分 整体累计</h2>
<div>
  {% for cat, title in [('inbound','接听（呼入）'),('outbound','外呼')] %}
  <div style="display:inline-block;vertical-align:top;">
    <div style="font-weight:bold;margin:4px;">{{ title }}</div>
    {% if data.card[cat] %}
      {% for k, v in data.card[cat].items() %}
      <div class="card"><div class="v">{{ v if v is not none else '—' }}</div><div class="l">{{ k }}</div></div>
      {% endfor %}
    {% else %}<div class="card"><div class="l">无数据</div></div>{% endif %}
  </div>
  {% endfor %}
</div>

<h2>第二部分 接听图表</h2>
{% for name in ['热线','在线','12378'] %}
  <div style="font-weight:bold;margin:6px 0 -6px;">{{ name }}</div>
  <div id="chart_in_{{ name }}" class="chart"></div>
{% endfor %}

<h2>第三部分 外呼图表</h2>
{% for name in ['常规二线','贷后二线','二线客诉','常规工单'] %}
  <div style="font-weight:bold;margin:6px 0 -6px;">{{ name }}</div>
  <div id="chart_out_{{ name }}" class="chart"></div>
{% endfor %}

<h2>第四部分 时段明细表</h2>
{% for cat, title in [('inbound','接听明细'),('outbound','外呼明细')] %}
<div style="font-weight:bold;margin:8px 0 4px;">{{ title }}</div>
<div style="overflow:auto;max-height:480px;">
<table>
  <thead><tr>{% for k in data.tables[cat][0].keys() %}<th>{{ k }}</th>{% endfor %}</tr></thead>
  <tbody>
  {% for row in data.tables[cat] %}
    <tr>{% for k, v in row.items() %}<td>{{ '' if v is none else v }}</td>{% endfor %}</tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endfor %}

<script>
{% for cid, opt in charts %}
  (function(){
    var c = echarts.init(document.getElementById("chart_{{ cid }}"));
    c.setOption({{ opt|safe }});
    window.addEventListener("resize", function(){ c.resize(); });
  })();
{% endfor %}
</script>
</body>
</html>
```

- [ ] **Step 5: 运行测试，期望通过**

Run: `.\.venv\Scripts\python.exe tests\test_dashboard_app.py`
Expected: `dashboard_app OK`

---

### Task 10: 真实数据冒烟验证

**Files:**
- 无新增文件（手动验证）

- [ ] **Step 1: 跑全部看板测试**

Run:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_dashboard_queries.py; .\.venv\Scripts\python.exe tests\test_dashboard_charts.py; .\.venv\Scripts\python.exe tests\test_dashboard_app.py
```
Expected: 三个脚本分别输出 `dashboard_queries OK`、`dashboard_charts OK`、`dashboard_app OK`。

- [ ] **Step 2: 启动 Flask，浏览器验证当天日视图**

Run（后台启动）:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m dashboard.app
```
打开浏览器 `http://127.0.0.1:5000/?view=day&date=2026-07-27`，人工核对：
- 第一部分卡片有数值（接听 6 指标 + 外呼 4 指标）。
- 第二部分 3 张图：热线/在线 9–21 点、12378 8–21 点（07-27 是周一，工作日）；在线图只有一根"在线"线；12378 图有"12378回访组"柱。
- 第三部分 4 张图：常规二线/贷后二线堆积柱（下转接量、上工单量）+ 折线；二线客诉/常规工单单柱无折线。
- 第四部分两张表，行 9–21 点。
- 切到月视图 `?view=month&date=2026-07`：图表 X 轴为日期，外呼部分 07-24 前为空（符合 §7 限制）。
- 改日期为 `2026-07-07`：12378 预测量应显示无数据/0（符合 §7 限制 2）。

- [ ] **Step 3: 停止服务，完成**

停止后台 Flask（Ctrl+C 或结束进程）。看板交付完成。

---

## Self-Review

**1. Spec coverage：**
- §2 架构（Flask+pyecharts、只读 db、分进程、meta 刷新）-> Task 1/9。✓
- §3 路由与入口（`/`、day、month、日期选择、视图切换）-> Task 9。✓
- §4.1 组定义与指标来源 -> Task 6 `build_day`（每组指标来源与 spec 表一致）。✓
- §4.2 聚合规则（每小时最新、每日最新/均值）-> Task 2/3/6/7。✓
- §4.3 预测量（CSV、12378 七天前、时段预测量）-> Task 4/5/6。✓
- §5.1 第一部分卡片（接听 6 指标、外呼量+签入空闲）-> Task 6 `card` + Task 9 模板。✓
- §5.2 第二部分（柱+折线、在线单线、12378 加回访组柱、X 轴 8–21/9–18、9–21）-> Task 6 `_hours_for` + Task 8 `inbound_chart` + Task 9。✓
- §5.3 第三部分（堆积柱下转接量/上工单量、单柱组、折线）-> Task 8 `outbound_chart`。✓
- §5.4 第四部分两张表（接听/外呼、行 9–21）-> Task 6 表格 + Task 9 模板。✓
- §6 月视图（同结构按日、累积量求和/瞬时量日均）-> Task 7 + Task 9。✓
- §7 数据限制（无数据标注）-> 模板 `—`/空单元格；冒烟 Task 10 验证。✓

**2. Placeholder scan：** 无 TBD/TODO；每步含完整代码或确切命令。✓

**3. Type consistency：** `hourly_latest`/`daily_latest`/`daily_avg`/`load_forecast`/`forecast_increment`/`forecast_12378`/`build_day`/`build_month` 在定义与调用处签名一致；group dict 的键（`hours`/`days`/各指标）在 `charts.py` 与 `build_day`/`build_month` 间一致。✓

**注意点：** Task 6 `build_day` 测试中 12378 预测量因同目录 7 天前 12378.db 为空取 0，已在测试注释说明；7 天前映射逻辑由 Task 5 独立覆盖。

