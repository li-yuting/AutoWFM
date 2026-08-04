"""APScheduler:WS 任务 + requests 任务,窗口 guard,各自线程池。"""
import datetime
import time
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import logging
from collector import ws as ws_mod
from collector import detail as detail_mod
from collector import storage
from collector import notify
from collector._utils import parse_hhmm, in_window

log = logging.getLogger("autowfm")

# 当周期 ws/detail 完成标记(记录已完成的周期 now_str),供 push_job 等待,
# 避免推送与采集同秒触发时读到上一周期快照
_last_ws_cycle = None
_last_detail_cycle = None

# 各源连续失败周期计数(缺口检测):成功即清零,连续失败达阈值告警一次
_ws_gap_counters = {}
_GAP_ALERTED = set()  # 已对本轮缺口告警过的源,恢复后移除

def _gap_threshold(cfg):
    return int(cfg.get("ws", {}).get("gap_alert_threshold", 5))

def _track_gap(name, ok, cfg):
    """更新某源连续失败计数,并在达到阈值时告警一次(日志+可选企微,恢复后提示)。"""
    if ok:
        if _ws_gap_counters.get(name, 0) > 0:
            log.info(f"[WS] {name} 已恢复(此前连续失败 {_ws_gap_counters[name]} 周期)")
        _ws_gap_counters[name] = 0
        _GAP_ALERTED.discard(name)
        return
    _ws_gap_counters[name] = _ws_gap_counters.get(name, 0) + 1
    if _ws_gap_counters[name] >= _gap_threshold(cfg) and name not in _GAP_ALERTED:
        _GAP_ALERTED.add(name)
        msg = f"⚠️ 采集缺口告警\n{name} 连续 {_ws_gap_counters[name]} 周期无数据/异常，疑似采集中断，请检查"
        log.warning(f"[WS] {msg}")
        _send_gap_alert(cfg, msg)

def _send_gap_alert(cfg, msg):
    """缺口告警走企微 main_key(失败不抛异常,不影响调度)。需要 notify.alert.gap_recipient 配置。"""
    try:
        wh = cfg.get("notify", {}).get("webhook", {})
        rcpt = cfg.get("notify", {}).get("alert", {}).get("gap_recipient", [])
        key = wh.get("main_key")
        if key:
            notify._send_text(key, rcpt, msg)
    except Exception:
        log.exception("[gap] 企微缺口告警发送失败")

def _now(cfg):
    return datetime.datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))

def ws_job(cfg, pool):
    global _last_ws_cycle
    now = _now(cfg)
    subs_in = [s for s in cfg["subs"] if in_window(cfg, s, now)]
    if not subs_in:
        return
    now_str = now.strftime("%Y-%m-%d %H:%M")
    futs = {pool.submit(ws_mod.collect_one, s, cfg): s for s in subs_in}
    ok, failed = [], []
    for f in as_completed(futs):
        s = futs[f]
        try:
            val = f.result()
            if val is None:
                failed.append(s)
                continue
            storage.insert(s["name"], {"时间": now_str, **val}, cfg["storage"]["dir"])
            ok.append(s["name"])
        except Exception:
            failed.append(s)
            log.warning(f"[WS] {s['name']} 采集异常(首轮)", exc_info=True)
    # 缺口重试补采:对失败源延迟 backfill_retry_delay 秒再采一次,仍失败才记 fail
    if failed:
        delay = cfg.get("ws", {}).get("backfill_retry_delay", 5)
        log.info(f"[WS] 周期 {now_str} {len(failed)} 源首轮失败,延迟 {delay}s 补采: {','.join(s['name'] for s in failed)}")
        time.sleep(delay)
        for s in failed:
            try:
                val = ws_mod.collect_one(s, cfg)
                if val is None:
                    raise ValueError("无数据")
                storage.insert(s["name"], {"时间": now_str, **val}, cfg["storage"]["dir"])
                ok.append(s["name"])
                _track_gap(s["name"], True, cfg)
                log.info(f"[WS] {s['name']} 补采成功")
            except Exception:
                _track_gap(s["name"], False, cfg)
                log.warning(f"[WS] {s['name']} 补采仍失败")
    fail_names = [f"{s['name']}(补采失败)" for s in failed if s['name'] not in ok]
    log.info(f"[WS] 周期 {now_str} 成功={','.join(ok) or '无'} 失败={','.join(fail_names) or '无'} (in-window={len(subs_in)})")
    try:
        notify.check_alerts(cfg)
    except Exception:
        log.exception("[alert] check_alerts 异常")
    _last_ws_cycle = now_str

def detail_job(cfg, pool):
    global _last_detail_cycle
    if not in_window(cfg):
        return
    now = _now(cfg)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    today = now.strftime("%Y-%m-%d")
    futs = {pool.submit(detail_mod.download_and_count, n, m, cfg["secrets"], today, cfg["detail"]["timeout"]): n
            for n, m in cfg["detail_modes"].items()}
    ok, fail_names = [], []
    for f in as_completed(futs):
        n = futs[f]
        try:
            counts = f.result()
            storage.insert(n, {"时间": now_str, **counts}, cfg["storage"]["dir"])
            ok.append(n)
        except detail_mod.EmptyDownloadError as e:
            fail_names.append(f"{n}(空表)")
            log.warning(f"[detail] {n} 空表,跳过写入(防覆盖): {e}")
        except Exception:
            fail_names.append(n)
            log.warning(f"[detail] {n} 采集异常", exc_info=True)
    log.info(f"[detail] 周期 {now_str} 成功={','.join(ok) or '无'} 失败={','.join(fail_names) or '无'}")
    _last_detail_cycle = now_str

def push_job(cfg):
    if not in_window(cfg):
        return
    now = _now(cfg)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    _wait_cycle(cfg, now_str)
    notify.send_report(cfg)

def _wait_cycle(cfg, now_str):
    """等待当周期 ws_job + detail_job 完成(数据落库)后再推;超时则放弃等待(退化为上一周期数据)。"""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    timeout = cfg["notify"].get("push_wait_timeout", 30)
    deadline = datetime.datetime.now(tz) + datetime.timedelta(seconds=timeout)
    while datetime.datetime.now(tz) < deadline:
        if _last_ws_cycle == now_str and _last_detail_cycle == now_str:
            return
        time.sleep(0.5)
    log.warning(f"[push] 等待 ws/detail 完成超时({timeout}s): "
                f"ws={_last_ws_cycle} detail={_last_detail_cycle} expect={now_str}")

def start(cfg):
    tz = cfg["schedule"]["timezone"]
    ws_pool = ThreadPoolExecutor(max_workers=7, thread_name_prefix="ws")
    det_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="detail")
    start_date = datetime.datetime.now(ZoneInfo(tz)).replace(hour=8, minute=35, second=0, microsecond=0)
    trig = IntervalTrigger(minutes=cfg["schedule"]["interval_minutes"], start_date=start_date, timezone=tz)
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(ws_job, trig, args=[cfg, ws_pool], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="ws")
    sched.add_job(detail_job, trig, args=[cfg, det_pool], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="detail")
    push_trig = CronTrigger(minute=",".join(str(m) for m in cfg["notify"]["push_minutes"]), timezone=tz)
    sched.add_job(push_job, push_trig, args=[cfg], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="push")
    log.info("调度器启动")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
        ws_pool.shutdown(wait=False)
        det_pool.shutdown(wait=False)
