# -*- coding: utf-8 -*-
"""manager.py 调度纯函数测试:plain assert,直接 `python tests/test_manager.py`。"""
import datetime as dt
import os, sys
from pathlib import Path
import tkinter as tk
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from manager import (compute_auto_start, auto_stop_minutes, in_run_window, schedule_text,
                       ManagerUI, ManagedTask, GRACE_SECONDS, parse_schedule, schedule_action,
                       load_auto_start_state, save_auto_start_state)

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


def test_parse_schedule():
    assert parse_schedule("18:00") == 18 * 60
    assert parse_schedule("09:30") == 9 * 60 + 30
    assert parse_schedule("00:00") == 0
    assert parse_schedule("") is None
    assert parse_schedule("abc") is None
    assert parse_schedule("25:00") is None
    assert parse_schedule("12:60") is None
    print("parse_schedule OK")


def test_schedule_action():
    t = 18 * 60
    assert schedule_action(False, t, t, False) == "idle"          # 未启用
    assert schedule_action(True, t - 30, t, False) == "wait"      # 未到点
    assert schedule_action(True, t, t, False) == "run"            # 到点触发
    assert schedule_action(True, t + 30, t, False) == "expired"   # 时间已过
    assert schedule_action(True, t, t, True) == "idle"            # 已触发过
    assert schedule_action(True, t, None, False) == "idle"        # 时间非法
    print("schedule_action OK")


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
    assert len(events) == 1 and "已自动拉起" in events[0]["msg"]
    print("tick_auto_start OK")


def test_tick_auto_stop():
    task = _make_task()
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        assert task.is_running()
        events = task.tick(False, dt.datetime(2026, 8, 1, 21, 30, tzinfo=SH))
    assert not task.is_running(), "窗口外 tick 应自动停止"
    assert len(events) == 1 and "已自动停止" in events[0]["msg"]
    # 勾选状态下窗口外手动再启 -> 收敛模型:再次被停止(原"只停一次"怪癖废除)
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.start()
        events2 = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert not task.is_running(), "勾选状态下窗口外手动启动应被自动停止"
    assert len(events2) == 1 and "已自动停止" in events2[0]["msg"]
    print("tick_auto_stop OK")


def test_tick_no_duplicate_auto_start():
    task = _make_task()
    with patch("manager.subprocess.Popen", return_value=_running_proc()) as popen:
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        spawned = popen.call_count   # Windows: 探测+拉起=2;Linux: 仅拉起=1;只看增量
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
        assert popen.call_count == spawned, "第二拍不应再有任何拉起/探测"
    assert task.is_running()
    assert len(events) == 0, "运行中不应重复拉起"
    print("tick_no_duplicate_auto_start OK")


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
        task.start()
        events = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert task.is_running(), "auto_enabled=False 手动启动后不应被自动停止"
    assert len(events) == 0

    # 崩溃也不应自动重启
    proc.poll.return_value = 1
    events = task.tick(False, dt.datetime(2026, 8, 1, 22, 5, tzinfo=SH))
    assert not task.is_running()
    assert task.restart_failures == 0, "auto_enabled=False 崩溃不应触发自动重启"
    print("tick_manual_only_no_auto_start_stop OK")


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
            assert len(ui._nav_buttons) == 7, f"7 个导航按钮, 实际 {len(ui._nav_buttons)}"
            assert len(ui._nav_pages) == 7, f"7 个内容页, 实际 {len(ui._nav_pages)}"
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


def test_member_limit_schedule_rearm():
    """预约触发后(fired=True)重新勾选启用,应重置 fired 允许第二次预约;
    已勾选但时间未填(idle)不应被静默取消勾选。"""
    with patch.object(ManagerUI, "_build_tray", lambda self: None), \
         patch.object(ManagerUI, "_refresh", lambda self: None):
        root = tk.Tk()
        root.withdraw()
        ui = ManagerUI(root, _cfg())
        try:
            now = dt.datetime(2026, 8, 21, 10, 0)
            # 场景1: 上次已触发,用户重新勾选 -> 重新武装进入等待
            st = ui._ml_sched[0]
            st["enabled"].set(True)
            st["time"].set("11:00")
            st["limit"].set("3")
            st["fired"] = True
            st["status"].set("已执行")
            ui._check_member_limit_schedules(now)
            assert st["fired"] is False, "重新勾选后应重置 fired,允许第二次预约"
            assert st["status"].get() == "等待预约"
            assert st["enabled"].get() is True, "等待到点中不应被取消勾选"
            # 场景2: 勾选了但时间还没填完(idle) -> 不应被静默取消
            st2 = ui._ml_sched[1]
            st2["enabled"].set(True)
            st2["time"].set("")
            ui._check_member_limit_schedules(now)
            assert st2["enabled"].get() is True, "时间未填(idle)不应取消勾选"
            assert st2["fired"] is False
        finally:
            root.destroy()
    print("member_limit_schedule_rearm OK")


