# 移除常规工单组（采集 + 看板）设计

日期：2026-09-09
状态：已批准（用户确认范围：只动「常规工单处理组」，历史数据保留）

## 背景

公司架构变动，CRM 中已无「常规工单处理组」的数据。该组当前存在于采集与看板两处：

- **采集**：`config.yaml` 的 `detail_modes.工单明细.filter.groups` 含 `常规工单处理组`，
  计数经 `collector/detail.py` 写入 `工单明细.db` 的 `常规工单处理组` 列
  （列定义在 `collector/repository.py` 的 `SCHEMAS`，单一事实源）。
- **看板**：外呼区第 4 组「常规工单」（卡片 + 图形 + 二线时段明细表列 + 外呼卡片合计项），
  数据来自 `dashboard/queries.py` 的 `build_day`/`build_month` 与 `_OUT_FIELDS`。

## 目标

1. 后续不再采集常规工单组的工单量。
2. 看板移除「常规工单」的全部展示（卡片/图形/明细表列/合计），
   剩余「常规二线 / 贷后二线 / 二线客诉」横向加宽填满空缺。

## 方案

选型：**硬删除**（配置 + SCHEMAS + 看板三处同步移除），不做配置开关。
理由：组在 CRM 已不存在，恢复时把组名加回 config + SCHEMAS 即可，成本极低；开关是 YAGNI。

### 1. 采集端

- `config.yaml`：`detail_modes.工单明细.filter.groups` 移除 `"常规工单处理组"`（8→7 组）。
- `collector/repository.py`：`SCHEMAS["工单明细"]` 移除 `"常规工单处理组"` 列。
  SCHEMAS 驱动插入列名，不删则插入与表结构不一致；历史库旧列保留、不再写入。
- `config.example.yaml`：镜像示例同步去掉一个占位组，保持 8→7 一致。

### 2. 看板端

- `dashboard/queries.py`：
  - `build_day`：删 `inc_cg2_gd` 计算、`outbound["常规工单"]`、`card_out` 中该组及合计的 `cg2_gd`；
  - `build_month`：删 `outbound["常规工单"]` 及合计里的 `cg2_gd`；
  - `_OUT_FIELDS`：删 `"常规工单"` 条目（驱动明细表列与表头）。
- `dashboard/templates/dashboard.html`：不改。
  卡片/图形容器为 `flex:1`，删除一组后其余三组自动横向加宽；表格列自动拉伸。
- API（`api/app.py`）透传 `build_day` JSON，常规工单自动消失。

### 3. 测试

- `tests/test_dashboard_queries.py`：
  - `test_build_day`：外呼合计 108→88；删 `常规工单` 断言，加「常规工单不在 outbound」回归断言；
  - `test_card_detail_lag`：外呼合计 93→83；删 `常规工单` 断言。
  - 种子数据保留旧列，兼作「旧库带该列也能跑」的兼容回归。
- 其他测试（detail/backfill/notify）使用自定义组名，不受 SCHEMAS 变更影响。

### 数据流

采集 → `工单明细.db` 不再写该列 → `build_day/build_month` 不再读 →
卡片/图形/明细表/API 全部消失。企微二线推送（notify.py）用 `回访组一组`/`贷后回访组`，
不含常规工单处理组，不受影响。

## 不做的事

- 不清历史库旧列/旧值（用户确认保留）。
- 不加配置开关。
- 不改通知推送。