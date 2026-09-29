# 功能型子系统插件化重构 · 设计文档

日期：2026-09-29
状态：已与用户对齐四节设计，待用户复核

## 背景与动机

AutoWFM 现状（见 `docs/autowfm-flow.html`）：主数据链（collector → SQLite → metrics → dashboard/api/notify）运转良好，但**功能型子系统**（peakflow / shift / member_limit / writeforecast）各自为政：

- 启动方式四种：`-m peakflow.main --fetch`、runpy+sys.path 注入（shift）、manager 内后台线程 import 调用、裸脚本直跑（writeforecast）
- manager.py 约 1450 行，其中 4 个 bespoke 页面（预测/接待上限/数据补全/明细导出）各占 ~60-130 行，表单/运行/进度/完成回调全部重复
- 新增子系统的成本 = 新目录 + 新启动 hack + 新 bespoke 管理器页 + 私有配置/定时逻辑

用户确认的动机：**结构分散难维护** + **为功能型扩展做准备**（未来会继续加排班/预测/成员管理类子系统）。

## 范围

**纳入**：peakflow、shift、member_limit、writeforecast 四个功能型子系统 + manager.py 中它们的页面与启动逻辑。

**不动**：collector / dashboard / api 主数据链（含 manager 中数据补全页、明细导出页、日志页、主进程守护页）；各子系统内部实现代码；config.yaml 键结构；测试风格（plain assert）。

**兼容底线**（用户确认）：
- `data/`、`output/`、`data/预估流入量.csv` 等数据与产物路径不变
- Windows 计划任务 `AutoWFM_Forecast` 命令行（`python -m peakflow.main --fetch`）、企微 webhook、API :8081 不变
- config.yaml / .env 现有键继续可用
- manager.py UI 布局允许变化（不作为兼容约束）

**进程模型**（用户确认）：保留多进程 + manager 守护，只做插件化统一，不合并进程、不引入任务队列。

## 设计

### 1. 目录与包结构

保持四个顶层目录**不移动**（移动 peakflow 会破坏计划任务命令行）。只统一「可启动性」：

| 子系统 | 现状 | 改后 |
|---|---|---|
| peakflow | `python -m peakflow.main --fetch` ✅ | 不动；`main.py` 补 `main(argv) -> int` 退出码 |
| member_limit | manager 内线程 import 调用 | 包内补 `__main__.py`，manager 与 CLI 共用同一 `run(limit)` 入口 |
| shift | manager 用 runpy + sys.path hack 启动 | 包内补 `__main__.py`（内部做 sys.path 自保）；manager 改为普通 `python -m shift` ManagedTask，删除 `_SHIFT_PRE_RUN` hack |
| writeforecast | 两个独立裸脚本 | 转为包：`__init__.py` + `__main__.py`（子命令 `forecast` / `shifts`，薄包装，脚本本体几乎不动） |

### 2. 子系统契约：taskspec.py

每个功能型子系统包根放一个 `taskspec.py`，暴露一个 `TASK: dict`：

```python
# member_limit/taskspec.py 示例
TASK = {
    "key": "member_limit",
    "title": "接待上限",
    "kind": "bg",                      # "bg"=管理器后台线程 | "service"=守护子进程
    "callable": "member_limit.main:run",
    "params": [
        {"name": "limit", "label": "接待上限", "type": "int", "required": True},
    ],
    "schedulable": True,               # 附带时段配置（复用现有定时机制）
}
```

字段约定：
- `key` / `title`：注册键与页面标题
- `kind`：`bg`（oneshot，后台线程跑完出摘要）| `service`（长驻进程，走 ManagedTask 守护+熔断）
- `callable`：`"pkg.mod:func"` 形式（仅 bg）；函数签名 `**params -> str`（摘要文本），异常即失败
- `params`：有序参数声明，`type` ∈ `int | str | select`（select 给 `choices`）
- `schedulable`：可选，声明后管理器提供定时时段 UI
- `url`：可选（仅 service），页面显示「打开页面」

刻意不定：子系统间依赖、异步流式进度（现有 `_run_bg` 的线程+回调已够）、版本协商。

### 3. manager.py 通用任务页

新增 `task_registry.py`（根目录，~100 行）：

- `discover(packages=("peakflow", "shift", "member_limit", "writeforecast")) -> list[dict]`：importlib 加载各 TASK
- `validate(spec)`：callable 可导入、param 字段合法、`schedulable` 任务的定时时段配置（来自 config.yaml）可被 `parse_schedule` 解析；**单任务校验失败 → 跳过该任务 + manager 日志告警，不影响其他任务与主数据链页面**

`manager.py`：

- 删除 `_build_forecast_page`、`_build_member_limit_page` 及配套 `_run_forecast` / `_on_forecast_done` / `_forecast_summary` / `_manual_member_limit` / `_run_member_limit` / `_on_member_limit_*` 等（约 250 行）
- 新增一个 `_build_task_page(page, spec)`：
  - 按 `params` 自动生成表单（int/str → Entry，select → Combobox）
  - 「运行」→ 校验参数 → kwargs → 复用现有 `_run_bg` → 完成显示摘要 / 失败显示错误
  - `schedulable` 时附定时时段 UI：`parse_schedule` / `schedule_action` / 时段检查循环从 member_limit 专用提到通用层
  - `kind="service"` → 页面 = 启动/停止/重启 + 状态 + 「打开页面」（走现有 ManagedTask）
- 主数据链页面（采集器/看板/API 进程、数据补全、明细导出、日志）原样保留

**扩展性兑现点**：以后新增功能型子系统 = 建包 + 写 taskspec.py，manager 零改动。

### 4. 错误处理与测试

错误处理（全部复用现有机制）：

- bg 任务异常 → 现有 `_run_bg` 回调捕获 → 页面显示失败原因 + `logs/manager.log`
- 定时任务失败 → 现有熔断/弹窗告警
- taskspec 加载/校验失败 → 跳过 + 日志告警（隔离）
- `__main__.py` 的 `main()` 返回 int 退出码，非零即失败

测试（plain assert，无 pytest）：

- `tests/test_taskspec.py`：四个 TASK 声明齐全、字段合法、callable 可导入、schedules 可被 `parse_schedule` 解析
- `tests/test_task_registry.py`：discover 加载 / 坏 spec 隔离 / 参数校验
- `tests/test_manager.py` 增补：通用页参数解析、schedule 通用化回归
- 各 `__main__.py`：`--help` 级冒烟断言（不起真实任务）
- 现有全部 `tests/test_*.py` 必须继续通过

## 明确不做（YAGNI）

- 不引入 click / pydantic / 插件框架（dict + importlib 足够）
- 不统一四个子系统内部代码风格
- 不改 config.yaml 键结构、不动数据链任何文件
- 不做子系统间依赖编排、任务队列、Web 版管理器

## 验收条件

1. `python -m peakflow.main --fetch`、`python -m member_limit`、`python -m shift`、`python -m writeforecast forecast|shifts` 四种启动方式均可用且退出码正确
2. Windows 计划任务 `AutoWFM_Forecast` 不修改即继续工作
3. manager 中四个功能型页面由注册表生成，手动运行/定时/启停行为与现状一致
4. 测试中以夹具形式注册一个最小子系统（仅包+taskspec，不入正式目录），验证 manager「零改动出页面并运行」的扩展点成立
5. 全部 `tests/test_*.py` 通过
