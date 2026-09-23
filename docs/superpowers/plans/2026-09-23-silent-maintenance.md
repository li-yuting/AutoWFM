# 管理器后台静默日志清理实施计划

## 目标

管理器每天 05:00 静默清理过期日志，删除磁盘维护 UI，保持 `output/` 与数据库不变。

## 实现

1. 收口 `collector/maintenance.py`，只保留 `human()` 与直接执行的 `prune_logs()`。
2. 删除 `scan()`、`run()`、`prune_output()`、`compact_databases()` 及相关辅助代码。
3. 在 `manager.py` 增加 `log_prune_due()` 和进程内去重状态，接入 `_refresh()` 后台线程。
4. 删除「磁盘维护」导航、页面、报告及确认框，成功或失败只写管理器日志。
5. 更新 README、AGENTS、配置注释和设计文档。

## 验证

1. 运行 `.\.venv\Scripts\python.exe tests\test_maintenance.py`。
2. 运行 `.\.venv\Scripts\python.exe tests\test_manager.py`；本机缺少 Tcl/Tk 时至少运行无 UI 的目标测试。
3. 运行全部 `tests/test_*.py`。
