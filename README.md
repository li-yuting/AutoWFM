# AutoWFM — 呼叫中心承接情况自动采集与看板

定时采集呼叫中心各线路的 WebSocket 监控数据与 CRM 明细导出，存入 SQLite，并提供只读网页看板与排班工具。**采集、看板、API、排班是相互独立的进程**：通过 `data/` 目录（采集写、看板/API 读）组织，由 `manager.py`（桌面监管器）统一管理。

## 功能总览

| 子系统 | 入口 | 职责 |
|--------|------|------|
| 采集器 | `python -m collector.main` | 每 5 分钟按各自时间窗口采集 7 路 WS 监控 + 2 路 CRM 明细（会话记录/工单明细），写入 `data/*.db` |
| 看板 | `python -m dashboard.app` | 只读 Flask 网页（:8080），经 `api_client.py` 调 FastAPI 渲染 9 个库；API 不可用时降级直连 `queries.py` |
| API | `python -m api.app` | FastAPI 只读层（:8081），看板与第三方的统一数据出口 |
| 排班 | `manager.py` 监管 | `shift/` Flask 子项目：排班计划导入、校验、生成 |
| 管理器 | `python manager.py` | Tkinter 桌面监管器：自动启停/崩溃重启采集器/API/看板/排班，含进线量预测页、次日差异告警、数据补全页 |
| 周度预估转换 | `writeforecast/` | 周度预估 Excel → `data/预估流入量.csv` |

入口（`collector`/`dashboard`/`api`）**必须用 `-m` 运行**：模块内使用 `from collector import ...` / `from dashboard import ...`，直接运行 `.py` 文件会把子目录（而非项目根）放进 `sys.path`，报 `ModuleNotFoundError`。

## 目录结构

```
AutoWFM/
├── collector/          # 采集器（写 data/*.db）
│   ├── main.py         # 入口：加载 config.yaml、日志、启动调度器
│   ├── scheduler.py    # APScheduler：ws_job + detail_job，按源时间窗口
│   ├── ws.py           # 7 路 WebSocket 采集 + 指标提取
│   ├── detail.py       # 2 路 CRM 明细导出 + Excel 解析 + 按组计数
│   ├── repository.py   # Repository 存储抽象（SQLite 实现，SCHEMAS 单一事实源）
│   ├── storage.py      # 兼容层：re-export repository.SCHEMAS / SQLiteRepository
│   ├── backfill.py     # 历史数据补全
│   ├── notify.py       # 企微告警 + 定时报告 + 看板截图
│   ├── forecast.py     # 进线量预测（7 天）+ 次日差异告警
│   └── _utils.py       # config/.env 加载、时间工具
├── dashboard/          # 看板（只读）
│   ├── app.py          # Flask 路由 + Bearer 认证
│   ├── api_client.py   # FastAPI（:8081）客户端，失败抛 ApiUnavailableError
│   ├── queries.py      # 数据层：小时/按日聚合（增量、均值、预测）
│   └── templates/dashboard.html
├── api/                # FastAPI 只读层（:8081）
│   └── app.py
├── shift/              # 排班子项目（Flask：计划导入/校验/生成）
├── writeforecast/      # 周度预估 Excel → CSV 转换
├── tests/              # 纯 assert 测试（test_*.py）+ 联网 smoke.py
├── manager.py          # 桌面监管器（Tkinter + 托盘）
├── config.yaml         # 采集/告警/预测配置（git-ignored）
├── config.example.yaml # 无密钥配置模板
├── .env / .env.example # 密钥（git-ignored）/ 模板
├── holidays.txt        # 预测节假日
└── AGENTS.md           # 仓库开发指南
```

## 快速开始

Windows + PowerShell。Python 3.14 位于 `.venv`，始终使用其解释器：

```powershell
$env:PYTHONIOENCODING="utf-8"   # 输出中文前必须设置

# 采集器（长驻，每 5 分钟一轮，按源分窗口）
.\.venv\Scripts\python.exe -m collector.main

# 看板（另一进程）http://127.0.0.1:8080
.\.venv\Scripts\python.exe -m dashboard.app

# API 只读层 http://127.0.0.1:8081
.\.venv\Scripts\python.exe -m api.app

# 桌面管理器统一监管 4 个子进程
.\.venv\Scripts\python.exe manager.py

# 一次性全量进线量预测 CSV（输出到 output/）
.\.venv\Scripts\python.exe -m collector.forecast
```

### 测试

纯 `assert`，无 pytest，逐文件直接运行：

```powershell
.\.venv\Scripts\python.exe tests\test_storage.py   # 单个测试
# 全部测试：
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

`tests/smoke.py` 为联网冒烟测试（真实访问 WS/requests 端点），**不属于 CI**。

## 配置与安全

- 密钥（token / webhook / 看板 Token）一律放 `.env`（git-ignored），经 `load_dotenv()` 注入；模板见 `.env.example` 与 `config.example.yaml`。
- 常用环境变量：`AUTOWFM_TOKEN` / `AUTOWFM_TENEMENT_ID`（CRM 导出）、`AUTOWFM_WEBHOOK_MAIN` / `AUTOWFM_WEBHOOK_SECONDARY`（企微）、`AUTOWFM_DASH_TOKEN`（看板/API Bearer，留空=本地无认证）。
- 业务配置（端点、时间窗口、线路 subs、告警阈值、预测参数）在 `config.yaml`。

## 数据说明

- 9 个独立 SQLite（每源一库，表 `t`，5 分钟时间序列）：热线、12378、热线明细、常规、贷后、12378明细、在线、会话记录、工单明细。
- 看板日视图按小时聚合增量，月视图取每日收盘值；预测量来源（CSV / 12378 历史 / 回归）详见 `AGENTS.md`。
- 部分库存在历史数据缺口（如工单明细/会话记录自 2026-07-24 起），看板对缺失渲染「无数据」而非报错。