# PeakFlow 咨询占比月内日序季节项 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `peakflow` 的 30 天进线预测在 M2-M3 / M3+ 上体现「月初冲高、月末回落」的月内规律，降低这两类咨询占比预测的 MAPE。

**Architecture:** 在 `peakflow/models.py` 的 `forecast_ratio` 内，沿用现有「趋势 + 周季节」分解框架，新增「月内日序季节项」：对 `log(ratio) − 趋势 − 星期项` 的残差按「几号(1–31)」取均值并做 3 天平滑，预测时叠加。仅对 M2-M3 / M3+ 生效，客户量模型不动。

**Tech Stack:** Python 3.14、pandas、numpy；无新依赖。测试为 plain `assert` 脚本，直接运行，不用 pytest。

## Global Constraints

- 解释器固定 `.venv\Scripts\python.exe`，运行前设 `$env:PYTHONIOENCODING="utf-8"`。
- `collector/`、`dashboard/` 等模块必须用 `-m` 运行；本任务只改 `peakflow/`（用 `-m` 运行 `peakflow` 内模块或直接 import），不影响。
- 测试文件命名 `tests/test_*.py`，函数 `test_*`，直接 `python tests/test_peakflow_month.py` 运行（不用 pytest）。
- DB 列名、extractor key 均不涉及本任务。
- `config.yaml` 当前工作区已有**与本任务无关的未提交改动**（`subs`、`member_limit` 段）。**任何提交都不得 `git add config.yaml` 整体暂存**；config.yaml 的新键用 `git commit -- <pathspec>` 方式避开，或留在工作区不提交。
- 月内日序项只在 `客户类型 ∈ MONTH_SEASONAL_TYPES` 且该类型历史覆盖自然月数 ≥ `DOM_MIN_MONTHS` 时启用；`len(series) < 21` 的兜底路径不加日序项。

---

### Task 1: 配置项（`peakflow/config.py` + `config.yaml`）

**Files:**
- Modify: `peakflow/config.py:37-38`（在 `TREND_DAMP` 之后追加 3 行）
- Modify: `config.yaml:60`（在 `trend_damp: 0.0` 之后追加 3 个键）

**Interfaces:**
- Produces: `config.MONTH_SEASONAL_TYPES: list[str]`、`config.DOM_SMOOTH_WINDOW: int`、`config.DOM_MIN_MONTHS: int`，供 Task 2 的 `forecast_ratio` 使用。

- [ ] **Step 1: 在 `peakflow/config.py` 追加配置项**

在文件末尾（`TREND_DAMP = float(_fc.get("trend_damp", 0.0))` 这一行之后）追加：

```python
MONTH_SEASONAL_TYPES = list(_fc.get("month_seasonal_types", ["M2-M3", "M3+"]))
DOM_SMOOTH_WINDOW = int(_fc.get("dom_smooth_window", 3))
DOM_MIN_MONTHS = int(_fc.get("dom_min_months", 2))
```

- [ ] **Step 2: 在 `config.yaml` 的 `forecast` 段追加 3 个键**

在 `forecast:` 段最后一行 `trend_damp: 0.0` 之后追加（注意缩进为 2 空格）：

```yaml
  # 月内日序季节(账龄类型月初冲高、月末回落)
  month_seasonal_types: ["M2-M3", "M3+"]
  dom_smooth_window: 3
  dom_min_months: 2
```

- [ ] **Step 3: 验证配置读取**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "from peakflow import config; print(config.MONTH_SEASONAL_TYPES, config.DOM_SMOOTH_WINDOW, config.DOM_MIN_MONTHS)"
```

Expected:

```
['M2-M3', 'M3+'] 3 2
```

- [ ] **Step 4: 提交（只提交 config.py，避开 config.yaml 的无关改动）**

```powershell
git commit -m "feat(peakflow): 增加月内日序季节配置项" -- peakflow/config.py
```

说明：`config.yaml` 的 3 个新键留在工作区**不提交**（该文件已有无关未提交改动，混入会污染提交）；3 个键为可选，默认值已在 `config.py`，功能不依赖它们。

---

### Task 2: `forecast_ratio` 月内日序季节项（`peakflow/models.py`）+ 单元测试

**Files:**
- Modify: `peakflow/models.py:55-69`（`forecast_ratio` 函数整体替换）、在 `_seasonal_index` 之后新增 `_dom_seasonal_index`
- Test: `tests/test_peakflow_month.py`（新建）

**Interfaces:**
- Consumes: `config.MONTH_SEASONAL_TYPES`（Task 1，本 Task 暂不直接调用，仅 `forecast_ratio` 内部用 `config.DOM_MIN_MONTHS` / `config.DOM_SMOOTH_WINDOW`）。
- Produces: `forecast_ratio(series, future_dates, use_month_seasonal=False) -> np.ndarray`（签名扩展一个关键字参数，旧调用不受影响）；`_dom_seasonal_index(resid, window) -> pd.Series`（内部函数）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_peakflow_month.py`：

