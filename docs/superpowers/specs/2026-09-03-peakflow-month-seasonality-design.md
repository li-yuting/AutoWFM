# PeakFlow 咨询占比月内日序季节项 — 设计文档

日期：2026-09-03
状态：待用户评审

## 1. 背景与动机

业务观察：每月月初 `M1` / `M2-M3` / `M3+` 三类客户的进线量会上涨，到月末逐渐下降。

经数据验证（`data/在线各类用户.csv`、`data/热线各类用户.csv`，2026-06-04 ~ 09-02，共 91 天 / 3 个自然月）：

- **M2-M3**：月内规律确凿。进线「前5天 / 后5天」（÷当月均值）在线 1.39~2.47 → 0.61~0.79，逐月一致下降；月内斜率 −0.018 ~ −0.063/天；剔除星期几后 r=−0.62。
- **M3+**：规律存在但略弱。2/3 月强（前5天 1.34/1.72 → 后5天 0.68/0.77），7 月例外（当月月初未冲高）；剔除星期几后 r=−0.56。
- **M1**：不成立，月内方向不稳定（正负交替），月内波动仅 ±5%。
- **关键定位**：月内波动 **100% 来自「咨询占比」（inbound/client_count）**，客户量（client_count）月内基本平（M3+ 斜率 +0.0005/天）。机制上解释为：月初客户滚入新账龄档，触达/咨询意愿冲高，随后回落。

## 2. 目标与范围

**目标**：让 `peakflow` 的 30 天进线预测在 M2-M3 / M3+ 上体现「月初冲高、月末回落」的月内规律，降低这三类的预测误差（MAPE）。

**范围内**：
- 仅改造咨询占比模型 `forecast_ratio`，新增「月内日序季节项」。
- 仅对 `M2-M3`、`M3+` 生效。

**范围外（明确不做）**：
- 不改 `forecast_client_volume`（客户量月内平，证据充分）。
- 不改 M1 及其他 5 个非逾期类型。
- 不做全新模型/依赖，沿用现有「趋势 + 季节分解」框架。

## 3. 方案设计

### 3.1 落点与改动文件

| 文件 | 改动 |
|---|---|
| `peakflow/models.py` | `forecast_ratio` 增加可选参数 `use_month_seasonal: bool = False`；新增月内日序季节指数计算与平滑；预测时叠加日序项 |
| `peakflow/forecast.py` | `point_forecast`、`backtest_sigma` 按 `t ∈ MONTH_SEASONAL_TYPES` 传入 `use_month_seasonal=True` |
| `peakflow/config.py` | 新增 `MONTH_SEASONAL_TYPES`、`DOM_SMOOTH_WINDOW`、`DOM_MIN_MONTHS`（从 `config.yaml` 的 `forecast` 段读取，带默认值） |
| `config.yaml` | `forecast` 段新增 3 个键（可选，有默认值） |

### 3.2 数学形式

现状：`log(ratio) = 趋势 + 星期季节 + 噪声`

目标类型变为：`log(ratio) = 趋势 + 星期季节 + 月内日序季节 + 噪声`

步骤：

1. 趋势 `trend`：7 日中心滑动平均（现有 `_trend_tail`）。
2. 星期季节 `sidx_weekday`：按 weekday 对 `log(ratio) − trend` 取均值（现有 `_seasonal_index`）。
3. 残差 `resid = log(ratio) − trend − sidx_weekday`（先扣星期项，避免日序指数被星期污染）。
4. 日序原始指数 `sidx_dom_raw`：按「几号 1–31」对 `resid` 取均值。
5. 平滑 `sidx_dom`：对 `sidx_dom_raw` 做 3 天中心滑动平均，边缘 `min_periods=1` 缩窗，**不做环形**（月初与月末不相邻）。
6. 预测：`ratio_t = exp(趋势外推_t + sidx_weekday[weekday] + sidx_dom[day])`，clamp 到 `[1e-9, 1]`（沿用现有）。

### 3.3 应用条件与退化

- 仅当 `客户类型 ∈ MONTH_SEASONAL_TYPES` 且 `use_month_seasonal=True` 时启用。
- 该类型历史覆盖的自然月数 `< DOM_MIN_MONTHS`（默认 2）时，退化为纯星期模型（不抛错）。
- 现有「历史 < 21 天取末值」兜底路径保持不变，且**不**叠加日序项。

### 3.4 回测一致性（易漏点）

`backtest_sigma` 内部同样调用 `forecast_ratio`，必须传入相同的 `use_month_seasonal` 标志。否则三档区间的 σ 按旧模型误差计算，与新点预测不匹配，区间会偏宽。

## 4. 配置项

| 键 | 默认值 | 含义 |
|---|---|---|
| `month_seasonal_types` | `["M2-M3", "M3+"]` | 启用月内日序项的类型 |
| `dom_smooth_window` | `3` | 日序曲线平滑窗口（天） |
| `dom_min_months` | `2` | 启用所需的最少自然月数 |

读取方式与现有 `config.py` 一致（`yaml` 的 `forecast` 段，`int(...)` / `float(...)` 转换，缺省用默认值）。

## 5. 验证与成功标准

1. **回测对比**：`backtest_sigma` 改动前后，M2-M3 / M3+ 的 MAPE 应下降；其余类型与整体不劣化。
2. **形状检查**：M2-M3 / M3+ 的 `sidx_dom` 指数应呈现「月初高、月末低」，且「月初几号」预测占比 > 「月末几号」。
3. **最小可运行检查**：新增 `tests/test_peakflow_month.py`（纯 `assert`，可直接运行），覆盖：
   - 目标类型日序指数存在且月初 > 月末；
   - 非目标类型调用结果与旧模型一致（`use_month_seasonal=False` 时逐位相等）；
   - 不足 `DOM_MIN_MONTHS` 时退化为无日序项。

## 6. 边界与错误处理

- 未来日期的「几号」在 1–31 内，日序指数查询必命中；极端情况下缺 key 用 0.0 兜底（与现有 `sidx.get(..., 0.0)` 一致）。
- 2 月等短月：预测日期只查 1–28，指数 29–31 自然不用。
- 全 0 客户量：`ratio` 计算已有 `replace(0, nan)` 与 `dropna` 保护，不新增路径。

## 7. 风险

- 仅 3 个月样本，日序指数每个「几号」最多 3 个观测；平滑（3 天）用于压噪。若未来拉长历史，可再调 `dom_smooth_window` 或改参数化形式。
- M3+ 存在单月（7 月）月初未冲高的例外，日序指数会平均掉这部分，属可接受。
