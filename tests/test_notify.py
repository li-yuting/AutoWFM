# -*- coding: utf-8 -*-
import sys, os, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import base64, hashlib
import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import storage, notify

def _cfg(data_dir):
    return {
        "schedule": {"timezone": "Asia/Shanghai", "window_start": "09:00", "window_end": "21:00"},
        "storage": {"dir": data_dir},
        "subs": [
            {"name": "热线"},
            {"name": "12378", "schedule": {"weekday": {"start": "08:30", "end": "21:00"},
                                            "weekend": {"start": "09:00", "end": "18:00"}}},
        ],
        "notify": {
            "screenshot_url": "http://localhost:5001/",
            "webhook": {"main_key": "MAIN", "secondary_key": "SECOND"},
            "alert": {"hotline_queue": 10, "online_queue": 20, "queue_12378": 1,
                      "recipients": {"hotline": ["111"], "online": ["222"], "12378": ["333"]}},
        },
    }

def test_latest_snapshot():
    d = tempfile.mkdtemp()
    storage.insert("热线", {"时间":"2026-07-28 09:05","转人工量":1,"接通量":1,"排队量":0,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1108,"接通量":1106,"排队量":0,"累计呼入量":1187,"外呼量":0,"外呼接通量":0}, d)
    row = notify.latest_snapshot(d, "热线", "2026-07-28")
    assert row["转人工量"] == 1108, row
    assert notify.latest_snapshot(d, "热线", "2026-07-29") is None
    assert notify.latest_snapshot(d, "在线", "2026-07-28") is None  # 无库
    print("latest_snapshot OK")

def test_forecast_at():
    d = tempfile.mkdtemp()
    with open(Path(d)/"预估流入量.csv", "w", encoding="utf-8", newline="") as f:
        f.write("时间,线路,时段预估量,累计预估量\n")
        f.write("2026-07-28 11:00,热线,100,1187\n")
        f.write("2026-07-28 11:00,在线,50,811\n")
    assert notify.forecast_at(d, "热线", "2026-07-28 11:00") == 1187
    assert notify.forecast_at(d, "在线", "2026-07-28 11:00") == 811
    assert notify.forecast_at(d, "热线", "2026-07-28 12:00") == 0
    assert notify.forecast_at(tempfile.mkdtemp(), "热线", "2026-07-28 11:00") == 0  # 无 CSV
    print("forecast_at OK")

def test_render_firstline():
    hot = {"转人工量":1108,"接通量":1106,"排队量":0,"累计呼入量":1187,"外呼量":0,"外呼接通量":0}
    hot_seat = {"签入":87,"通话":38,"空闲":42,"离席":0,"话后":5,"振铃":0,"置忙":0}
    ol = {"转人工量":826,"转人工失败":2,"排队":0,"咨询":75,"在线":32,"小休":5,"示忙":1,"话后":0,"就餐":0,"培训":0,"回访":0}
    s = notify._render_firstline("2026-07-28 11:00", hot, hot_seat, ol, 1187, 811)
    assert "统计监控`热线`" in s, s
    assert ">预测量: 1187, 转人工量：1108" in s, s
    assert ">流入率：93.34%" in s, s          # 1108/1187
    assert ">接通量：1106, 接通率：99.82%" in s, s  # 1106/1108
    assert "统计监控`在线`" in s, s
    assert ">接通量：824, 接通率：99.76%" in s, s  # 826-2=824, 824/826
    assert ">流入率：101.85%" in s, s          # 826/811
    assert ">示忙人数：1, 就餐人数：0" in s, s
    print("render_firstline OK")

def test_render_secondline():
    groups = [
        ("常规转接组", 100, 150, {"签入":17,"通话":8,"空闲":6,"离席":0,"话后":3,"振铃":0,"置忙":0}),
        ("贷后转接组", 100, 150, {"签入":18,"通话":5,"空闲":9,"离席":0,"话后":4,"振铃":0,"置忙":0}),
    ]
    z = {"转人工量":44,"接通量":44,"排队量":0,"累计呼入量":44}
    z_seat = {"签入":6,"通话":1,"空闲":5,"离席":0,"话后":0,"振铃":0,"置忙":0}
    s = notify._render_secondline("2026-07-28 11:15", groups, z, z_seat)
    assert "签入情况`常规转接组`" in s
    assert "签入情况`贷后转接组`" in s
    assert ">转接量：100, 工单量：150" in s
    assert "统计监控`12378`" in s
    assert ">转人工量：44" in s
    assert ">接通量：44, 接通率：100.00%" in s  # 44/44
    print("render_secondline OK")

