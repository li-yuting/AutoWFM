# -*- coding: utf-8 -*-
import sys, os, shutil, time, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
from collector import scheduler

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_notify.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")

def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d

def _cfg(data_dir):
    return {
        "schedule": {"timezone": "Asia/Shanghai", "window_start": "00:00", "window_end": "23:59"},
        "storage": {"dir": data_dir},
        "ws": {"gap_alert_threshold": 2, "backfill_retry_delay": 0},
        "subs": [{"name": "热线",
                  "schedule": {"weekday": {"start": "00:00", "end": "23:59"},
                               "weekend": {"start": "00:00", "end": "23:59"}}}],
        "notify": {"webhook": {"main_key": ""}},
    }

_OK_VAL = {"转人工量": 1, "接通量": 1, "排队量": 0, "累计呼入量": 1, "外呼量": 0, "外呼接通量": 0}

def test_ws_job_gap_alert_recovers():
    d = _tmp()
    cfg = _cfg(d)
    fixed = datetime.datetime(2026, 8, 25, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    alerts = []
    scheduler._ws_gap_counters.clear()
    scheduler._GAP_ALERTED.clear()
    scheduler._last_ws_cycle = None
    try:
        with patch.object(scheduler, "_now", return_value=fixed), \
             patch.object(scheduler, "time", MagicMock()), \
             patch.object(scheduler, "_send_gap_alert", side_effect=lambda c, m: alerts.append(m)), \
             patch.object(scheduler.notify, "check_alerts", return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                # 周期1: 首轮失败 + 补采失败 -> counter=1
                with patch.object(scheduler.ws_mod, "collect_one", return_value=None):
                    scheduler.ws_job(cfg, pool)
                assert scheduler._ws_gap_counters.get("热线", 0) == 1, "周期1 应计一次失败"
                assert len(alerts) == 0
                # 周期2: 再失败达阈值 -> 告警一次
                with patch.object(scheduler.ws_mod, "collect_one", return_value=None):
                    scheduler.ws_job(cfg, pool)
                assert len(alerts) == 1, "周期2 应触发一次告警"
                assert "热线" in scheduler._GAP_ALERTED
                # 周期3: 恢复正常, 首轮直接成功 -> 应复位
                with patch.object(scheduler.ws_mod, "collect_one", return_value=_OK_VAL):
                    scheduler.ws_job(cfg, pool)
                assert scheduler._ws_gap_counters.get("热线", 0) == 0, "恢复后计数应清零"
                assert "热线" not in scheduler._GAP_ALERTED, "恢复后应从 _GAP_ALERTED 移除"
                # 周期4-5: 再次连续失败达阈值 -> 应再次告警
                for _ in range(2):
                    with patch.object(scheduler.ws_mod, "collect_one", return_value=None):
                        scheduler.ws_job(cfg, pool)
                assert len(alerts) == 2, "恢复后再失败应再次告警"
    finally:
        scheduler._ws_gap_counters.clear()
        scheduler._GAP_ALERTED.clear()
        scheduler._last_ws_cycle = None
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    test_ws_job_gap_alert_recovers()
    print("test_scheduler OK")
