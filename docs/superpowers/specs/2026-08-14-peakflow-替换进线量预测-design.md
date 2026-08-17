# PeakFlow 替换进线量预测 — 设计文档

## 概述

将 AutoWFM 当前的「进线量预测」功能（`collector/forecast.py`）替换为 PeakFlow 预测模块，采用趋势+周季节分解外推模型，输出三档区间 Excel + HTML 看板。PeakFlow 代码以 `peakflow/` 包形式平级迁移到 AutoWFM 项目根目录。

## 目标

- 用 PeakFlow 的自实现模型（无外部依赖）替换 statsmodels 回归模型
- 预测粒度从渠道级升级到 8 种客户类型细分
- 输出从 CSV 升级为 Excel（4 sheet）+ HTML 可视化看板
- 每天早上 09:30 自动运行（AutoTableau 09:00 完成后）
- Dashboard 看板保持读取 `预估流入量.csv` 不变
- Tkinter 管理器保留手动触发预测功能
- 删除 21:05 次日差异对比 + 企微告警

## 目录结构

```
AutoWFM/
├── collector/                 # 采集器（不变）
│   ├── forecast.py            # ← 删除
│   ├── scheduler.py           # ← 删除 21:05 forecast diff 逻辑
│   ├── notify.py              # ← 删除 forecast_at() 函数
│   └── ...
├── peakflow/                  # ← 新增：独立预测模块
│   ├── __init__.py
│   ├── config.py              # 适配 config.yaml
│   ├── models.py              # 趋势+周季节分解
│   ├── forecast.py            # 三档预测
│   ├── loader.py              # CSV 解析
│   ├── fetch.py               # AutoTableau 同步
│   ├── report.py              # Excel 输出
│   ├── dashboard.py           # HTML 看板
│   ├── dashboard_template.html
│   └── main.py                # 入口
├── manager.py                 # ← 修改：预测页面调用 peakflow
├── config.yaml                # ← 新增 forecast 配置段
└── ...
```

## 配置

### config.yaml 新增

```yaml
forecast:
  auto_tableau_dir: "D:\\PythonProject\\AutoTableau"
  fetch_files: ["在线各类用户.csv", "热线各类用户.csv"]
  fetch_max_age_days: 2
  horizon: 30
  min_history: 28
  trend_fit_days: 14
  ratio_window: 14
  backtest_window: 14
  sigma_k: 1.0
  trend_damp: 0.0
  output_dir: "output"
```

## 数据流

```
AutoTableau (每天 09:00)
    │  下载 CSV 到 downloads/YYYY-MM-DD/
    ▼
Windows 计划任务 (每天 09:30)
    │  pythonw -m peakflow.main --fetch
    ▼
peakflow.fetch     sync_from_autotableau() → 复制 CSV 到 data/
    │
peakflow.loader    解析 UTF-16 TSV → DataFrame
    │
peakflow.models    趋势+周季节分解 → 客户量外推 / 咨询占比外推
    │
peakflow.forecast  三档区间预测 → 进线量 / 转人工量
    │
peakflow.report    输出 Excel → output/YYYY-MM-DD/预测_YYYYMMDD_未来30天.xlsx
peakflow.dashboard 输出 HTML → output/YYYY-MM-DD/预测_YYYYMMDD_未来30天.html
    │
manager.py (手动触发)
    │  直接调用 peakflow.main.run_forecast(fetch=True)
    ▼
   Tkinter 页面显示结果摘要
```

## Tkinter 管理器改造

### 删除
- `_check_forecast()`: 21:05 自动触发差异对比
- `_run_forecast_diff()`: 差异对比线程
- 预测天数 Spinbox 控件

### 修改
- `_run_forecast()`: 改为调用 `peakflow.main.run_forecast(fetch=True)`
- `_forecast_summary()`: 适配 PeakFlow 的 DataFrame 格式
- 按钮文案改为"运行预测(30天)"

## Dashboard 看板

**不变**。Dashboard 所有预测数据均来自 `预估流入量.csv`，不依赖 `进线量预测_*.csv`，因此无需修改。

## Windows 计划任务

```
任务名: PeakFlow_Daily
触发:   每天 09:30
命令:   D:\PythonProject\AutoWFM\.venv\Scripts\pythonw.exe -m peakflow.main --fetch
目录:   D:\PythonProject\AutoWFM
```

## 删除清单

| 文件 | 内容 |
|------|------|
| `collector/forecast.py` | 整个文件 |
| `collector/scheduler.py` | 21:05 forecast diff 调度逻辑 |
| `collector/notify.py` | `forecast_at()` 函数 |
| `manager.py` | `_check_forecast`、`_run_forecast_diff`、旧的 `_run_forecast` 实现 |

## 依赖变化

- **移除**: statsmodels（不再需要）
- **保持**: numpy, pandas, openpyxl（AutoWFM 已有）

## 模型对比

| 方面 | 旧 (collector/forecast.py) | 新 (peakflow) |
|------|---------------------------|---------------|
| 算法 | statsmodels OLS 回归 | 趋势+周季节分解外推 |
| 预测目标 | 直接预测转人工量 | 客户量→进线→转人工 三级合成 |
| 粒度 | 渠道级 | 8种客户类型 |
| 区间 | 无 | 悲观/中性/乐观 三档 |
| 回测 | 简单自检 ±15% | 滚动回测 + σ + MAPE |
| 输出 | CSV | Excel(4 sheet) + HTML |
| 预测天数 | 7天默认 | 固定30天 |
| 外部依赖 | statsmodels | 无 |