def test_webhook_success():
    fake = MagicMock(); fake.status_code = 200; fake.json.return_value = {"errcode": 0}; fake.text = "ok"
    with patch.object(notify.requests, "post", return_value=fake):
        assert notify._webhook("K", {"msgtype": "text", "text": {"content": "x"}}) == "ok"

def test_webhook_errcode():
    fake = MagicMock(); fake.status_code = 200; fake.json.return_value = {"errcode": 93000, "errmsg": "bad"}
    with patch.object(notify.requests, "post", return_value=fake):
        r = notify._webhook("K", {})
        assert isinstance(r, str) and "失败" in r

def test_webhook_exception():
    with patch.object(notify.requests, "post", side_effect=Exception("boom")):
        assert "失败" in notify._webhook("K", {})

def test_send_md_payload():
    with patch.object(notify, "_webhook", return_value="ok") as m:
        notify._send_md("hello", "KEY")
        m.assert_called_once_with("KEY", {"msgtype": "markdown", "markdown": {"content": "hello"}})

def test_send_text_payload():
    with patch.object(notify, "_webhook", return_value="ok") as m:
        notify._send_text("KEY", ["111", "222"], "hi")
        m.assert_called_once_with("KEY", {"msgtype": "text",
                                          "text": {"content": "hi", "mentioned_mobile_list": ["111", "222"]}})

def test_send_img_payload():
    d = tempfile.mkdtemp(); p = Path(d) / "t.png"; p.write_bytes(b"\x89PNG fake")
    with patch.object(notify, "_webhook", return_value="ok") as m:
        notify._send_img(str(p), "KEY")
        payload = m.call_args[0][1]
        assert payload["msgtype"] == "image"
        assert payload["image"]["base64"] == base64.b64encode(b"\x89PNG fake").decode()
        assert payload["image"]["md5"] == hashlib.md5(b"\x89PNG fake").hexdigest()

def test_send_img_missing_file():
    assert "失败" in notify._send_img("/nonexistent_screenshot.png", "KEY")

def test_take_screenshot_failure():
    with patch("playwright.sync_api.sync_playwright") as sp:
        sp.side_effect = Exception("no browser")
        assert notify.take_screenshot("http://localhost:5001/") is None
    print("take_screenshot_failure OK")

def _capture_alerts(cfg, now):
    calls = []
    with patch.object(notify, "_send_text", lambda key, mob, msg: calls.append((key, mob, msg)) or "ok"):
        notify.check_alerts(cfg, now=now)
    return calls

def test_check_alerts_hotline():
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周二
    # 排队=10(>=阈值) 空闲=3(<10) -> 告警
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":10,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":5,"通话":1,"空闲":3,"离席":0,"话后":1,"振铃":0,"置忙":0}, d)
    calls = _capture_alerts(cfg, now)
    assert any("热线排队" in c[2] and c[0] == "MAIN" for c in calls), calls
    # 排队=9(<阈值) -> 不告警
    d2 = tempfile.mkdtemp(); cfg2 = _cfg(d2)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":9,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d2)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":5,"通话":1,"空闲":0,"离席":0,"话后":1,"振铃":0,"置忙":0}, d2)
    assert _capture_alerts(cfg2, now) == [], "排队9不应告警"
    # 排队=10 空闲=10(>=排队) -> 不告警
    d3 = tempfile.mkdtemp(); cfg3 = _cfg(d3)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":10,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d3)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":5,"通话":1,"空闲":10,"离席":0,"话后":1,"振铃":0,"置忙":0}, d3)
    assert _capture_alerts(cfg3, now) == [], "空闲>=排队不应告警"
    print("check_alerts_hotline OK")

