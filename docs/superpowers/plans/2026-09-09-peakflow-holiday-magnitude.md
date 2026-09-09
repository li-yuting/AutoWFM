# PeakFlow 量级预测纳入法定节假日/补班 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 peakflow 未来 30 天预测按国家法定日历调整每日量级：休假/三薪日套"周日档"走低，补班日套"周一档"回升，总览 sheet 标注当日类型。

**Architecture:** 在 `peakflow/models.py` 的 `forecast_ratio` 星期因子查表处按 `data/节假日.csv` 覆盖档位（数据驱动、复用历史周日/周一季节因子，零新增系数）；日历查表 `lru_cache` 每进程读文件一次。`forecast.py`、`main.py` 零改动。

**Tech Stack:** Python 3.14 + pandas/numpy（仓库 `.venv`），plain-assert 测试（无 pytest）。

## Global Constraints

- 所有命令用仓库 `.venv` 解释器：`.\.venv\Scripts\python.exe`，且 `$env:PYTHONIOENCODING="utf-8"`。
- 测试是 plain-assert 脚本，直接运行：`.\.venv\Scripts\python.exe tests\test_xxx.py`；无 pytest、无 fixtures。
- 语义映射（来自设计 spec §3.1）：`法定休假`/`法定三薪` → 周日档 `sidx.get(6, 0.0)`；`法定补班` → 周一档 `sidx.get(0, 0.0)`；未标记/未知类型/文件缺失 → 自身星期档（现状行为，逐位一致）。
- **客户量不做任何调整**（存量客群恒定）；只改 `forecast_ratio` 未来日期循环里的档位查表，**训练段（`_seasonal_index`/dom 残差）不改**。
- 日历文件 `data/节假日.csv`：UTF-8（读用 `utf-8-sig` 容忍 BOM），两列表头 `日期,类型`；解析失败/文件缺失 → 空映射 + 至多一行警告，不抛错。
- 总览「备注」列追加在**最右列**（不位移既有列，避免破坏现读取方）。
- Commit 遵循 Conventional Commits，中文描述（如 `feat(peakflow): ...`）；LF→CRLF warning 属正常，可忽略。
- 设计文档：`docs/superpowers/specs/2026-09-09-peakflow-holiday-magnitude-design.md`（§3.3 文件改动表、§5 验证口径、§6 已知局限）。

---

### Task 1: 日历文件与配置常量

**Files:**
- Create: `data/节假日.csv`
- Modify: `peakflow/config.py:24-25`（`DATA_DIR`/`OUTPUT_DIR` 定义之后）

**Interfaces:**
- Consumes: 无（设计 spec §2 提供的 2026 全年 39 行节假日清单）。
- Produces: `config.HOLIDAY_FILE = Path`（指向 `data/节假日.csv`），Task 2 的 `_holiday_map()` 读取它。

- [ ] **Step 1: 先加配置常量并跑既有配置测试确认不破坏**

`peakflow/config.py` 当前第 24-25 行：

```python
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
```

改为：

```python
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
HOLIDAY_FILE = DATA_DIR / "节假日.csv"
```

- [ ] **Step 2: 创建 `data/节假日.csv`（2026 全年，用户提供 39 行，UTF-8 无 BOM）**

文件完整内容（含表头，共 40 行）：

```csv
日期,类型
2026-01-01,法定三薪
2026-01-02,法定休假
2026-01-03,法定休假
2026-01-04,法定补班
2026-02-14,法定补班
2026-02-15,法定休假
2026-02-16,法定三薪
2026-02-17,法定三薪
2026-02-18,法定三薪
2026-02-19,法定三薪
2026-02-20,法定休假
2026-02-21,法定休假
2026-02-22,法定休假
2026-02-23,法定休假
2026-02-28,法定补班
2026-04-04,法定休假
2026-04-05,法定三薪
2026-04-06,法定休假
2026-05-01,法定三薪
2026-05-02,法定三薪
2026-05-03,法定休假
2026-05-04,法定休假
2026-05-05,法定休假
2026-05-09,法定补班
2026-06-19,法定三薪
2026-06-20,法定休假
2026-06-21,法定休假
2026-09-20,法定补班
2026-09-25,法定三薪
2026-09-26,法定休假
2026-09-27,法定休假
2026-10-01,法定三薪
2026-10-02,法定三薪
2026-10-03,法定三薪
2026-10-04,法定休假
2026-10-05,法定休假
2026-10-06,法定休假
2026-10-07,法定休假
2026-10-10,法定补班
```

