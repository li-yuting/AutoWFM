# PeakFlow 客户量「总量 + 份额」重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把客户量预测从「每类型独立线性外推」改为「flat 总量 × 份额（复用 forecast_ratio + 归一化）」，编码「无新增、桶间切换」，根治下降桶被外推到 0 的塌缩。

**Architecture:** 新增 `forecast_client_volumes(history_df, future_dates)` 一次算全部类型：flat 总量 = 最近 `TOTAL_WINDOW` 天均值的标量；每类型份额 = client_count / 总量，用 `forecast_ratio`（log 空间趋势+星期+阻尼，无月内项）预测，再按日归一化使份额和=1；客户量 = flat 总量 × 归一化份额。`point_forecast` 与 `backtest_sigma` 改为先算该 dict 再按类型取值。

**Tech Stack:** Python 3.14、pandas、numpy；无新依赖。plain-`assert` 测试，直接运行。

## Global Constraints

- 解释器固定 `.\.venv\Scripts\python.exe`，运行前设 `$env:PYTHONIOENCODING="utf-8"`。
- 测试命名 `tests/test_*.py`，直接 `python tests\test_xxx.py` 运行，不用 pytest。
- 当前 `config.yaml` 工作区已有无关未提交改动；任何提交都不得 `git add config.yaml` 整体暂存，用 `git commit -- <pathspec>` 只提交目标文件。
- 客户量无月内日序规律（已实证），份额预测**不启用**月内季节项（`use_month_seasonal=False`）。
- `TREND_DAMP` 已启用为 0.8（上一提交 c36709d），份额复用 `forecast_ratio` 自动继承该阻尼。
- `forecast_ratio` 逻辑保持不变，仅新增调用方。

---

### Task 1: `forecast_client_volumes` 实现 + 单元测试

**Files:**
- Modify: `peakflow/models.py:41-52`（删除 `forecast_client_volume`，替换为 `forecast_client_volumes`）
- Modify: `peakflow/config.py:41`（追加 `TOTAL_WINDOW`）
- Modify: `config.yaml`（追加 `total_window` 键，不提交）
- Test: `tests/test_peakflow_models.py:17-46`（3 个 client volume 测试改写为新函数）

**Interfaces:**
- Produces: `forecast_client_volumes(history_df: pd.DataFrame, future_dates: list) -> dict[str, np.ndarray]`；`config.TOTAL_WINDOW: int`。
- Consumes: `forecast_ratio(series, future_dates, use_month_seasonal=False)`（已存在）。

- [ ] **Step 1: 在 `peakflow/config.py` 追加 `TOTAL_WINDOW`**

在 `DOM_MIN_MONTHS = int(_fc.get("dom_min_months", 2))` 之后追加：

```python
TOTAL_WINDOW = int(_fc.get("total_window", 14))
```

- [ ] **Step 2: 在 `config.yaml` 追加 `total_window` 键（不提交）**

在 `dom_min_months: 2` 之后追加：

```yaml
  total_window: 14
```

- [ ] **Step 3: 写失败测试**

改写 `tests/test_peakflow_models.py`：删除 `test_client_volume_basic`、`test_client_volume_follows_trend_and_weekday`、`test_client_volume_short_history_raises` 三个函数，替换为以下内容；保留其余 ratio 测试和 `main()` 里对它们的调用不变。

新增 import（在 `from peakflow import models` 之后）：

```python
from peakflow import config
```

新增 helper（放在 `_future_dates` 之后）：

```python
def _future_dates_from(last, n=30):
    return [last + dt.timedelta(days=i) for i in range(1, n + 1)]


def _make_history_df(n_days=70, total=1_000_000.0):
    dates = pd.date_range("2026-06-01", periods=n_days)
    x = np.linspace(0.0, 1.0, n_days)
    s_m1 = 0.15 - 0.10 * x           # M1 份额下降
    s_over = 0.35 + 0.10 * x         # over_30 份额上升
    s_out = 0.10 - 0.08 * x          # repay_3out 份额下降
    rest = 1.0 - (s_m1 + s_over + s_out)
    others = [t for t in config.CLIENT_TYPES if t not in ("M1", "over_30", "repay_3out")]
    share_of = {"M1": s_m1, "over_30": s_over, "repay_3out": s_out}
    for t in others:
        share_of[t] = rest / len(others)
    rows = []
    for j, d in enumerate(dates):
        for t in config.CLIENT_TYPES:
            rows.append({"date": d, "client_type": t,
                         "client_count": total * share_of[t][j]})
    return pd.DataFrame(rows)
```

