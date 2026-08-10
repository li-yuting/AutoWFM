# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import ws

STATIC = {"cmd":2,"screen":"STATICS","data":{
    "allHrCount":9380,
    "hcAnalysisData":{"allHcCount":8250,"hcSuccessCount":3056,"hourHcCount":11},
    "manualAnalysisData":{"agentCount":4617,"agentSuccessCount":4605,"agentQueueCount":0,
                          "agentFailCount":12,"hourHrCount":82,"pickupRate":"0.9974"}}}
SEAT = {"cmd":2,"screen":"SEAT","data":{
    "agentStatusStatics":{"loginCount":2,"callingCount":1,"idleCount":1,"leaveCount":0,
                          "afterCount":0,"ringCount":0,"busyCount":0},
    "afterOverTimeStatics":[{"agentSplit":"252","agentName":"x"}]}}
IM = {"cmd":2,"screen":"IM_MONITOR","data":{
    "overview":{"todaySessionTotalCnt":3307,"todayQueueFailCnt":1,"queueingCnt":0,"consultingCnt":0},
    "seats":[{"seatStatus":"free"},{"seatStatus":"rest","seatRestReason":"meal"},
             {"seatStatus":"rest","seatRestReason":"training"},
             {"seatStatus":"rest","seatRestReason":"arrange"},
             {"seatStatus":"rest","seatRestReason":"restroom"},
             {"seatStatus":"rest","seatRestReason":"rest"},
             {"seatStatus":"rest"},{"seatStatus":"offline"},
             {"seatStatus":"notReady"},{"seatStatus":"free"}]}}

def main():
    s = ws._extract_statics(STATIC)
    assert s == {"转人工量":4617,"接通量":4605,"排队量":0,"累计呼入量":9380,
                 "外呼量":8250,"外呼接通量":3056}, s
    assert ws._extract_statics(STATIC, keep_hc=False) == {"转人工量":4617,"接通量":4605,"排队量":0,"累计呼入量":9380}, "12378 不应含外呼量/外呼接通量"
    seat = ws._extract_seat("252")(SEAT)
    assert seat == {"签入":2,"通话":1,"空闲":1,"离席":0,"话后":0,"振铃":0,"置忙":0}, seat
    assert ws._extract_seat("520")(SEAT) is None  # 跨 skill 过滤
    im = ws._extract_im("在线")(IM)
    assert im == {"转人工量":3307,"转人工失败":1,"排队":0,"咨询":0,
                  "在线":2,"小休":3,"示忙":1,"话后":1,"就餐":1,"培训":1,"回访":0}, im
    # 工厂化: 不同 name(在线/常规/贷后) 提取值一致, 仅日志前缀随 name 变
    assert ws._extract_im("常规")(IM) == im
    # _merge_im: IM「在线」数加到主 SEAT 的「签入」上；im_val/val 为 None 时不变
    seat520 = ws._extract_seat("252")(SEAT)
    merged = ws._merge_im(seat520, im)
    assert merged["签入"] == 4, merged  # 2(SEAT loginCount) + 2(IM free 在线)
    assert merged["通话"] == 1 and merged["空闲"] == 1  # 其他列不受影响
    assert ws._merge_im(seat520, None)["签入"] == 2  # IM 无数据 -> 原值不变
    assert ws._merge_im(None, im) is None  # 主源无数据 -> None
    assert ws._merge_im({"通话":1}, im) == {"通话":1}  # 主 dict 无「签入」键 -> 原样返回
    print("ws OK")

if __name__ == "__main__": main()
