# -*- coding: utf-8 -*-
"""用真实 collect_one 路径抓在线帧,跑 _extract_im,对比原始 seatStatus 计数。"""
import sys, os
sys.path.insert(0, r"D:\PythonProject\AutoWFM")
import json, websocket
from collections import Counter
import ws

URL = "ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor"
CMD = {"cmd": 1, "screen": "IM_MONITOR", "data": {"skillCode": "", "agentStatus": ""}}
cfg = {"ws": {"connect_timeout": 12, "recv_timeout": 8, "retry": 1},
       "endpoints": {"online": URL},
       "subs": [{"name": "在线", "endpoint": "online", "screen": "IM_MONITOR",
                 "data": {"skillCode": "", "agentStatus": ""}}]}

val = ws.collect_one(cfg["subs"][0], cfg)
print("collect_one ->", val)

w = cfg["ws"]
ws2 = websocket.create_connection(URL, timeout=w["connect_timeout"])
ws2.send(json.dumps(CMD, ensure_ascii=False)); ws2.settimeout(w["recv_timeout"])
for _ in range(10):
    try:
        raw = ws2.recv()
    except Exception:
        break
    try:
        o = json.loads(raw)
    except Exception:
        continue
    if o.get("screen") == "IM_MONITOR" and o.get("data"):
        seats = o["data"].get("seats", [])
        print("raw seatStatus:", dict(Counter(s.get("seatStatus") for s in seats)))
        print("rest reasons:", dict(Counter(s.get("seatRestReason") for s in seats if s.get("seatStatus") == "rest")))
        break
ws2.close()
