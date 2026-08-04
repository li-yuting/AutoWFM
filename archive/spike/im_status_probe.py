# -*- coding: utf-8 -*-
"""探测在线 IM_MONITOR 帧的 seatStatus 值分布,补全 STATUS_MAP。"""
import json, websocket
from collections import Counter

URL = "ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor"
CMD = {"cmd": 1, "screen": "IM_MONITOR", "data": {"skillCode": "", "agentStatus": ""}}

ws = websocket.create_connection(URL, timeout=12)
ws.send(json.dumps(CMD, ensure_ascii=False))
ws.settimeout(8)
obj = None
for _ in range(10):
    try:
        raw = ws.recv()
    except Exception:
        break
    try:
        o = json.loads(raw)
    except Exception:
        continue
    if o.get("screen") == "IM_MONITOR" and o.get("data"):
        obj = o
        break
ws.close()

if not obj:
    print("no IM frame")
else:
    seats = obj["data"].get("seats", [])
    print("seats total:", len(seats))
    c = Counter(s.get("seatStatus") for s in seats)
    print("seatStatus counts:")
    for k, v in c.most_common():
        print(f"  {k!r}: {v}")
    # 每个 seatStatus 各取一个样本,看是否有别的状态字段
    seen = {}
    for s in seats:
        st = s.get("seatStatus")
        if st and st not in seen:
            seen[st] = s
    print("\nsample per status (seatStatus + seatRestReason + agentInfo.deptName):")
    for st, s in seen.items():
        print(f"  {st!r}: restReason={s.get('seatRestReason')!r} dept={s.get('agentInfo',{}).get('deptName')!r}")
