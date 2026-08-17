# -*- coding: utf-8 -*-
"""manager.py 调度纯函数测试:plain assert,直接 `python tests/test_manager.py`。"""
import datetime as dt
import os, sys
from pathlib import Path
import tkinter as tk
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from manager import compute_auto_start, auto_stop_minutes, in_run_window, schedule_text, ManagerUI, ManagedTask, GRACE_SECONDS

SH = ZoneInfo("Asia/Shanghai")


def _cfg():
    return {
        "schedule": {"window_start": "09:00", "window_end": "21:00", "timezone": "Asia/Shanghai"},
        "subs": [
            {"name": "热线"},
            {"name": "12378", "schedule": {"weekday": {"start": "08:30", "end": "21:00"},
                                            "weekend": {"start": "09:00", "end": "18:00"}}},
            {"name": "在线"},
        ],
    }


def test_auto_start_weekday():
    now = dt.datetime(2026, 7, 28, 12, 0, tzinfo=SH)   # 周二
    assert compute_auto_start(_cfg(), now) == 8 * 60 + 30   # 最早 12378 08:30


def test_auto_start_weekend():
    now = dt.datetime(2026, 8, 1, 12, 0, tzinfo=SH)    # 周六
    assert compute_auto_start(_cfg(), now) == 9 * 60       # 12378 周末 09:00


def test_auto_stop():
    assert auto_stop_minutes(_cfg()) == 21 * 60           # 全局 window_end 21:00


def test_in_run_window_weekday():
    cfg = _cfg()
    assert in_run_window(cfg, dt.datetime(2026, 7, 28, 8, 29, tzinfo=SH)) is False   # 早于 08:30
    assert in_run_window(cfg, dt.datetime(2026, 7, 28, 8, 30, tzinfo=SH)) is True    # 起点(含)
    assert in_run_window(cfg, dt.datetime(2026, 7, 28, 20, 59, tzinfo=SH)) is True
    assert in_run_window(cfg, dt.datetime(2026, 7, 28, 21, 0, tzinfo=SH)) is False   # 终点(不含)


def test_in_run_window_weekend():
    cfg = _cfg()
    assert in_run_window(cfg, dt.datetime(2026, 8, 1, 8, 30, tzinfo=SH)) is False    # 周末起点 09:00
    assert in_run_window(cfg, dt.datetime(2026, 8, 1, 9, 0, tzinfo=SH)) is True


def test_schedule_text():
    s = schedule_text(_cfg(), dt.datetime(2026, 7, 28, 12, 0, tzinfo=SH))
    assert "每日计划" in s and "工作日" in s and "08:30" in s and "21:00" in s, s
    s2 = schedule_text(_cfg(), dt.datetime(2026, 8, 1, 12, 0, tzinfo=SH))
    assert "每日计划" in s2 and "周末" in s2 and "09:00" in s2, s2


def test_forecast_summary():
    # Path 归一化输出（Windows 为反斜杠、Linux 为正斜杠），断言与实现一致、跨平台
    out = Path("output/x.xlsx")
    s = ManagerUI._forecast_summary(str(out))
    assert f"Excel: {out}" in s, s
    assert f"HTML:  {out.with_suffix('.html')}" in s, s
    print("forecast_summary OK")


# ── ManagedTask.tick() 行为测试 ──────────────────────────────────────

def _make_task():
    """创建不启动真实进程的 ManagedTask 测试实例。"""
    return ManagedTask(name="test", module="test.module", log_path=Path("nul"), capture_log=False)


def _running_proc():
    """返回一个模拟正在运行的子进程。"""
    proc = MagicMock()
    proc.poll.return_value = None
    return proc


def test_tick_auto_start():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        now = dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH)
        events = task.tick(True, now)
    assert task.is_running(), "窗口内首次 tick 应自动启动"
    assert task.auto_started_today is True
    assert len(events) == 1 and "已自动启动" in events[0]["msg"]
    print("tick_auto_start OK")