- [ ] **Step 3: 验证文件可读、行数与类型集合正确**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import pandas as pd; df=pd.read_csv('data/节假日.csv',dtype=str); print(len(df), sorted(df['类型'].unique())); assert len(df)==39; assert set(df['类型'])=={'法定休假','法定三薪','法定补班'}; print('OK')"
```

Expected: `39 ['法定休假', '法定三薪', '法定补班']` 且末尾 `OK`。

- [ ] **Step 4: 回归配置测试**

Run: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_config.py`
Expected: `test_peakflow_config OK`

- [ ] **Step 5: Commit**

```powershell
git add data/节假日.csv peakflow/config.py
git commit -m "feat(peakflow): 新增国家法定节假日日历文件与配置常量"
```

---

### Task 2: models.py 节假日档位覆盖（TDD）

**Files:**
- Modify: `peakflow/models.py:1-6`（imports）、`:8`（`from peakflow import config` 之后插入 helpers）、`:98`（`v = tf[i] + sidx.get(d.weekday(), 0.0)` 一行）
- Test: `tests/test_peakflow_models.py`（追加 helpers + 4 个测试 + main() 注册）

**Interfaces:**
- Consumes: `config.HOLIDAY_FILE`（Task 1）、`_seasonal_index`（现有，返回 `pd.Series`，index 0-6 为 weekday）。
- Produces: 模块内 `_holiday_map() -> dict`、`_day_band(sidx, d) -> float`；公共 `holiday_type(d) -> str`（Task 3 report 与测试用）；`forecast_ratio` 行为升级（签名不变）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_peakflow_models.py` 顶部 imports（现有第 1-10 行）后追加临时文件 helpers：

```python
def _with_holiday(rows, fn):
    """把 rows 写成临时节假日.csv 并指向 config.HOLIDAY_FILE，执行 fn 后还原。"""
    import shutil
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".holiday_tmp")
    os.makedirs(tmp, exist_ok=True)
    path = os.path.join(tmp, "节假日.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("日期,类型\n")
        for d, t in rows:
            f.write(f"{d},{t}\n")
    orig = config.HOLIDAY_FILE
    config.HOLIDAY_FILE = path
    models._holiday_map.cache_clear()
    try:
        return fn()
    finally:
        config.HOLIDAY_FILE = orig
        models._holiday_map.cache_clear()
        shutil.rmtree(tmp, ignore_errors=True)


def _ratio_with_weekday_season(n_days=84):
    """确定性周季节占比序列：周日最低(0.62)，周一最高(1.00)。"""
    idx = pd.date_range("2026-06-01", periods=n_days)  # 2026-06-01 为周一
    mult = {0: 1.00, 1: 1.05, 2: 1.02, 3: 0.98, 4: 0.92, 5: 0.78, 6: 0.62}
    vals = 0.05 * np.array([mult[w] for w in idx.weekday])
    return pd.Series(vals, index=idx)
```

然后追加 4 个测试函数（在文件末尾 `def main():` 之前）：

```python
def test_ratio_holiday_monday_drops_toward_sunday():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)                       # 8/24 起 21 天
    d_mon = next(d for d in fd if d.weekday() == 0)  # 第一个周一
    i = fd.index(d_mon)
    plain = models.forecast_ratio(r, fd)

    def run_with_holiday():
        out = models.forecast_ratio(r, fd)
        assert out[i] < plain[i] * 0.9, "节假日周一应明显低于普通周一"
        assert 0.4 < out[i] / plain[i] < 0.95, "节假日周一应接近周日档(≈0.6x)"
        return out

    _with_holiday([(d_mon.date().isoformat(), "法定休假")], run_with_holiday)


def test_ratio_makeup_saturday_rises_toward_monday():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)
    d_sat = next(d for d in fd if d.weekday() == 5)  # 第一个周六
    i = fd.index(d_sat)
    plain = models.forecast_ratio(r, fd)

    def run_with_makeup():
        out = models.forecast_ratio(r, fd)
        assert out[i] > plain[i] * 1.05, "补班周六应明显高于普通周六"
        return out

    _with_holiday([(d_sat.date().isoformat(), "法定补班")], run_with_makeup)


def test_ratio_unflagged_dates_identical_with_and_without_calendar():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)  # 2026-08-24..09-13，无一落在真实/临时日历

    def run():
        return models.forecast_ratio(r, fd)

    base = run()  # 此刻 config.HOLIDAY_FILE 指向真实仓库文件（或缺失），fd 内无标记日
    # 临时日历只含无关日期 → 输出与无日历完全一致
    out = _with_holiday([("2030-01-01", "法定休假")], run)
    assert np.allclose(base, out, atol=1e-12)


