# shift 排班：B/C 班次新增 + Z/Z1 约束重构 设计文档

- 日期：2026-08-28
- 范围：`shift/` 子项目（reader 零改动；scheduler / validators / writer / utils / app / 模板 / 测试）
- 方案：方案 A「班次家族化重构」（已与需求方确认）

## 1. 背景与目标

排班器 `scheduler.run_scheduler` 十步流水线现有约束以 `HIGH_LIMIT_SHIFTS = {D, D1, Z, Z1}` 为一个整体。本次变更：

1. 新增 **B 班**（8:30 上班）与 **C 班**两个班次；
2. 将高强约束**按家族拆分**：D/D1 保留旧三条；Z/Z1 启用新四条（贴 OFF、连排 2–3 天可配、单块禁夹心、数量均衡）。

## 2. 已确认的需求规则

| # | 班次/对象 | 规则 |
|---|---|---|
| 1 | B 班 | 前一天不能是 D/D1/Z（**Z1 允许**）；其余规则同 A2；三期不可上 |
| 2 | C 班 | **只能**出现在 D/D1/Z/Z1 的次日（硬约束，候选不足报需求缺口 WARN）；其余规则同 A2；三期不可上 |
| 3 | D/D1 | 旧三条原样：连排 ≤ `max_high_consecutive`(2)、次日非 OFF、禁夹心；作用对象改为 D 族自身 |
| 4 | Z/Z1 | ① 尽量贴前一个 OFF；② 连排下限 `z_min`(默认 2, 软)、上限 `z_max`(默认 3, 硬)，Web UI 可调；③ 两次 OFF 之间只允许一个连续 Z/Z1 块（禁 Z→A→Z）；④ 非三期员工间 Z/Z1 计数均衡，差 < `balance_threshold` |
| 5 | 均衡组 | 三组各自内部平均：`{D,D1}`、`{Z,Z1}`、`{A1,A4}`；原 `HIGH_BALANCE_SHIFTS`/`SECONDARY_BALANCE_SHIFTS` 废弃 |

## 3. 班次家族（utils.py）

```python
SHIFT_ORDER = ("D", "D1", "Z", "Z1", "C", "B", "A1", "A4", "A2", "A3")

D_FAMILY                  = {"D", "D1"}
Z_FAMILY                  = {"Z", "Z1"}
HIGH_LIMIT_SHIFTS         = D_FAMILY | Z_FAMILY   # 保留为派生并集
HIGH_SHIFTS               = {"D", "D1"}            # 新人禁上，不变
A_CLASS_SHIFTS            = {"A1", "A2", "A3", "A4", "B", "C"}  # B/C 并入
COMFORT_SHIFTS            = {"A2", "A3"}           # 三期舒适班，不变 → 三期自动排除 B/C

D_BALANCE_SHIFTS          = D_FAMILY
Z_BALANCE_SHIFTS          = Z_FAMILY
A_BALANCE_SHIFTS          = {"A1", "A4"}
BALANCE_GROUPS            = (D_BALANCE_SHIFTS, Z_BALANCE_SHIFTS, A_BALANCE_SHIFTS)
# HIGH_BALANCE_SHIFTS / SECONDARY_BALANCE_SHIFTS 删除，引用点全部改 BALANCE_GROUPS
```

- **SHIFT_ORDER 中 C、B 插在 Z1 之后、A1 之前**：C 候选池最小（硬位置约束）先填先得，B 次之。
- `normalize_shift` 现有逻辑对单字母 B/C 直接命中，`"B(8:30)"` 类注释格式经 `startswith(shift + "(")` 命中，无需修改。
- 连续 Z/Z1 段 = 连续多天的 Z_FAMILY 成员（Z 与 Z1 混排算同一块）。

## 4. 配置与 Web UI

- `SchedulerConfig` 新增：`z_min_consecutive: int = 2`（软下限）、`z_max_consecutive: int = 3`（硬上限）。
- `app.py /run`：读取表单参数 `z_min_consecutive` / `z_max_consecutive`；校验 `1 ≤ z_min ≤ z_max`，不满足返回 400。
- `templates/index.html`：参数区新增「Z/Z1 连排下限」「Z/Z1 连排上限」数字输入框。
- `main.py` 可调参数区：`Z_MIN_CONSECUTIVE = 2`、`Z_MAX_CONSECUTIVE = 3`。

## 5. 排班器（scheduler.py）

### 5.1 `_can_assign_shift` 家族分支（硬约束唯一收口）

```python
if shift in D_FAMILY:
    # 旧三条原样，作用对象改为 D_FAMILY 自身：
    # - D 族连排 ≤ max_high_consecutive
    # - 次日不能是 OFF
    # - 禁夹心：昨日A类&前日D族 / 明日A类&后日D族 → 拒
if shift in Z_FAMILY:
    # ① 次日不能是 OFF
    # ② 含当天的连续 Z_FAMILY 段（双向计数）> z_max → 拒（硬上限；下限软，此处不拦）
    # ③ 禁 Z→A→Z：昨日A类&前日Z族 / 明日A类&后日Z族 → 拒
```

关键机制：所有交换/转换路径已经过 `_can_hold_shift` → `_can_assign_shift`，硬约束写进此函数后，修复/均衡/对换步骤自动继承。
A 类夹心检查（`shift in A_CLASS_SHIFTS` 分支）的"高强"继续用并集 `HIGH_LIMIT_SHIFTS`，B/C 并入 A_CLASS 后自动受保护。
新增辅助 `_z_run_count(employee, day_index)`（仿 `_high_limited_count`，族集合换 Z_FAMILY）；D 族连排计数函数相应族化。