```python
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from peakflow.models import forecast_ratio


def _month_spike_ratio(dates):
    """仅月初 1-3 号冲高、无星期效应的占比序列。"""
    return pd.Series(
        [np.exp(0.6 if 1 <= d.day <= 3 else 0.0) for d in dates], index=dates
    )


def _flat_ratio(dates):
    """恒定占比序列(无星期、无月内效应)。"""
    return pd.Series(0.8, index=dates)


def test_month_seasonal_early_gt_late():
    dates = pd.date_range("2026-01-01", "2026-03-31", freq="D")
    s = _month_spike_ratio(dates)
    # 同为周六: 4-03(3号, 月初冲高) vs 4-24(24号, 平稳)
    future = [pd.Timestamp("2026-04-03"), pd.Timestamp("2026-04-24")]
    out = forecast_ratio(s, future, use_month_seasonal=True)
    assert out[0] > out[1], f"月初占比应高于月末: {out[0]:.3f} vs {out[1]:.3f}"


def test_month_seasonal_flat_on_equals_off():
    dates = pd.date_range("2026-01-01", "2026-03-31", freq="D")
    s = _flat_ratio(dates)
    future = pd.date_range("2026-04-01", "2026-04-30", freq="D")
    off = forecast_ratio(s, future, use_month_seasonal=False)
    on = forecast_ratio(s, future, use_month_seasonal=True)
    assert np.allclose(off, on), "恒定占比时开关应一致"


def test_month_seasonal_disabled_under_min_months():
    dates = pd.date_range("2026-01-01", "2026-01-25", freq="D")  # 仅1个月
    s = _month_spike_ratio(dates)
    future = [pd.Timestamp("2026-02-02"), pd.Timestamp("2026-02-23")]
    on = forecast_ratio(s, future, use_month_seasonal=True)
    off = forecast_ratio(s, future, use_month_seasonal=False)
    assert np.allclose(on, off), "不足 DOM_MIN_MONTHS 时应退化为无日序项"


if __name__ == "__main__":
    test_month_seasonal_early_gt_late()
    test_month_seasonal_flat_on_equals_off()
    test_month_seasonal_disabled_under_min_months()
    print("OK: all peakflow month-seasonality tests passed")
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_month.py
```

Expected: 报 `TypeError: forecast_ratio() got an unexpected keyword argument 'use_month_seasonal'`（当前签名不接受该关键字）。

- [ ] **Step 3: 在 `peakflow/models.py` 新增 `_dom_seasonal_index`**

在 `_seasonal_index` 函数之后（`_extrapolate` 之前）插入：

```python
def _dom_seasonal_index(resid: pd.Series, window: int) -> pd.Series:
    """按「几号(1-31)」对残差取均值，再做 window 天中心平滑(边缘缩窗)。"""
    tmp = pd.DataFrame({"v": resid.values, "dom": resid.index.day})
    idx = tmp.groupby("dom")["v"].mean().reindex(range(1, 32))
    return idx.rolling(window, center=True, min_periods=1).mean().ffill().bfill()
```

- [ ] **Step 4: 替换 `forecast_ratio` 函数**

将 `peakflow/models.py` 中现有 `forecast_ratio`（含其 docstring）整体替换为：

```python
def forecast_ratio(series: pd.Series, future_dates: list,
                   use_month_seasonal: bool = False) -> np.ndarray:
    """咨询占比：对 log(r) 做趋势+周季节分解外推，再 exp。返回 (0,1] 数组。

    use_month_seasonal=True 时叠加月内日序季节项(仅 M2-M3/M3+ 等账龄类型)。
    """
    s = series.dropna()
    s = s[s > 0]
    if len(s) < 21:
        last = float(s.iloc[-1]) if len(s) else 0.0
        return np.clip(np.full(len(future_dates), last), 1e-9, 1.0)
    s = s.sort_index()
    ls = np.log(s)
    trend = _trend_tail(ls)
    sidx = _seasonal_index(ls, trend)
    tf = _extrapolate(trend, len(future_dates))
    dom_idx = None
    if use_month_seasonal:
        n_months = s.index.to_period("M").nunique()
        if n_months >= config.DOM_MIN_MONTHS:
            wd = pd.Series([sidx.get(d.weekday(), 0.0) for d in ls.index],
                           index=ls.index)
            resid = ls - trend - wd
            dom_idx = _dom_seasonal_index(resid, config.DOM_SMOOTH_WINDOW)
    out = np.empty(len(future_dates))
    for i in range(len(future_dates)):
        d = future_dates[i]
        v = tf[i] + sidx.get(d.weekday(), 0.0)
        if dom_idx is not None:
            v += float(dom_idx.get(d.day, 0.0))
        out[i] = v
    return np.clip(np.exp(out), 1e-9, 1.0)
```