def test_tick_manual_stop_relaunch():
    """勾选+窗口内手动停止:收敛模型应在下一拍自动拉回(≤5s)。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        assert task.is_running()
        task.stop()
        assert not task.is_running()
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
    assert task.is_running(), "勾选状态下窗口内手动停止后应被自动拉回"
    assert len(events) == 1 and "已自动拉起" in events[0]["msg"]
    print("tick_manual_stop_relaunch OK")


def test_tick_unchecked_not_stopped_outside_window():
    """未勾选:窗口外在跑也不被自动停止(manager 零干预)。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    task.auto_start = False
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start()
        events = task.tick(False, dt.datetime(2026, 8, 1, 22, 0, tzinfo=SH))
    assert task.is_running(), "未勾选的任务窗口外不应被自动停止"
    assert len(events) == 0
    print("tick_unchecked_not_stopped_outside_window OK")


def test_tick_popen_fail_breaker():
    """Popen 启动失败计入熔断:第 3 次失败当拍告警并暂停拉起;跨天复位后恢复。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    with patch("manager.subprocess.Popen", side_effect=OSError("boom")):
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))            # 失败 1/3
        task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))            # 失败 2/3
        events3 = task.tick(True, dt.datetime(2026, 8, 1, 9, 31, tzinfo=SH))  # 失败 3/3 -> 告警
        events4 = task.tick(True, dt.datetime(2026, 8, 1, 9, 32, tzinfo=SH))  # 已熔断 -> 去重
    assert task.restart_failures == 3
    assert not task.is_running()
    assert len(events3) == 1 and events3[0]["type"] == "alert", "第 3 次失败当拍应告警"
    assert len(events4) == 0, "告警当日去重"
    # 跨天复位熔断,恢复自动拉起
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        task.tick(True, dt.datetime(2026, 8, 2, 8, 35, tzinfo=SH))
    assert task.is_running(), "跨天复位后应恢复自动拉起"
    assert task.restart_failures == 0
    print("tick_popen_fail_breaker OK")


def test_tick_quick_crash_trips_breaker():
    """启动后 <30s 崩溃连续 3 次:熔断告警一次并暂停拉起(uptime 计数路径)。"""
    task = _make_task()
    task._current_date = dt.date.today()
    now = dt.datetime.now().astimezone()
    proc = _running_proc()
    events_all = []
    with patch("manager.subprocess.Popen", return_value=proc):
        for _ in range(3):
            proc.poll.return_value = None      # 复位为运行中,先拉起
            task.tick(True, now)
            proc.poll.return_value = 1         # 5 秒后崩溃(< 30s 宽限)
            events_all.extend(task.tick(True, now + dt.timedelta(seconds=5)))
    assert task.restart_failures == 3, "连续 3 次秒退应计满失败"
    alerts = [e for e in events_all if e["type"] == "alert"]
    assert len(alerts) == 1, "熔断告警只弹一次"
    with patch("manager.subprocess.Popen", return_value=_running_proc()) as popen:
        task.tick(True, now + dt.timedelta(seconds=10))
        popen.assert_not_called(), "熔断后不应再拉起"
    print("tick_quick_crash_trips_breaker OK")


# ── 「自启动」勾选框行为 ─────────────────────────────────────────────

_TMP = Path(__file__).resolve().parent / ".test_tmp"  # gitignored 测试临时目录(勿用系统 %TEMP%)

def test_tick_auto_start_requires_checkbox():
    """未勾选自启动:窗口内不自动拉起;中途勾选后窗口内立即拉起。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)  # 避开跨天重置
    task.auto_start = False
    with patch("manager.subprocess.Popen", return_value=_running_proc()) as popen:
        events = task.tick(True, dt.datetime(2026, 8, 1, 9, 30, tzinfo=SH))
        popen.assert_not_called()
    assert not task.is_running()
    assert len(events) == 0
    task.auto_start = True
    with patch("manager.subprocess.Popen", return_value=_running_proc()):
        events = task.tick(True, dt.datetime(2026, 8, 1, 10, 0, tzinfo=SH))
    assert task.is_running(), "勾选后处于运行时段应立即自启动"
    assert len(events) == 1 and "已自动拉起" in events[0]["msg"]
    print("tick_auto_start_requires_checkbox OK")


