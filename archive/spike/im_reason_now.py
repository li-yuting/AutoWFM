# -*- coding: utf-8 -*-
"""抓在线 IM_MONITOR 帧:看 collect_one 提取结果 + 原始 seatRestReason 分布(补全 REASON_MAP 用)。"""
import sys, os
sys.path.insert(0, r"D:\PythonProject\AutoWFM")
import json, websocket
from collections import Counter, defaultdict
from collector import main as M
from collector import ws as W

cfg = M.load_cfg()
sub = next(s for s in cfg["subs"] if s["name"] == "在线")
print("在线 sub data:", sub["data"])

# 1) 真实 collect_one 提取结果
val = W.collect_one(sub, cfg)
print("\ncollect_one ->", val)

# 2) 原始帧: seatRestReason -> seatStatus 分布 + 样本
url = cfg["endpoints"][sub["endpoint"]]
cmd = {"cmd": 1, "screen": sub["screen"], "data": sub["data"]}
w = cfg["ws"]
reason_status = defaultdict(Counter)
reason_sample = {}
reason_dept = defaultdict(set)
ws = websocket.create_connection(url, timeout=w["connect_timeout"])
ws.send(json.dumps(cmd, ensure_ascii=False))
ws.settimeout(w["recv_timeout"])
got = False
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
        got = True
        seats = o["data"].get("seats", [])
        print("\nraw seats total:", len(seats))
        print("seatStatus counts:", dict(Counter(s.get("seatStatus") for s in seats)))
        for s in seats:
            st = s.get("seatStatus")
            r = s.get("seatRestReason") or ""
            reason_status[r][st] += 1
            if r and r not in reason_sample:
                reason_sample[r] = {
                    "seatStatus": st,
                    "dept": s.get("agentInfo", {}).get("deptName"),
                    "agentName": s.get("agentInfo", {}).get("agentName"),
                    "restReasonName": s.get("seatRestReasonName"),
                }
            if r:
                d = s.get("agentInfo", {}).get("deptName")
                if d:
                    reason_dept[r].add(d)
        break
ws.close()

if not got:
    print("no IM frame")
else:
    print("\nseatRestReason -> seatStatus counts:")
    for r, cc in sorted(reason_status.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {r!r}: {dict(cc)}  sample={reason_sample.get(r)}  depts={sorted(reason_dept.get(r, []))}")