def test_tick_auto_stop():
    task = _make_task()
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        assert task.is_running()
        events = task.tick(False, dt.datetime(2026, 8, 1, 21, 30, tzinfo=SH))
    assert not task.is_running(), "窗口外首次 tick 应自动停止"
    assert task.auto_stopped_today is True
    assert len(events) == 1 and "已自动停止" in events[0]["msg"]
    # 自动停止后再次 tick（仍在窗口外），不应重复停止
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.start(automatic=False)
        events2 = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert task.is_running(), "已自动停止过，不应再次强制停止"
    assert len(events2) == 0
    print("tick_auto_stop OK")


def test_tick_no_duplicate_auto_start():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
    assert task.is_running()
    assert len(events) == 0, "同一天内不应重复自动启动"
    print("tick_no_duplicate_auto_start OK")


def test_tick_manual_stop_blocks_auto_start():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        assert task.is_running()
        task.stop(automatic=False)
        assert task.user_stopped is True
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
    assert not task.is_running(), "手动停止后当天不应再自动启动"
    assert len(events) == 0
    print("tick_manual_stop_blocks_auto_start OK")


def test_tick_manual_start_outside_window():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))  # 标记 auto_stopped_today
        task.start(automatic=False)
        events = task.tick(False, dt.datetime(2026, 8, 1, 23, 0, tzinfo=SH))
    assert task.is_running(), "窗口外手动启动后不应被自动停止"
    assert len(events) == 0
    print("tick_manual_start_outside_window OK")


def test_tick_no_crash_restart_outside_window():
    task = _make_task()
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
        task.start(automatic=False)
    # 模拟进程崩溃退出
    proc.poll.return_value = 1
    events = task.tick(False, dt.datetime(2026, 8, 1, 22, 5, tzinfo=SH))
    assert task.restart_failures == 0, "窗口外崩溃不应触发自动重启"
    assert len(events) == 0
    print("tick_no_crash_restart_outside_window OK")


def test_tick_user_stopped_reset_on_new_day():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        task.stop(automatic=False)
        assert task.user_stopped is True
        events = task.tick(True, dt.datetime(2026, 8, 2, 9, 30, tzinfo=SH))
    assert task.user_stopped is False, "跨天后 user_stopped 应被清除"
    assert task.is_running(), "跨天后应恢复自动启动"
    assert len(events) == 1 and "已自动启动" in events[0]["msg"]
    print("tick_user_stopped_reset_on_new_day OK")


def test_tick_manual_only_no_auto_start_stop():
    """auto_enabled=False 的任务:不做自动启停、不做崩溃自动重启,完全手动控制。"""
    task = ManagedTask(name="手动-only", module="", log_path=Path("nul"), capture_log=False,
                       script="app.py", auto_enabled=False)
    # 窗口内 tick 不应自动启动(默认关闭)
    with patch("manager.subprocess.Popen") as popen:
        events = task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        popen.assert_not_called()
    assert not task.is_running()
    assert len(events) == 0, "auto_enabled=False 不应产生自动启停事件"

    # 手动启动后,即使窗口外 tick 也不应自动停止
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start(automatic=False)
        events = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert task.is_running(), "auto_enabled=False 手动启动后不应被自动停止"
    assert len(events) == 0

    # 崩溃也不应自动重启
    proc.poll.return_value = 1
    events = task.tick(False, dt.datetime(2026, 8, 1, 22, 5, tzinfo=SH))
    assert not task.is_running()
    assert task.restart_failures == 0, "auto_enabled=False 崩溃不应触发自动重启"
    print("tick_manual_only_no_auto_start_stop OK")


def test_stop_automatic_parameter():
    task = _make_task()
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start()
        task.stop(automatic=True)
    assert task.user_stopped is False, "自动停止不应设置 user_stopped"
    assert not task.is_running()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start()
        task.stop(automatic=False)
    assert task.user_stopped is True, "手动停止应设置 user_stopped"
    print("stop_automatic_parameter OK")


