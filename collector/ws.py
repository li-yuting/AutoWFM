# -*- coding: utf-8 -*-
"""WS 每周期采集:连->发cmd->收首个匹配帧->提取指标->关。提取器复用 v14。"""
import json
import logging
import time
import websocket
from websocket import WebSocketTimeoutException

log = logging.getLogger("autowfm")
# seatStatus 仅 free/rest/notReady/offline;rest 的子状态在 seatRestReason
REASON_MAP = {"meal": "就餐", "training": "培训", "arrange": "话后"}
# ponytail: 回访 等 reason 待业务时段日志补全

def _extract_statics(obj, keep_hc=True):
    try:
        d = obj["data"]; m = d["manualAnalysisData"]
        out = {"转人工量": m["agentCount"], "接通量": m["agentSuccessCount"],
               "排队量": m["agentQueueCount"], "累计呼入量": d["allHrCount"]}
        if keep_hc:  # 12378 不存外呼量/外呼接通量(与热线重复,hcAnalysisData 为全局值)
            hc = d["hcAnalysisData"]
            out["外呼量"] = hc["allHcCount"]
            out["外呼接通量"] = hc["hcSuccessCount"]
        return out
    except Exception:
        return None

def _extract_seat(skill):
    def f(obj):
        try:
            d = obj["data"]; st = d.get("agentStatusStatics")
            if not st:
                return None
            aots = d.get("afterOverTimeStatics") or []
            if aots and aots[0].get("agentSplit") != skill:
                return None  # :7000 夹带跨 skill 推送,跳过
            return {"签入": st["loginCount"], "通话": st["callingCount"],
                    "空闲": st["idleCount"], "离席": st["leaveCount"],
                    "话后": st["afterCount"], "振铃": st["ringCount"],
                    "置忙": st["busyCount"]}
        except Exception:
            return None
    return f

def _extract_im(name=""):
    """IM_MONITOR 提取器。name 透入日志前缀(在线/常规/贷后)，区分来源。"""
    def f(obj):
        try:
            d = obj["data"]; ov = d.get("overview"); seats = d.get("seats")
            if not ov or not seats:
                return None
            out = {"在线":0,"小休":0,"示忙":0,"话后":0,"就餐":0,"培训":0,"回访":0}
            for s in seats:
                st = s.get("seatStatus")
                if not st or st == "offline":
                    continue
                if st == "free":
                    out["在线"] += 1
                elif st == "notReady":
                    out["示忙"] += 1
                elif st == "rest":
                    zh = REASON_MAP.get(s.get("seatRestReason") or "")
                    if zh:
                        out[zh] += 1
                    else:
                        out["小休"] += 1
                        if s.get("seatRestReason"):
                            log.warning(f"[{name or '在线'}] 未映射 seatRestReason: {s.get('seatRestReason')!r}")
            return {"转人工量": ov["todaySessionTotalCnt"], "转人工失败": ov["todayQueueFailCnt"],
                    "排队": ov["queueingCnt"], "咨询": ov["consultingCnt"], **out}
        except Exception:
            return None
    return f

def _make_extractor(screen, skill, name=""):
    if screen == "STATICS":    return lambda obj: _extract_statics(obj, name != "12378")
    if screen == "SEAT":       return _extract_seat(skill)
    if screen == "IM_MONITOR": return _extract_im(name)
    return lambda obj: None

def _collect_screen(sub, cfg):
    """连->发cmd->收首个匹配帧->提取->关。单屏单帧采集，主 sub 与 im 附加 sub 复用。

    返回提取器产出的 dict 或 None（失败/无数据/无匹配帧）。"""
    url = cfg["endpoints"][sub["endpoint"]]
    cmd = {"cmd": 1, "screen": sub["screen"], "data": sub["data"]}
    screen = sub["screen"]; skill = sub.get("skill")
    extract = _make_extractor(screen, skill, sub.get("name", ""))
    w = cfg["ws"]
    backoff = w.get("retry_backoff_base", 2)
    for attempt in range(w["retry"] + 1):
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=w["connect_timeout"])
            ws.send(json.dumps(cmd, ensure_ascii=False))
            ws.settimeout(w["recv_timeout"])
            for _ in range(10):  # 最多收 10 帧找匹配
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    break
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if obj.get("screen") == screen and obj.get("data"):
                    val = extract(obj)
                    if val is not None:
                        return val
            return None
        except Exception:
            if attempt < w["retry"]:
                if backoff > 0:
                    time.sleep(backoff ** attempt)  # 指数退避: 1,2,4,8...
                continue
            return None
        finally:
            if ws:
                try: ws.close()
                except Exception: pass
    return None

def _merge_im(val, im_val):
    """把 IM 附加源(online 数)加到主 SEAT 的「签入」上。im_val 为 None 时原值不变。

    纯函数，便于单测。IM 的「在线」数 = seatStatus==free 的座席计数(_extract_im)。
    """
    if val is None or im_val is None:
        return val
    online = im_val.get("在线")
    if online is None or "签入" not in val:
        return val
    merged = dict(val)
    merged["签入"] = (merged.get("签入") or 0) + online
    return merged

def collect_one(sub, cfg):
    """采集一个 sub：主屏提取指标；若配了 im 附加源，串行采 IM 并把「在线」加到「签入」。

    IM 采集失败仅 warning，不阻断主数据(降级为不加在线数)。
    """
    val = _collect_screen(sub, cfg)
    im_sub = sub.get("im")
    if im_sub and val is not None and "签入" in val:
        try:
            im_val = _collect_screen(im_sub, cfg)
            if im_val is None:
                log.warning(f"[WS] {sub.get('name','')} IM 附加源无数据，签入未加在线数")
            else:
                val = _merge_im(val, im_val)
        except Exception:
            log.warning(f"[WS] {sub.get('name','')} IM 附加源采集异常，签入未加在线数", exc_info=True)
    return val