def test_ratio_missing_holiday_file_falls_back():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)

    def run():
        return models.forecast_ratio(r, fd)

    base = models.forecast_ratio(r, fd)

    def run_with_missing():
        return models.forecast_ratio(r, fd)

    import tempfile
    orig = config.HOLIDAY_FILE
    config.HOLIDAY_FILE = os.path.join(tempfile.gettempdir(), "definitely_missing_holidays.csv")
    models._holiday_map.cache_clear()
    try:
        out = run_with_missing()
    finally:
        config.HOLIDAY_FILE = orig
        models._holiday_map.cache_clear()
    assert np.allclose(base, out, atol=1e-12), "文件缺失应回退为现状行为"
```

并在 `main()` 中追加注册（现有 main 末尾 `test_mean_recent_ratio_clamps()` 之后）：

```python
    test_ratio_holiday_monday_drops_toward_sunday()
    test_ratio_makeup_saturday_rises_toward_monday()
    test_ratio_unflagged_dates_identical_with_and_without_calendar()
    test_ratio_missing_holiday_file_falls_back()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_models.py`
Expected: 报 `AttributeError: module 'peakflow.models' has no attribute '_holiday_map'`（或类似），红色失败。

- [ ] **Step 3: 实现**

`peakflow/models.py` 顶部现有：

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from peakflow import config
```

改为：

```python
from __future__ import annotations

import functools

import numpy as np
import pandas as pd

from peakflow import config

# 国家法定节假日：休息类（休假/三薪）→ 周日档（一周最低）；补班（上班）→ 周一档
_REST_DAYS = {"法定休假", "法定三薪"}
_MAKEUP_DAYS = {"法定补班"}


@functools.lru_cache(maxsize=1)
def _holiday_map() -> dict:
    """data/节假日.csv → {date: 类型}。文件缺失或解析失败返回 {}（行为与无日历一致）。"""
    path = config.HOLIDAY_FILE
    if not path.is_file():
        return {}
    mapping = {}
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        for _, r in df.iterrows():
            try:
                d = pd.Timestamp(r["日期"]).date()
            except (KeyError, TypeError, ValueError):
                continue
            t = str(r["类型"]).strip()
            if t in _REST_DAYS or t in _MAKEUP_DAYS:
                mapping[d] = t
    except Exception:
        print(f"警告: 节假日文件解析失败，本次忽略: {path}")
        return {}
    return mapping


def holiday_type(d) -> str:
    """该日期在节假日.csv 中的类型（法定休假/法定三薪/法定补班）；无则 ''。"""
    return _holiday_map().get(pd.Timestamp(d).date(), "")


def _day_band(sidx: pd.Series, d) -> float:
    """未来日期的星期档位：默认自身星期档；法定休息日→周日档，法定补班→周一档。"""
    t = _holiday_map().get(pd.Timestamp(d).date())
    if t in _REST_DAYS:
        return float(sidx.get(6, 0.0))
    if t in _MAKEUP_DAYS:
        return float(sidx.get(0, 0.0))
    return float(sidx.get(d.weekday(), 0.0))
```

`forecast_ratio` 未来日期循环（当前第 96-101 行）：

```python
    out = np.empty(len(future_dates))
    for i in range(len(future_dates)):
        d = future_dates[i]
        v = tf[i] + sidx.get(d.weekday(), 0.0)
        if dom_idx is not None:
            v += float(dom_idx.get(d.day, 0.0))
        out[i] = v
    return np.clip(np.exp(out), 1e-9, 1.0)
```

其中 `v = tf[i] + sidx.get(d.weekday(), 0.0)` 一行改为：

```python
        v = tf[i] + _day_band(sidx, d)
```

训练段（`_seasonal_index`、dom 残差、`wd = pd.Series([sidx.get(d.weekday(), ...)` 那几处）**一律不改**。

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_models.py`
Expected: `test_peakflow_models OK`

若 `test_ratio_unflagged_dates_identical_with_and_without_calendar` 依赖真实仓库 `data/节假日.csv` 存在：该测试第一个 `models.forecast_ratio(r, fd)` 用真实文件（fd=8/24..9/13，无 2026 标记日）→ 与空日历相等；随后临时日历只含 2030 无关日 → 相等。跑 CI（无真实文件时）同样成立（文件缺失→空映射）。

- [ ] **Step 5: 快速冒烟确认映射 API**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import pandas as pd; from peakflow.models import holiday_type; print(holiday_type(pd.Timestamp('2026-10-01')), holiday_type(pd.Timestamp('2026-10-10')), repr(holiday_type(pd.Timestamp('2026-10-08'))))"
```