def test_find_external_pid_matches_pythonw():
    """_find_external_pid 须匹配 pythonw.exe:管理器经开机自启以 pythonw.exe 运行,
    子进程也是 pythonw.exe;只认 python.exe 会漏掉 -> 接管失败、重复拉起(已修复)。"""
    task = _make_task()
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = "21704\n"
    with patch("manager.subprocess.run", return_value=fake) as mock_run:
        pid = task._find_external_pid()
    assert pid == 21704, f"应匹配 pythonw.exe 进程,实际 pid={pid}"
    cmd = mock_run.call_args[0][0][3]  # ["powershell","-NoProfile","-Command", cmd]
    assert "pythonw.exe" in cmd, f"查询须含 pythonw.exe: {cmd}"
    print("find_external_pid_matches_pythonw OK")


def test_tick_health_check_clears_failures():
    """运行中进程跑过 grace 后清零失败计数(覆盖 now-started_at;原 _refresh 用 naive now
    与 aware started_at 相减抛 TypeError 使监控循环崩溃,已改为 aware now)。"""
    task = _make_task()
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start()  # started_at = aware
    task.restart_failures = 2
    task.started_at = dt.datetime.now().astimezone() - dt.timedelta(seconds=GRACE_SECONDS + 5)
    now = dt.datetime.now().astimezone()  # aware,与 _refresh 修复后一致
    events = task.tick(True, now)
    assert task.restart_failures == 0, "跑过 grace 应清零失败计数"
    assert task.is_running()
    assert len(events) == 0
    print("tick_health_check_clears_failures OK")


def test_ui_constructs():
    """构造 ManagerUI 不崩溃 + 左侧导航结构正确。
    _build_tray/_refresh mock 成空操作,避免起托盘线程/拉起采集器看板进程。"""
    with patch.object(ManagerUI, "_build_tray", lambda self: None), \
         patch.object(ManagerUI, "_refresh", lambda self: None):
        root = tk.Tk()
        root.withdraw()
        ui = ManagerUI(root, _cfg())
        try:
            assert len(ui._nav_buttons) == 6, f"6 个导航按钮, 实际 {len(ui._nav_buttons)}"
            assert len(ui._nav_pages) == 6, f"6 个内容页, 实际 {len(ui._nav_pages)}"
            assert len(ui._log_boxes) == 4, f"4 个日志框, 实际 {len(ui._log_boxes)}"
        finally:
            root.destroy()
    print("ui_constructs OK")


def test_update_status_sets_dot():
    """_update_status 在运行中状态会把状态点染绿(验证状态点接线)。"""
    with patch.object(ManagerUI, "_build_tray", lambda self: None), \
         patch.object(ManagerUI, "_refresh", lambda self: None):
        root = tk.Tk()
        root.withdraw()
        ui = ManagerUI(root, _cfg())
        try:
            task = ui.tasks[0]
            proc = MagicMock(); proc.poll.return_value = None; proc.pid = 12345
            task.process = proc
            ui._update_status()
            assert ui._vars[0]["status_dot"].cget("fg") == "#16803c", "运行中状态点应为绿 #16803c"
        finally:
            root.destroy()
    print("update_status_sets_dot OK")


def main():
    test_auto_start_weekday()
    test_auto_start_weekend()
    test_auto_stop()
    test_in_run_window_weekday()
    test_in_run_window_weekend()
    test_schedule_text()
    test_forecast_summary()
    test_tick_auto_start()
    test_tick_auto_stop()
    test_tick_no_duplicate_auto_start()
    test_tick_manual_stop_blocks_auto_start()
    test_tick_manual_start_outside_window()
    test_tick_no_crash_restart_outside_window()
    test_tick_user_stopped_reset_on_new_day()
    test_stop_automatic_parameter()
    test_tick_manual_only_no_auto_start_stop()
    test_find_external_pid_matches_pythonw()
    test_tick_health_check_clears_failures()
    test_ui_constructs()
    test_update_status_sets_dot()
    print("ALL manager tests OK")


if __name__ == "__main__":
    main()
