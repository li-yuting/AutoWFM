# AutoWFM — 承接情况自动采集与看板

定时采集呼叫中心 7 路 WebSocket 监控源 + 2 路 requests 明细导出,存入 9 个 SQLite 库;另有一个只读看板把数据可视化成网页。**采集与看板是两个独立进程**,共享 `data/` 目录(采集写、看板读,SQLite 允许并发读,互不阻塞)。

## 功能

| 子系统 | 入口 | 职责 |
|--------|------|------|
| 采集器 | `python -m collector.main` | 每 5 分钟在各自时间窗口内采集 7 路 WS + 2 路 Excel 明细,写入 `data/*.db` |
| 看板 | `python -m dashboard.app` | 只读 Flask + Chart.js 网页,渲染 9 个库,5 分钟自动刷新 |
| 管理器 | `python manager.py` | 桌面监管采集器/看板子进程(自动启停、崩溃重启),含进线量预测页与重启控制台 |

两个入口都**必须用 `-m`** 运行:模块内部用 `from collector import ...` / `from dashboard import ...`,直接 `python collector/main.py` 或 `python dashboard/app.py` 会让子目录(而非项目根)进入 `sys.path`,报 `ModuleNotFoundError`。

## 目录结构

```
AutoWFM/
├── collector/          # 采集器(写 data/*.db)
│   ├── main.py         # 入口:加载 config.yaml、日志、启动调度器
│   ├── scheduler.py    # APScheduler:WS 任务 + requests 任务,窗口 guard
│   ├── ws.py           # 7 路 WebSocket 采集 + 指标提取
│   ├── detail.py       # 2 路 requests 明细下载 + Excel 解析 + 按组计数
│   ├── storage.py      # 9 库 SQLite 写入(SCHEMAS 定义各库列)
│   ├── notify.py       # 企微告警 + 定时报告(排队阈值告警、15 分钟推送、Playwright 截图)
│   └── forecast.py     # 进线量预测(7 天) + 次日差异告警;run_forecast 供管理器手动触发
├── dashboard/          # 看板(只读 data/*.db + 预估流入量.csv)
│   ├── app.py          # Flask 路由:日视图 / 月视图
│   ├── queries.py      # 数据层:小时/按日聚合(增量、均值、预测)
│   └── templates/dashboard.html
├── tests/              # 纯 assert,无 pytest,逐文件直接跑
├── data/               # 9 个 *.db + 预估流入量.csv(运行时生成)
├── logs/               # 采集日志(按天轮转,保留 30 天)
├── docs/superpowers/   # 设计 spec 与 plan(时间快照)
├── archive/            # 非生产文件:一次性探针脚本、scratch notebook
├── manager.py          # 桌面管理器(Tkinter+托盘):监管采集器/看板 + 进线量预测页 + 重启
├── config.yaml         # 采集配置(端点、调度窗口、7 subs、2 detail_modes、密钥)
└── CLAUDE.md           # 给 Claude 的详细架构说明(契约、边界、已知行为)
```

## 快速开始

Windows + PowerShell。Python 3.14 在 `.venv` 里,始终用它:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING="utf-8"   # 输出中文前必须设

# 跑采集器(长驻,每 5 分钟一轮,按源分窗口,阻塞前台)
python -m collector.main

# 跑看板(另一进程)  http://127.0.0.1:8080
python -m dashboard.app

# 或用桌面管理器统一监管采集器+看板(含进线量预测页、重启控制台)
.\.venv\Scripts\python.exe manager.py
```

### 测试

纯 `assert`,不用 pytest,每个文件直接跑(测试自带 `sys.path` 指向项目根,可在任意目录运行):

```powershell
python tests/test_storage.py
python tests/test_ws.py
python tests/test_detail.py
python tests/test_guard.py
python tests/test_notify.py
python tests/test_manager.py
python tests/test_dashboard_queries.py
python tests/test_dashboard_app.py

# 一次性跑全部:
Get-ChildItem tests\test_*.py | ForEach-Object { python $_.FullName }
```

`tests/smoke.py` 是**联网**一次性烟测(打全部 7 WS + 2 requests,各写一行),离线不会过。

## 架构要点(精简)

- **采集**:两个独立 APScheduler job——`ws_job`(7 线程)与 `detail_job`(2 线程),同一 5 分钟触发器但分开,避免慢的 Excel 下载阻塞 WS 采集。窗口**按源**判断(`12378`/`12378明细` 工作日 8:30–21:00、周末 9:00–18:00,其余走全局 9:00–21:00),窗口外的源在当 tick 跳过。WS 每周期连→发订阅→收首个匹配帧→提取→关(非长连,适配 5 分钟节奏)。
- **存储**:9 个独立 SQLite,每源一库一表 `t`,5 分钟时间序列;每次写入开/关连接,9 路各写各的库无锁竞争。
- **看板**:日视图图表/表格展示**每小时增量**(计数器隔夜归零,首小时取基线;瞬时量如签入/空闲/在线取小时均值);卡片展示截止当前的**累计**值。月视图用每日收盘值(=当日总量)。
- **预测量**有三个来源,勿混为一谈:热线/在线 读 `data/预估流入量.csv`;12378 无 CSV,用 7 天前同时段转人工量;CSV 区间外或 12378 不足 7 天历史时预测为 0/None。
- **进线量预测**:`collector/forecast.py` 用热线/在线每日 21:00 累计转人工量做 OLS 回归预测未来 7 天转人工量;21:05 定时跑次日差异告警(对比 `预估流入量.csv`,超阈值发企微),也可在 `manager.py` 控制台手动触发。`manager.py` 是可选桌面监管器,自动启停/崩溃重启采集器与看板子进程。

> 数据契约、group→source 映射、`:00` 模型、主题配色等细节见 `CLAUDE.md`。

## 数据缺口(非 bug)

- `工单明细.db` / `会话记录.db` 自 2026-07-24 才有数据(此前外呼各组 + 12378回访组为空)。
- 12378 预测需 7 天前历史,故 07-01…07-07 无 12378 预测量。
- CSV 预测覆盖 2026-06-01…2026-08-15,区间外热线/在线无预测量。
- 看板对缺失数据渲染为「无数据」,不报错。