def test_tick_no_crash_restart_when_unchecked():
    """未勾选自启动:窗口内进程崩溃不自动重启、不告警、失败计数不涨。"""
    task = _make_task()
    task._current_date = dt.date(2026, 8, 1)
    task.auto_start = False
    proc = _running_proc()
    with patch("manager.subprocess.Popen", return_value=proc):
        task.start()
    proc.poll.return_value = 1
    events = task.tick(True, dt.datetime(2026, 8, 1, 9, 40, tzinfo=SH))
    assert not task.is_running()
    assert len(events) == 0
    assert task.restart_failures == 0
    print("tick_no_crash_restart_when_unchecked OK")


def test_auto_start_state_roundtrip():
    """勾选状态持久化:缺文件/损坏返回空(默认全勾选),保存后读取一致。"""
    _TMP.mkdir(exist_ok=True)
    state_path = _TMP / "manager_state.json"
    state_path.unlink(missing_ok=True)
    with patch("manager.AUTO_START_STATE", state_path):
        assert load_auto_start_state() == {}
        save_auto_start_state({"采集器": False, "API": True, "看板": True})
        assert load_auto_start_state() == {"采集器": False, "API": True, "看板": True}
        state_path.write_text("{broken", encoding="utf-8")
        assert load_auto_start_state() == {}, "损坏文件应回退为空(默认全勾选)"
    state_path.unlink(missing_ok=True)
    print("auto_start_state_roundtrip OK")


def test_ui_auto_start_checkboxes():
    """勾选框只属于采集器/API/看板;切换勾选写任务属性并落盘,新实例恢复。"""
    _TMP.mkdir(exist_ok=True)
    state_path = _TMP / "ui_manager_state.json"
    state_path.unlink(missing_ok=True)
    roots = []
    try:
        with patch("manager.AUTO_START_STATE", state_path):
            root = tk.Tk(); root.withdraw(); roots.append(root)
            ui = ManagerUI(root, _cfg())
            assert set(ui._auto_start_vars) == {"采集器", "API", "看板"}, "排班不应有勾选框"
            ui._auto_start_vars["API"].set(False)
            ui._toggle_auto_start(ui.tasks[1])
            assert ui.tasks[1].auto_start is False
            assert load_auto_start_state() == {"采集器": True, "API": False, "看板": True}
            root2 = tk.Tk(); root2.withdraw(); roots.append(root2)
            ui2 = ManagerUI(root2, _cfg())
            assert ui2.tasks[0].auto_start is True
            assert ui2.tasks[1].auto_start is False, "新实例应恢复未勾选状态"
    finally:
        state_path.unlink(missing_ok=True)
        for r in roots:
            try:
                r.destroy()
            except Exception:
                pass
    print("ui_auto_start_checkboxes OK")


def main():
    test_auto_start_weekday()
    test_auto_start_weekend()
    test_auto_stop()
    test_in_run_window_weekday()
    test_in_run_window_weekend()
    test_schedule_text()
    test_parse_schedule()
    test_schedule_action()
    test_forecast_summary()
    test_tick_auto_start()
    test_tick_auto_stop()
    test_tick_no_duplicate_auto_start()
    test_tick_manual_stop_relaunch()
    test_tick_unchecked_not_stopped_outside_window()
    test_tick_no_crash_restart_when_unchecked()
    test_tick_popen_fail_breaker()
    test_tick_quick_crash_trips_breaker()
    test_tick_manual_only_no_auto_start_stop()
    test_find_external_pid_matches_pythonw()
    test_tick_health_check_clears_failures()
    test_ui_constructs()
    test_update_status_sets_dot()
    test_member_limit_schedule_rearm()
    test_tick_auto_start_requires_checkbox()
    test_auto_start_state_roundtrip()
    test_ui_auto_start_checkboxes()
    print("ALL manager tests OK")


if __name__ == "__main__":
    main()
