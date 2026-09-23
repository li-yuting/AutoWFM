# 管理器后台静默日志清理设计

日期：2026-09-23
状态：已确认
范围：`collector/maintenance.py`、`manager.py`、相关测试与文档

## 目标

管理器每天本地时间 05:00 在后台静默清理 `logs/` 中超过 30 天的轮转归档。
当天 05:00 后才启动管理器时补跑一次；同日每个管理器进程最多触发一次。
不新增状态文件、计划任务或依赖。

## 行为

- 只在内存保存最近触发日期；同日重启后可能重新扫描，但删除操作幂等。
- 只匹配 `logs/` 顶层的 `x.log.YYYY-MM-DD` 与 `x.log.N`。
- 正在写入的 `.log`、其他目录、`output/` 和 `data/*.db` 均不处理。
- 自动任务不弹窗、不更新 UI，只向 `logs/manager.log` 写一行结果。
- 单个文件删除失败时记录错误并继续处理其他文件。

## 接口

- `collector.maintenance.prune_logs(log_dir, keep_days=30, now=None) -> list[dict]`
- `manager.log_prune_due(now, last_run) -> bool`

已删除 `scan()`、`run()`、`prune_output()`、`compact_databases()` 及磁盘维护页。

## 测试

- 删除两类过期轮转归档，保留近期归档、活动日志、无关文件和嵌套文件。
- `output/` 与 SQLite 文件在日志清理前后保持不变。
- 04:59 不触发；05:00、晚启动和跨天触发；同日及后台运行期间不重复。
- 管理器导航不再包含「磁盘维护」。
