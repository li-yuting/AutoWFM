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
    im = ws._extract_im(IM)
    assert im == {"转人工量":3307,"转人工失败":1,"排队":0,"咨询":0,
                  "在线":2,"小休":1,"示忙":1,"话后":1,"就餐":1,"培训":1,"回访":0}, im
    print("ws OK")

if __name__ == "__main__": main()