注意：`forecast_ratio` 的旧调用点（如 `peakflow/forecast.py`）不传关键字参数仍可正常工作（默认 `False`）。

- [ ] **Step 5: 运行测试，确认通过**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_month.py
```

Expected:

```
OK: all peakflow month-seasonality tests passed
```

- [ ] **Step 6: 提交**

```powershell
git add tests/test_peakflow_month.py
git commit -m "feat(peakflow): forecast_ratio 增加月内日序季节项" -- peakflow/models.py tests/test_peakflow_month.py
```

---

### Task 3: 接入 `forecast.py` 并用真实数据验证 MAPE

**Files:**
- Modify: `peakflow/forecast.py:16`（`point_forecast`）、`peakflow/forecast.py:59`（`backtest_sigma`）

**Interfaces:**
- Consumes: `forecast_ratio(..., use_month_seasonal=bool)`（Task 2）、`config.MONTH_SEASONAL_TYPES`（Task 1）。
- Produces: 无新对外接口；`point_forecast` / `three_band_forecast` / `backtest_sigma` 的行为对 M2-M3 / M3+ 改变，其余类型不变。

- [ ] **Step 1: 修改 `point_forecast`**

将 `peakflow/forecast.py` 第 16 行：

```python
        rf = forecast_ratio(r_series, future_dates)
```

改为：

```python
        rf = forecast_ratio(r_series, future_dates,
                            use_month_seasonal=t in config.MONTH_SEASONAL_TYPES)
```

- [ ] **Step 2: 修改 `backtest_sigma`**

将 `peakflow/forecast.py` 第 59 行：

```python
            fr = forecast_ratio(rs, [d])[0]
```

改为：

```python
            fr = forecast_ratio(rs, [d],
                                use_month_seasonal=t in config.MONTH_SEASONAL_TYPES)[0]
```

- [ ] **Step 3: 回归——重跑单元测试**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_month.py
```

Expected: `OK: all peakflow month-seasonality tests passed`

- [ ] **Step 4: 真实数据验证——对比回测 MAPE**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "from peakflow import config, loader; from peakflow.forecast import backtest_sigma; [print(ch, backtest_sigma(loader.load_channel_data(config.DATA_DIR/f))) for ch,f in [('在线','在线各类用户.csv'),('热线','热线各类用户.csv')]]"
```

对比基线（改动前记录）：

| 渠道 | 类型 | 改动前 MAPE | 期望 |
|---|---|---|---|
| 在线 | M2-M3 | 16.77% | 下降或持平 |
| 在线 | M3+ | 14.15% | 下降或持平 |
| 热线 | M2-M3 | 26.86% | 下降或持平 |
| 热线 | M3+ | 19.60% | 下降或持平 |
| 其余类型 | — | — | 不劣化（保持不变或轻微波动） |

Expected: M2-M3 / M3+ 的 MAPE 较基线下降（或至少不上升）；其余类型 MAPE 与基线一致（因为 `use_month_seasonal=False`，路径不变）。若 M2-M3 / M3+ 的 MAPE 明显上升，停下检查 `_dom_seasonal_index` 的符号与叠加方向。

- [ ] **Step 5: 提交**

```powershell
git commit -m "feat(peakflow): 预测接入月内日序季节项(仅 M2-M3/M3+)" -- peakflow/forecast.py
```

---

## Self-Review 记录

- **Spec 覆盖**：§3.1 落点（Task 1/2/3 对应 config/models/forecast）、§3.2 数学形式（Task 2 Step 3/4）、§3.3 退化（Task 2 的 `DOM_MIN_MONTHS` 与 `<21` 兜底保持不变）、§3.4 回测一致性（Task 3 Step 2）、§4 配置（Task 1）、§5 验证（Task 2 测试 + Task 3 Step 4）、§6 边界（`reindex(1..32)` + `ffill/bfill` + `.get(...,0.0)` 兜底）——均有对应任务。
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`forecast_ratio` 签名、`_dom_seasonal_index` 签名、`config.MONTH_SEASONAL_TYPES/DOM_SMOOTH_WINDOW/DOM_MIN_MONTHS` 三处引用一致。
