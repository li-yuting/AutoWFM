# 接待上限批量修改接入 manager.py — 设计文档

## 概述

将 AutoConjurer 项目的 `recording/set_member_limit.py`（腾讯云联络中心成员接待上限批量修改，Playwright 自动化）功能移植进 AutoWFM，作为 `member_limit/` 独立包（仿 `peakflow/`），并集成到 `manager.py` 桌面监管器：新增「接待上限」页，支持手动执行、实时日志、逐人取消，以及**两个相互独立的单次预约时间入口**（每行 = 时间 + 上限值）。

## 背景与动机

- 原脚本位于独立项目 `D:\PythonProject\AutoConjurer\recording\set_member_limit.py`，非 git 仓库、凭据明文存放、无 UI。
- AutoWFM 是统一监管入口（manager.py），希望把该日常运维动作纳入桌面管理器，避免跨项目路径耦合。
- AutoWFM 的 `.venv` 已安装 playwright 1.61（notify.py 截图在用），无需新增依赖。

## 需求决策（已与用户确认）

| 问题 | 结论 |
|------|------|
| 代码位置 | 移植进 AutoWFM 仓库，独立模块 `member_limit/`，manager.py 用**进程内工作线程**调用（仿 forecast/backfill 页） |
| 凭据位置 | 账号密码进 `.env`（`AUTOWFM_QCLOUD_ACCOUNT` / `AUTOWFM_QCLOUD_PASSWORD`），git-ignored |
| limit 来源 | `manager.py` UI 输入框（含预约行的各自上限），config.yaml 提供默认值 |
| 名单位置 | config.yaml 的 `member_limit.members`（41 人） |
| 浏览器 | **headless 无头** + 持久 profile（`member_limit/chrome_profile/`），首次自动用 .env 凭据登录并持久化会话 |
| UI 形态 | 仿「数据补全」页：上限输入框 + 实时日志流 + 汇总 |
| 预约语义 | **两个独立预约行**，每行 = HH:MM 时间 + 上限值；单次预约，到点执行一次后自动清除；错过即跳过 |
| 取消 | 「停止」按钮，逐人可中断，保留已处理结果 |

## 目录结构

```
member_limit/                  # 新增：接待上限批量修改模块
├── __init__.py               # 导出 run_member_limit()
├── core.py                   # Playwright 自动化核心（由 set_member_limit.py 重构）
├── config.py                 # 配置加载：config.yaml 段 + .env 凭据 + 校验
├── main.py                   # CLI：python -m member_limit.main [--limit N] [--dry-run]
└── chrome_profile/           # 持久登录态（git-ignored，含 Cookie）
```

## 组件设计

### member_limit/config.py

职责：
- 读 `config.yaml` 的 `member_limit:` 段：`url`（默认 `https://desk.qcloud.com/`）、`limit`（默认值，UI 预填）、`members`（名单）、`headless`（默认 true，排障可临时改 false）。
- 读 `.env` 的 `AUTOWFM_QCLOUD_ACCOUNT` / `AUTOWFM_QCLOUD_PASSWORD`。
- `load() -> dict`，含校验：名单非空、账号/密码齐全；缺失抛 `ConfigError`（带中文提示）。

### member_limit/core.py

核心函数（纯逻辑可单测）：

```python
def run_member_limit(
    config: dict,          # url / account / password / members / limit / headless
    progress_cb=None,      # callable(str) → 每行进度（登录/翻页/逐人结果）
    should_cancel=None,    # callable() -> bool → 每处理完一人检查
) -> dict:                 # {changed, already, unverified, failed, not_found, cancelled}
```

流程（沿用原 set_member_limit.py 语义）：
1. `chromium.launch_persistent_context(user_data_dir=profile, channel="chrome", headless=config["headless"])`。
2. 访问 url；URL 含 `/login` 则用账号密码自动填表登录（会话持久化到 profile）。
3. 进入「成员」页，按页（最多 20 页防死循环）逐人处理：定位行 → 读当前上限 → 已达标跳过 / 否则点编辑 → 勾选个人接待上限 → 填目标值 → 完成 → 回读校验。
4. **每处理完一人先调 `should_cancel()`**，为 True 停止后续，返回 `cancelled=True`。
5. 汇总五类结果；浏览器/登录异常向上抛，不静默吞。

### member_limit/main.py（CLI）