新增测试函数：

```python
def test_client_volumes_conservation():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    total_flat = df.groupby("date")["client_count"].sum().iloc[-config.TOTAL_WINDOW:].mean()
    for i in range(len(fd)):
        s = sum(vols[t][i] for t in config.CLIENT_TYPES)
        assert abs(s - total_flat) < 1e-6, f"day {i} 总量不守恒: {s} vs {total_flat}"


def test_client_volumes_non_negative_and_keys():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    assert set(vols.keys()) == set(config.CLIENT_TYPES)
    for t in config.CLIENT_TYPES:
        assert len(vols[t]) == len(fd)
        assert np.all(vols[t] >= 0)


def test_client_volumes_declining_share_stays_positive():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    # repay_3out 份额持续下降: 客户量不应塌缩到 0, 且整体递减
    assert np.all(vols["repay_3out"] > 0)
    assert vols["repay_3out"][-5:].mean() < vols["repay_3out"][:5].mean()


def test_client_volumes_rising_share():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    # over_30 份额上升: 客户量应递增
    assert vols["over_30"][-5:].mean() > vols["over_30"][:5].mean()
```

更新 `main()` 中相应调用为这 4 个新函数名（替换原来的 3 个 client volume 调用）。

- [ ] **Step 4: 运行测试，确认失败**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_models.py
```

Expected: 报 `AttributeError: module 'peakflow.models' has no attribute 'forecast_client_volumes'`（函数尚未实现）。

- [ ] **Step 5: 在 `peakflow/models.py` 实现 `forecast_client_volumes`**

将现有 `forecast_client_volume` 函数整体替换为：

```python
def forecast_client_volumes(history_df: pd.DataFrame, future_dates: list) -> dict[str, np.ndarray]:
    """客户量预测：flat 总量 × 份额(forecast_ratio + 归一化)。

    返回 {client_type: np.ndarray(len(future_dates))}，值 >= 0；总量守恒。
    """
    total = history_df.groupby("date")["client_count"].sum().sort_index()
    recent = total.iloc[-config.TOTAL_WINDOW:]
    if len(recent) == 0 or recent.mean() <= 0:
        return {t: np.zeros(len(future_dates)) for t in config.CLIENT_TYPES}
    flat = float(recent.mean())
    shares = {}
    for t in config.CLIENT_TYPES:
        sub = history_df[history_df["client_type"] == t].set_index("date").sort_index()
        share = sub["client_count"].divide(total).replace([np.inf, -np.inf], np.nan)
        shares[t] = forecast_ratio(share, future_dates, use_month_seasonal=False)
    out = {t: np.empty(len(future_dates)) for t in config.CLIENT_TYPES}
    for i in range(len(future_dates)):
        s = sum(shares[t][i] for t in config.CLIENT_TYPES)
        for t in config.CLIENT_TYPES:
            out[t][i] = flat * (shares[t][i] / s) if s > 0 else 0.0
    return out
```

注意：删除原 `forecast_client_volume`，不留别名（其唯一外部调用在 Task 2 一并更新）。

- [ ] **Step 6: 运行测试，确认通过**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_models.py
```

Expected: `test_peakflow_models OK`

- [ ] **Step 7: 提交（只提交 config.py、models.py、测试文件）**

```powershell
git add peakflow/config.py peakflow/models.py tests/test_peakflow_models.py
git commit -m "feat(peakflow): 客户量预测改为总量+份额两层模型"
```

---

### Task 2: 接入 `forecast.py` 并用真实数据验证

**Files:**
- Modify: `peakflow/forecast.py:7`（import）、`peakflow/forecast.py:19`（point_forecast）、`peakflow/forecast.py:51-70`（backtest_sigma 循环重构）

**Interfaces:**
- Consumes: `forecast_client_volumes(history_df, future_dates) -> dict[str, np.ndarray]`（Task 1）。
- Produces: 无新对外接口；`point_forecast` / `three_band_forecast` / `backtest_sigma` 行为改变（客户量总量守恒）。

- [ ] **Step 1: 修改 import**

将 `peakflow/forecast.py` 第 7 行：

```python
from peakflow.models import forecast_client_volume, forecast_ratio, mean_recent_ratio
```

