# -*- coding: utf-8 -*-
"""深抓在线帧 ~25s,统计 seatRestReason 取值(子状态在此)。"""
import json, websocket, time
from collections import Counter, defaultdict

URL = "ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor"
CMD = {"cmd": 1, "screen": "IM_MONITOR", "data": {"skillCode": "", "agentStatus": ""}}

ws = websocket.create_connection(URL, timeout=12)
ws.send(json.dumps(CMD, ensure_ascii=False))
ws.settimeout(3)
reason_status = defaultdict(Counter)
reason_dept = {}
frames = 0
deadline = time.time() + 25
while time.time() < deadline:
    try:
        raw = ws.recv()
    except Exception:
        continue
    try:
        o = json.loads(raw)
    except Exception:
        continue
    if o.get("screen") != "IM_MONITOR" or not o.get("data"):
        continue
    frames += 1
    for s in o["data"].get("seats", []):
        st = s.get("seatStatus")
        r = s.get("seatRestReason") or ""
        reason_status[r][st] += 1
        if r and r not in reason_dept:
            reason_dept[r] = s.get("agentInfo", {}).get("deptName")
ws.close()

print("frames:", frames)
print("\nseatRestReason -> seatStatus counts (sample dept):")
for r, cc in sorted(reason_status.items(), key=lambda x: -sum(x[1].values())):
    print(f"  {r!r}: {dict(cc)}  dept={reason_dept.get(r)!r}")