def test_check_alerts_online():
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("在线", {"时间":"2026-07-28 11:00","转人工量":1,"转人工失败":0,"排队":20,"咨询":0,"在线":5,"小休":0,"示忙":0,"话后":0,"就餐":0,"培训":0,"回访":0}, d)
    assert any("在线排队" in c[2] for c in _capture_alerts(cfg, now))   # =20 告警
    d2 = tempfile.mkdtemp(); cfg2 = _cfg(d2)
    storage.insert("在线", {"时间":"2026-07-28 11:00","转人工量":1,"转人工失败":0,"排队":19,"咨询":0,"在线":5,"小休":0,"示忙":0,"话后":0,"就餐":0,"培训":0,"回访":0}, d2)
    assert _capture_alerts(cfg2, now) == [], "排队19不应告警"
    print("check_alerts_online OK")

def test_check_alerts_12378():
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周二,在 12378 窗口
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("12378", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":1,"累计呼入量":1}, d)
    storage.insert("12378明细", {"时间":"2026-07-28 11:00","签入":3,"通话":1,"空闲":0,"离席":0,"话后":0,"振铃":0,"置忙":0}, d)
    assert any("12378排队" in c[2] and c[0] == "SECOND" for c in _capture_alerts(cfg, now))  # =1 告警
    d2 = tempfile.mkdtemp(); cfg2 = _cfg(d2)
    storage.insert("12378", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":0,"累计呼入量":1}, d2)
    storage.insert("12378明细", {"时间":"2026-07-28 11:00","签入":3,"通话":1,"空闲":0,"离席":0,"话后":0,"振铃":0,"置忙":0}, d2)
    assert _capture_alerts(cfg2, now) == [], "排队0不应告警"
    print("check_alerts_12378 OK")

def test_check_alerts_12378_window():
    now = datetime.datetime(2026, 8, 1, 18, 30, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周六
    assert now.weekday() == 5, now.weekday()
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("12378", {"时间":"2026-08-01 18:00","转人工量":1,"接通量":1,"排队量":5,"累计呼入量":1}, d)
    storage.insert("12378明细", {"时间":"2026-08-01 18:00","签入":3,"通话":1,"空闲":0,"离席":0,"话后":0,"振铃":0,"置忙":0}, d)
    assert all("12378排队" not in c[2] for c in _capture_alerts(cfg, now)), "出窗口不应告警"
    print("check_alerts_12378_window OK")

def _seed_full(d):
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1108,"接通量":1106,"排队量":0,"累计呼入量":1187,"外呼量":0,"外呼接通量":0}, d)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":87,"通话":38,"空闲":42,"离席":0,"话后":5,"振铃":0,"置忙":0}, d)
    storage.insert("在线", {"时间":"2026-07-28 11:00","转人工量":826,"转人工失败":2,"排队":0,"咨询":75,"在线":32,"小休":5,"示忙":1,"话后":0,"就餐":0,"培训":0,"回访":0}, d)
    storage.insert("常规", {"时间":"2026-07-28 11:00","签入":17,"通话":8,"空闲":6,"离席":0,"话后":3,"振铃":0,"置忙":0}, d)
    storage.insert("贷后", {"时间":"2026-07-28 11:00","签入":18,"通话":5,"空闲":9,"离席":0,"话后":4,"振铃":0,"置忙":0}, d)
    storage.insert("12378", {"时间":"2026-07-28 11:00","转人工量":44,"接通量":44,"排队量":0,"累计呼入量":44}, d)
    storage.insert("12378明细", {"时间":"2026-07-28 11:00","签入":6,"通话":1,"空闲":5,"离席":0,"话后":0,"振铃":0,"置忙":0}, d)
    storage.insert("会话记录", {"时间":"2026-07-28 11:00","转接一组":60,"转接二组":40,"贷后转接组":100,"回访组一组":50,"贷后回访组":50}, d)
    storage.insert("工单明细", {"时间":"2026-07-28 11:00","二线客诉处理组":0,"常规工单处理组":0,"回访组一组":150,"贷后回访组":150,"12378回访组":0,"转接一组":10,"转接二组":10,"贷后转接组":20}, d)
    with open(Path(d)/"预估流入量.csv", "w", encoding="utf-8", newline="") as f:
        f.write("时间,线路,时段预估量,累计预估量\n2026-07-28 11:00,热线,100,1187\n2026-07-28 11:00,在线,50,811\n")

def test_send_report():
    d = tempfile.mkdtemp(); cfg = _cfg(d); _seed_full(d)
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    md_calls = []; img_calls = []
    with patch.object(notify, "_send_md", lambda text, key: md_calls.append((text, key)) or "ok"), \
         patch.object(notify, "_send_img", lambda path, key: img_calls.append((path, key)) or "ok"), \
         patch.object(notify, "take_screenshot", return_value="data/s.png"):
        notify.send_report(cfg, now=now)
    keys = [c[1] for c in md_calls]
    assert keys == ["MAIN", "SECOND"], md_calls
    assert [c[1] for c in img_calls] == ["MAIN", "SECOND"], img_calls
    assert "统计监控`热线`" in md_calls[0][0]
    assert ">预测量: 1187, 转人工量：1108" in md_calls[0][0]   # forecast 命中
    assert "统计监控`12378`" in md_calls[1][0]
    print("send_report OK")

def test_build_secondline_msg_empty():
    d = tempfile.mkdtemp()   # 无任何 db
    assert notify.build_secondline_msg(d, "2026-07-28 11:15", "2026-07-28") == ""
    print("build_secondline_msg_empty OK")

def test_config_notify_block():
    # 用 load_cfg() 而非直接 yaml.safe_load,以验证 .env 密钥注入链路
    from collector._utils import load_cfg
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_cfg(os.path.join(root, "config.yaml"))
    n = cfg["notify"]
    # webhook key 由 .env 注入;本地有 .env 时非空,CI 无 .env 时可为空(只验结构)
    assert "main_key" in n["webhook"]
    assert "secondary_key" in n["webhook"]
    assert n["alert"]["hotline_queue"] == 10
    assert n["alert"]["online_queue"] == 20
    assert n["alert"]["queue_12378"] == 1
    assert n["screenshot_url"].startswith("http://")
    print("config_notify_block OK")

def test_push_job_window_gate():
    from collector import scheduler
    # 出窗口(空窗口 (9,9] 永不成立)
    cfg_out = _cfg(tempfile.mkdtemp())
    cfg_out["schedule"]["window_start"] = "09:00"; cfg_out["schedule"]["window_end"] = "09:00"
    called = []
    with patch.object(notify, "send_report", lambda c: called.append(c)):
        scheduler.push_job(cfg_out)
    assert called == [], "出窗口不应调 send_report"
    # 在窗口(全天)
    cfg_in = _cfg(tempfile.mkdtemp())
    cfg_in["schedule"]["window_start"] = "00:00"; cfg_in["schedule"]["window_end"] = "23:59"
    called2 = []
    with patch.object(notify, "send_report", lambda c: called2.append(c)), \
         patch.object(scheduler, "_wait_cycle", lambda cfg, now_str: None):
        scheduler.push_job(cfg_in)
    assert len(called2) == 1, "在窗口应调 send_report"
    print("push_job_window_gate OK")

def test_wait_cycle():
    from collector import scheduler
    cfg = _cfg(tempfile.mkdtemp())
    cfg["notify"]["push_wait_timeout"] = 1
    # mark 就绪 -> 立即返回(不阻塞)
    scheduler._last_ws_cycle = "2026-07-28 11:00"
    scheduler._last_detail_cycle = "2026-07-28 11:00"
    scheduler._wait_cycle(cfg, "2026-07-28 11:00")
    # mark 未就绪 -> 超时返回(不抛异常,退化为上一周期)
    scheduler._last_ws_cycle = None
    scheduler._wait_cycle(cfg, "2026-07-28 11:00")
    print("wait_cycle OK")

def main():
    test_latest_snapshot()
    test_forecast_at()
    test_render_firstline()
    test_render_secondline()
    test_webhook_success()
    test_webhook_errcode()
    test_webhook_exception()
    test_send_md_payload()
    test_send_text_payload()
    test_send_img_payload()
    test_send_img_missing_file()
    test_take_screenshot_failure()
    test_check_alerts_hotline()
    test_check_alerts_online()
    test_check_alerts_12378()
    test_check_alerts_12378_window()
    test_send_report()
    test_build_secondline_msg_empty()
    test_config_notify_block()
    test_push_job_window_gate()
    test_wait_cycle()
    print("ALL notify tests OK")

if __name__ == "__main__": main()