- `python -m member_limit.main --limit 3`：脱离 GUI 运行同一逻辑。
- `--dry-run`：只遍历并打印每人当前上限，**不实际修改**（安全网）。
- 日志走 stdout（可在 GUI 外调试）。

### manager.py UI —— 「接待上限」页

导航新增一项（位于「数据补全」之后），仿「数据补全」页。

**上半区（手动执行）**：
- 上限输入框（预填 config.yaml 的 `member_limit.limit`）
- 成员数显示（`len(config.yaml 名单)`）
- 「开始执行」按钮 + 「停止」按钮 + 状态标签
- 实时日志框（ScrolledText，每处理一人追加一行，完成输出五类汇总）

**下半区（两个独立预约行）**：
- 每行：启用勾选框 + HH:MM 时间框 + 上限值框 + 状态标签（等待预约 / 已执行 / 已过期 / 已取消 / 执行中）
- 单次预约：到点执行一次后自动清除（勾选复位、状态「已执行」）；错过（管理器未运行）→「已过期」，不补跑

**调度机制**（复用现有 5 秒周期 `_refresh()`）：
- 每 tick 检查每行「启用 && now ≥ 预约时间 && 未执行」→ 用该行 limit 触发。
- 手动按钮与预约触发共用同一 `_run_member_limit(limit, label)` 工作线程；互斥锁防重入（已有执行时跳过新触发并在日志提示）。
- 按钮状态联动：执行中禁用「开始执行」、启用「停止」。

**取消机制**：点「停止」置取消标记；工作线程每处理完一人检查，随后退出并输出**已处理部分**的汇总（含 `cancelled=True` 提示），不丢已生效结果。

**日志**：实时日志同时追加 `logs/member_limit.log`（文件持久化）。

## 数据流

```
用户操作（手动/预约到点）
      │
      ▼
manager.py _run_member_limit(limit, label)   [互斥锁防重入]
      │  threading.Thread(worker)
      ▼
member_limit.core.run_member_limit(config, progress_cb, should_cancel)
      │  progress_cb → root.after(0, 追加日志)
      ▼
汇总 dict → 日志框 + logs/member_limit.log + 状态标签
```

## 错误处理

- 凭据缺失 / 名单为空 → 日志框提示，不启动浏览器。
- 登录失败（无头风控）→ 明确报错 + 提示临时改 `member_limit.headless=false` 排障。
- 单成员编辑异常 → 计入 failed，继续下一个（与原脚本一致）。
- 调度触发时已有执行在跑 → 跳过本次触发并在日志提示。

## 配置示例

```yaml
# config.yaml 新增
member_limit:
  url: "https://desk.qcloud.com/"
  limit: 3            # UI 预填默认值（实际值以 UI 输入框为准）
  headless: true      # 排障可临时改 false（可见窗口）
  members:
    - "雷博"
    - "蒙静"
    # ... 41 人
```

```ini
# .env 新增（.env.example 同步注释占位）
AUTOWFM_QCLOUD_ACCOUNT=
AUTOWFM_QCLOUD_PASSWORD=
```

## 安全

- 账号密码只进 `.env`（git-ignored），config.yaml 不放任何密码。
- `.gitignore` 新增 `member_limit/chrome_profile/`（持久登录态含 Cookie）。
- `logs/member_limit.log` 已被 `*.log` 覆盖忽略。

## 测试（纯 assert、进 CI，遵循仓库约定）

- `tests/test_member_limit_config.py` — 配置加载 / 校验 / 缺凭据报错。
- `tests/test_member_limit_core.py` — 无浏览器的纯逻辑：cancel flag、汇总聚合、配置透传、dry-run 模式。
- 真实浏览器流程不入 CI（仿 smoke.py，属联网冒烟）。

## 配套改动

- `config.yaml` / `config.example.yaml`：加 `member_limit:` 段。
- `.env` / `.env.example`：加 `AUTOWFM_QCLOUD_ACCOUNT` / `AUTOWFM_QCLOUD_PASSWORD`。
- `manager.py`：新增「接待上限」页 + 调度/取消逻辑。
- `.gitignore`：加 `member_limit/chrome_profile/`。
- `AGENTS.md` / `README.md`：各补一段说明。
- `requirements.txt`：无需新增（playwright 已有）。

## 范围外（不做）

- 不做成员名单的 UI 内编辑（名单只从 config.yaml 读取）。
- 不做每日重复预约（仅单次）。
- 不改造原 AutoConjurer 项目（代码迁移后不再依赖它）。