改为：

```python
from peakflow.models import forecast_client_volumes, forecast_ratio, mean_recent_ratio
```

- [ ] **Step 2: 修改 `point_forecast`**

在 `rows = []` 之后、`for t in config.CLIENT_TYPES:` 之前插入一行，并将循环内 `cv = forecast_client_volume(sub["client_count"], future_dates)` 改为 `cv = cvs[t]`：

```python
def point_forecast(history_df: pd.DataFrame, future_dates: list) -> pd.DataFrame:
    cvs = forecast_client_volumes(history_df, future_dates)
    rows = []
    for t in config.CLIENT_TYPES:
        sub = history_df[history_df["client_type"] == t].set_index("date").sort_index()
        cv = cvs[t]
        r_series = sub["inbound"] / sub["client_count"].replace(0, np.nan)
        rf = forecast_ratio(r_series, future_dates,
                            use_month_seasonal=_uses_month_seasonal(t))
        ...
```

（其余循环体不变。）

- [ ] **Step 3: 重构 `backtest_sigma`**

将 `backtest_sigma` 的循环部分整体替换（保持函数签名、`res_*`/`mape_*` 字典初始化和最后的 `sigma` 计算不变）：

```python
    sub_by_type = {t: history_df[history_df["client_type"] == t].set_index("date").sort_index()
                   for t in config.CLIENT_TYPES}
    for d in back:
        train_df = history_df[history_df["date"] < d]
        if train_df["date"].nunique() < 21:
            continue
        cvs = forecast_client_volumes(train_df, [d])
        for t in config.CLIENT_TYPES:
            sub = sub_by_type[t]
            train = sub[sub.index < d]
            if len(train) < 21:
                continue
            fv = cvs[t][0]
            rs = train["inbound"] / train["client_count"].replace(0, np.nan)
            fr = forecast_ratio(rs, [d], use_month_seasonal=_uses_month_seasonal(t))[0]
            tr = mean_recent_ratio(train["transfer"] / train["inbound"].replace(0, np.nan))
            pred_in = fv * fr
            pred_tr = pred_in * tr
            act_in = float(sub.loc[d, "inbound"])
            act_tr = float(sub.loc[d, "transfer"])
            res_in[t].append(abs(pred_in - act_in))
            res_tr[t].append(abs(pred_tr - act_tr))
            if act_in > 0:
                mape_num[t].append(abs(pred_in - act_in))
                mape_den[t].append(act_in)
```

（即把原来的「外层按类型、内层按日期」改为「外层按日期、内层按类型」，并在每个日期先算一次 `cvs`。）

- [ ] **Step 4: 回归——重跑单元测试**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_models.py
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_peakflow_forecast.py
```

Expected: 两者均通过（`test_peakflow_models OK` / `All tests passed!`）。

- [ ] **Step 5: 真实数据验证——客户量守恒 + 不再塌缩**

Run:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "from peakflow import config, loader; from peakflow.forecast import three_band_forecast; import pandas as pd; hist=loader.load_channel_data(config.DATA_DIR/'在线各类用户.csv'); fd=[hist['date'].max()+pd.Timedelta(days=i) for i in range(1,31)]; base,_=three_band_forecast(hist,fd); print('各日客户量合计(应恒定):', [round(base[base['date']==d]['client_vol'].sum()) for d in fd[:3]]); print('repay_3in 客户量(应>0, 不塌缩):', [round(base[(base['date']==d)&(base['client_type']=='repay_3in')]['client_vol'].sum()) for d in fd[:3]])"
```

Expected: 各日客户量合计恒定（约 1280 万）；`repay_3in` 客户量为正、不再为 0。

- [ ] **Step 6: 提交**

```powershell
git commit -m "feat(peakflow): 预测接入总量+份额客户量模型" -- peakflow/forecast.py
```

---

## Self-Review 记录

- **Spec 覆盖**：§3.1 数学（Task 1 Step 5）、§3.2 落点（Task 1/2 对应 models/config/forecast）、§3.3 接口（Task 1 Step 5 签名）、§4 边界（`recent.mean()<=0` 全 0、`s>0` 归一化、inf→NaN）、§5 验证（Task 1 测试 + Task 2 Step 5）——均有对应任务。
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`forecast_client_volumes` 签名、`config.TOTAL_WINDOW` 引用、`cvs[t]` 取值三处一致。