Expected: `法定三薪 法定补班 ''`

- [ ] **Step 6: Commit**

```powershell
git add peakflow/models.py tests/test_peakflow_models.py
git commit -m "feat(peakflow): 量级预测按法定日历覆盖星期档位(休假→周日档/补班→周一档)"
```

---

### Task 3: 总览 sheet「备注」列标注节假日

**Files:**
- Modify: `peakflow/report.py:3-7`（imports）、`report.py:47-63`（`_overview_frame`）
- Test: `tests/test_peakflow_report.py`（追加 1 个测试 + main() 注册）

**Interfaces:**
- Consumes: `peakflow.models.holiday_type(d) -> str`（Task 2）。
- Produces: 总览 sheet 最右新增「备注」列，每日行填 法定休假/法定三薪/法定补班 或空；周汇总行空。既有列位置不变。

- [ ] **Step 1: 写失败测试**

在 `tests/test_peakflow_report.py` 追加（`test_report_has_totals_in_detail` 之后、`def main():` 之前）：

```python
def test_overview_notes_statutory_holidays():
    os.makedirs(_WS_TMP, exist_ok=True)
    import tempfile
    from peakflow import models
    try:
        df, sig = _sample()
        # 预测日期约 2026-07-06..07-15；标记首日为首个法定休假日
        fd_dates = sorted(set(df["date"]))
        target = fd_dates[0].date()
        tmp_dir = os.path.join(_WS_TMP, "holiday")
        os.makedirs(tmp_dir, exist_ok=True)
        path = os.path.join(tmp_dir, "节假日.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"日期,类型\n{target},法定三薪\n")
        orig = config.HOLIDAY_FILE
        config.HOLIDAY_FILE = path
        models._holiday_map.cache_clear()
        try:
            out = Path(_WS_TMP) / "out_note.xlsx"
            report.write_report(df, df, sig, {}, out)
            wb = load_workbook(out)
            ws = wb["总览"]
            headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
            assert headers[-1] == "备注", f"备注应是最右列, got {headers}"
            col_date = headers.index("日期") + 1
            col_note = len(headers)
            found = False
            for r in range(2, ws.max_row + 1):
                if ws.cell(row=r, column=col_date).value == target:
                    assert ws.cell(row=r, column=col_note).value == "法定三薪"
                    found = True
                elif ws.cell(row=r, column=col_date).value and "周汇总" not in str(ws.cell(row=r, column=col_date).value):
                    assert ws.cell(row=r, column=col_note).value in ("", None), f"{ws.cell(row=r, column=col_date).value} 不应有备注"
            assert found, "未找到标记日期行"
        finally:
            config.HOLIDAY_FILE = orig
            models._holiday_map.cache_clear()
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_overview_notes_statutory_holidays")
```

在 `main()` 中 `test_report_has_totals_in_detail()` 之后注册：

```python
    test_overview_notes_statutory_holidays()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_report.py`
Expected: `AssertionError: headers[-1] == "备注"` 失败（当前无该列）。

- [ ] **Step 3: 实现**

`peakflow/report.py` imports（当前第 3-7 行）：

```python
from pathlib import Path

import pandas as pd

from peakflow import config
from peakflow.forecast import total_by_date, weekly_summary
```

改为（在 config import 后加一行）：

```python
from peakflow import config
from peakflow.models import holiday_type
from peakflow.forecast import total_by_date, weekly_summary
```

`_overview_frame`（当前第 47-63 行）两处 `row = {...}` 各加一行（**在渠道列填完之后**，确保「备注」成为最右列）：

```python
    for r in daily.itertuples():
        row = {"日期": r.date.date(), "星期": _WEEKDAY_CN[r.date.weekday()]}
        for ch, suf in (("在线", "_o"), ("热线", "_h")):
            for label, o_col, _base, _k in _BANDS:
                row[f"{ch}{label}"] = round(getattr(r, f"{o_col}{suf}"))
        row["备注"] = holiday_type(r.date)
        rows.append(row)
    weekly = o_week.merge(h_week, on="week", suffixes=("_o", "_h"))
    for r in weekly.itertuples():
        row = {"日期": f"周汇总 {r.week}", "星期": ""}
        for ch, suf in (("在线", "_o"), ("热线", "_h")):
            for label, _o_col, base, k in _BANDS:
                row[f"{ch}{label}"] = round(getattr(r, f"{base}{suf}") * k)
        row["备注"] = ""
        rows.append(row)
```