### 5.2 新流水线步骤 `shape_z_runs`

位置：`repair_employee_rest_count` 之后、`cluster_high_pairs` 之前。只做**行内同日对调**（每人每日班次多元集合不变，与 `cluster_high_pairs` 同一不变式）：

1. **扩短块**（软下限）：长度 < `z_min` 的 Z 块与相邻工作日非 Z 格行内对调延伸，双向过 `_can_hold_shift`；
2. **合并 Z→A→Z**：同一工作块内两个 Z 块尝试对调连成一块；失败留给校验器报；
3. **贴 OFF**（软偏好）：Z 块尽量行内挪到工作块头部、紧贴前一个 OFF，轻量尝试不强求。

### 5.3 相邻步骤适配

- **`cluster_high_pairs`**：扫描对象从并集改为 **D_FAMILY**（Z 块 2–3 天会干扰"恰好 2 个高强班"判断）；Z 块贴 OFF 归 `shape_z_runs`。
- **`redistribute_balance`**：改为遍历 `BALANCE_GROUPS` 三组；receivers 继续排除三期；交换合法性由 `_can_hold_shift` 自动把关。让步：交换可能临时产生孤立 Z（软下限不拦），最终校验 WARN——均衡优先于块形。
- **`_shift_key`** 首元素：D 族班看 D 族计数、Z 族班看 Z 族计数、A1/A4 看 A1/A4 计数、其余 0。
- **`_fallback_shift`**：allowed 自动含 B/C（受约束过滤 + 需求>0 限制）；固定兜底序列与终极 `"A3"` **不含 B/C**——B/C 是需求驱动班次，不做无需求兜底。

### 5.4 流水线全貌（加粗为变化处）

```
precompute → arrange_rests → optimize_double_rests → arrange_shifts(SHIFT_ORDER 含 C/B)
→ repair_generated_streaks → redistribute_rest_excess → repair_rest_excess
→ repair_employee_rest_count → **shape_z_runs(新)** → cluster_high_pairs(**D 族**)
→ redistribute_balance(**三组**)
```

## 6. 校验器与输出

### 新增 check_id

| id | 名称 | 规则 | 级别 |
|---|---|---|---|
| 16 | B 班前置 | B 的前一日 ∈ {D, D1, Z} | 违规格及前一日均可动 → ERROR；涉及锁定/历史段 → WARN |
| 17 | C 班位置 | C 的前一日 ∉ {D, D1, Z, Z1} | 同上 |
| 18 | Z/Z1 块形状 | ① Z 族连排 > `z_max` → ERROR；② 同一工作块内 ≥2 个 Z 块 → ERROR；③ Z 块 < `z_min` → WARN | 见左 |

### 既有 check 适配

- **check 08**：扫描对象改为 D_FAMILY；Z 族超限并入 18①。
- **check 09**：零改动（A_CLASS 已含 B/C）。
- **check 10**：三组各报一行（D/D1、Z/Z1、A1/A4）。
- 其余 01–07、11–15 不动。

### 输出层

- `writer._highlight_schedule`：08 高亮改用 D 族；18 高亮涉事 Z 块整段；16/17 单格默认分支。
- 统计表「员工统计」：原两列 → 三列「D/D1 均衡」「Z/Z1 均衡」「A1/A4 均衡」；「每日满足情况」自动多 B/C 行，零改动。
- `app.py _WARN_LABELS` 加 `"16": "B班前置", "17": "C班位置", "18": "Z/Z1块形状"`。

## 7. 模板与入口

- **reader：零改动**（需求行动态读取；B/C 经 `normalize_shift` 识别）。
- **模板 xlsx ×4**（排班计划 / 主管 / 在线 / 热线）：需求 sheet 追加 B、C 两行（默认 0；openpyxl 一次性脚本处理，保留原格式）。

## 8. 边界行为（明确写死）

- 历史段/锁定格的 Z 块**计入连排**：历史已连 `z_max` 天 Z，活跃段随后一天 Z 被拒（硬上限跨段生效）。
- `z_min=1` 合法：孤立 Z 不算违规，`shape_z_runs` 扩块步骤自动跳过。
- 新人（系数<1）仍只禁 D/D1；Z/Z1 与 B/C 对新人开放（现状如此，本次不改）。
- C 候选不足：调度中记 03（随后被 clear），最终以 check 03「需求差异」报告。

## 9. 测试（tests/test_shift_bc_z.py，纯 assert）

1. `normalize_shift`：`"b"→"B"`、`"B(8:30)"→"B"`、未知值原样返回。
2. `_can_assign_shift`：D 后拒 B；Z1 后可 B；C 仅高强次日合法；Z 超 `z_max` 拒；Z→A→Z 拒；Z 次日 OFF 拒。
3. `shape_z_runs`：构造孤立 Z → 扩成 2 天块。
4. 三组均衡：构造超阈值分布 → `redistribute_balance` 拉平 / check 10 报警。
5. check 16/17/18 分级：锁定/历史段涉及 → WARN，否则 ERROR。
6. 冒烟：复制「排班计划 - 在线.xlsx」注入 B/C 需求跑全流程，断言最终 ERROR=0。
