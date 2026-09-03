# PeakFlow 客户量预测「总量 + 份额」重构 — 设计文档

日期：2026-09-03
状态：待用户评审

## 1. 背景与动机

业务现实：**已无新客户新增**，客户主要在状态间切换（M1 → M2-M3 → M3+，repay_3out 流出等），因此**总客户量基本恒定**。

数据证据（`data/在线各类用户.csv`，6/4–9/2，91 天；在线/热线客户量同源）：

- 总客户量均值 1278.8 万，变异系数 **0.20%**，无星期季节，线性趋势 +0.006%/天（30 天后 +0.00%）——总量是平直常数。
- 各类型在切换：repay_3out −0.92%/天、repay_3in −1.02%/天、M2-M3 −0.49%/天 下降；over_30 +0.10%/天、M3+ +0.05%/天 上升；各桶此消彼长、合计≈0。

当前问题：`forecast_client_volume` 把每个类型当**独立序列**线性外推，不懂「总量守恒、桶间切换」：

- 下降桶（repay_3out/repay_3in）被外推到 0（甚至负值被 clip 到 0）——但业务上客户是**流去别的桶**，不是消失。
- 上升桶（over_30）无上限增长。
- 各桶预测之和 ≠ 稳定总量。

## 2. 目标与范围

**目标**：客户量预测改为「总量(flat) + 份额」两层，编码「无新增、桶间切换」，根治塌缩/爆炸。

**范围内**：仅替换客户量预测模型；咨询占比模型（`forecast_ratio`）、转人工占比逻辑不变。

**范围外（明确不做）**：
- 不做马尔可夫转移矩阵（更重，暂无转移率数据需求）。
- 不改咨询占比、转人工占比。

## 3. 方案设计

### 3.1 数学形式

1. 历史总量：`total(t) = Σ_i client_count_i(t)`
2. flat 总量：`T = mean(total 最近 TOTAL_WINDOW 天)`（标量，未来所有日期共用）
3. 每类型历史份额：`share_i(t) = client_count_i(t) / total(t)`
4. 份额预测：`ŝ_i = forecast_ratio(share_i, future_dates, use_month_seasonal=False)`（复用现有 log 空间「趋势 + 星期 + 阻尼外推」，自动继承 `TREND_DAMP=0.8`）
5. 归一化：`ŝ_i[d] ← ŝ_i[d] / Σ_j ŝ_j[d]`（每天份额和 = 1）
6. 客户量：`level_i[d] = T × ŝ_i[d]`

说明：客户量无月内日序规律（已验证月内平），故份额**不启用**月内季节项。

### 3.2 落点与改动文件

| 文件 | 改动 |
|---|---|
| `peakflow/models.py` | 删除 `forecast_client_volume`；新增 `forecast_client_volumes(history_df, future_dates) -> dict[str, np.ndarray]` |
| `peakflow/forecast.py` | `point_forecast`、`backtest_sigma` 改为先算 `cvs = forecast_client_volumes(...)` 再按类型取值；`backtest_sigma` 循环由「按类型外层」改为「按日期外层」 |
| `peakflow/config.py` / `config.yaml` | 新增 `TOTAL_WINDOW`（默认 14，从 `forecast` 段 `total_window` 读取） |
| `tests/test_peakflow_models.py` | 3 个 client volume 测试改写为新函数 |

### 3.3 接口

```python
def forecast_client_volumes(history_df: pd.DataFrame, future_dates: list) -> dict[str, np.ndarray]:
    """客户量预测：总量(flat) × 份额(forecast_ratio + 归一化)。
    history_df 列: date, client_type, client_count, ...
    返回 {client_type: np.ndarray(len(future_dates))}，值 >= 0。"""
```

`forecast_client_volume` 现有调用点仅 `peakflow/forecast.py`（2 处）与 `tests/test_peakflow_models.py`，无其他引用。

## 4. 边界与错误处理

- 总量为 0 或近期窗口为空：返回全 0。
- 某类型份额全 0：`forecast_ratio` 对 `s[s>0]` 为空时返回 0，但后续 `exp→clip` 路径保证份额预测下限为 `1e-9`（非负正数），归一化后该类型客户量 ≈ `flat × 1e-9`（极小正数，非恰好 0）。
- 归一化分母为 0（所有份额预测均为 0）：该日各类型 client volume 置 0。
- `share` 与 `total` 按日期对齐（pandas Series 相除自动对齐），NaN 由 `forecast_ratio` 的 `dropna` 处理。

## 5. 验证与成功标准

1. **单元测试**（`tests/test_peakflow_models.py` 改写 + 新增）：
   - 各类型客户量之和 = flat 总量（守恒）；
   - 份额和为 1（归一化生效）；
   - 客户量非负，且下降桶不会被外推到 0（份额机制）；
   - `forecast_client_volumes` 返回 8 个类型、长度 = len(future_dates)。
2. **回测对比**：全月回测（整个 8 月）MAPE 不劣化；重点看 repay_3out/repay_3in/behind_30 不再出现「外推到 0」的塌缩。
3. **回归**：完整测试套件通过；重新生成预测，确认 9 月底转人工不再因月初 2 天被虚高，且客户量总量稳定。

## 6. 风险

- 份额外推仍依赖最近趋势（虽已阻尼），但份额有界（0~1）且和为 1，不会像原始水平那样塌缩/爆炸。
- 若未来真的重新出现「新增客户」（总量不再平），flat 总量会滞后——届时再引入总量的趋势项。