即改动仅两处：daily 循环渠道列后加 `row["备注"] = holiday_type(r.date)`；weekly 循环渠道列后加 `row["备注"] = ""`。

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_report.py`
Expected: 输出含 `PASS test_write_report`、`PASS test_report_has_totals_in_detail`、`PASS test_overview_notes_statutory_holidays` 且末尾 `All tests passed!`。

- [ ] **Step 5: Commit**

```powershell
git add peakflow/report.py tests/test_peakflow_report.py
git commit -m "feat(peakflow): 总览 sheet 新增备注列标注法定节假日/补班"
```

---

### Task 4: 全量回归与端到端核对

**Files:**（无源码改动；仅验证）

- [ ] **Step 1: 全量单测回归**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"
Get-ChildItem tests\test_*.py | Where-Object { $_.Name -ne "smoke.py" } | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

Expected: 每个文件各自输出 OK/PASS/All tests passed，无红色异常退出。重点文件：`test_peakflow_config.py`、`test_peakflow_models.py`、`test_peakflow_forecast.py`、`test_peakflow_report.py`、`test_peakflow_main.py`、`test_peakflow_dashboard.py`、`test_peakflow_month.py`、`test_peakflow_fetch.py`、`test_peakflow_loader.py`。

- [ ] **Step 2: 端到端跑一次预测**

Run: `$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -m peakflow.main`
Expected: 两渠道各打印 `预测完成`，末尾 `完成: output\2026-09-09\预测_*.xlsx`。

- [ ] **Step 3: 核对总览关键日期**

Run（读刚生成的 Excel，打印关键日期行）：

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -c "import glob,pandas as pd,datetime as dt; f=glob.glob('output/2026-09-09/预测_*.xlsx')[-1]; df=pd.read_excel(f,sheet_name='总览'); key=['2026-09-20','2026-09-25','2026-10-01','2026-10-07','2026-09-19','2026-10-08']; print(df[(df['日期'].astype(str).isin(key))][['日期','星期','在线进线-中性','热线进线-中性','备注']].to_string(index=False))"
```

Expected 判读（量级相对比较，非绝对值）：
- `2026-09-20`（周六·补班）备注=法定补班，量级**高于** `2026-09-19`（普通周六）；
- `2026-10-01`（周四·三薪）备注=法定三薪，量级**低于** `2026-10-08`（普通周四，节后第一工作日）与 9/24（普通周四）；
- `2026-10-07`（周三·休假）备注=法定休假，量级贴近周日档；
- `2026-09-25`（周五·三薪）备注=法定三薪，量级低于 9/18（普通周五）。

若当天非 2026-09-09 或数据已更新，取实际最新 `output/<今天>/预测_*.xlsx` 中落在日历内的行核对，判读标准不变。

- [ ] **Step 4: 提交收尾（如有数据文件更新一并提交）**

```powershell
git status --short
```

Expected: 无未提交源码改动（Task 1-3 已各自提交）；若 Step 2 生成物在 `output/`（git 忽略）则无需提交。

---

## Self-Review（写完即自查）

- **Spec 覆盖**：spec §3.1 语义映射 → Task 2（`_day_band`/`forecast_ratio`）；§3.2 日历载体与格式 → Task 1（`data/节假日.csv`，含 2026 全部 39 行）；§3.3 文件改动表 → Task 1（config）、Task 2（models）、Task 3（report 备注）；§4 边界（缺失文件/坏行/回测窗口/降级路径）→ Task 2 测试与实现注释；§5 验证（4 单测 + 端到端 + 回归）→ Task 2/4；§6 已知局限 → Global Constraints 与实现注释（`ponytail:` 无需标注，覆盖语义写在 `_REST_DAYS`/`_MAKEUP_DAYS` 注释）。
- **占位符扫描**：无 TBD/TODO；每步含完整代码与命令。
- **类型一致性**：`_holiday_map()`（无参，返回 dict，lru_cache maxsize=1）、`holiday_type(d) -> str`、`_day_band(sidx: pd.Series, d) -> float` 三者在 Task 2 定义、Task 3/测试引用处签名一致；`forecast_ratio(series, future_dates, use_month_seasonal=False)` 签名未变，既有调用（`forecast.py` `point_forecast`/`backtest_sigma`/`forecast_client_volumes`）零改动成立。
