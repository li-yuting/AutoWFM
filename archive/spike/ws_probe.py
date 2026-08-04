# -*- coding: utf-8 -*-
"""一次性 WS 探测:验证 7 路订阅能否连通、每路返回什么数据。
用于解决 spec 的开放项:认证 + 响应 schema。非生产代码。
"""
import json
import socket
import websocket  # websocket-client

ONLINE_URL = "ws://monitor-datawarehouse-cloud.weicai.com.cn:7100/im/monitor"
OTHER_URL  = "ws://monitor-datawarehouse-cloud.weicai.com.cn:7000/customer/monitor"

SEAT_DATA = {"skillCode": "", "pickUpRankDeptId": "", "busyRankDeptId": "",
             "afterOverTimeDeptId": "", "afterOverTimeStatiscsDeptId": "", "agentStatusDeptId": ""}

SUBS = [
    ("热线",     OTHER_URL,  {"cmd": 1, "screen": "STATICS", "data": {"skillCode": "", "numberType": "HFQ_OFFICIAL"}}),
    ("12378",    OTHER_URL,  {"cmd": 1, "screen": "STATICS", "data": {"skillCode": "", "numberType": "SERVICE_12378"}}),
    ("热线明细",  OTHER_URL,  {"cmd": 1, "screen": "SEAT", "data": {**SEAT_DATA, "skillCode": "252"}}),
    ("常规",     OTHER_URL,  {"cmd": 1, "screen": "SEAT", "data": {**SEAT_DATA, "skillCode": "520"}}),
    ("贷后",     OTHER_URL,  {"cmd": 1, "screen": "SEAT", "data": {**SEAT_DATA, "skillCode": "958"}}),
    ("12378明细", OTHER_URL, {"cmd": 1, "screen": "SEAT", "data": {**SEAT_DATA, "skillCode": "847", "agentStatusDeptId": "q40YvMUfzHi1y3aOr89s8lx3mTk55OluQVhDCmYH"}}),
    ("在线",     ONLINE_URL, {"cmd": 1, "screen": "IM_MONITOR", "data": {"skillCode": "", "agentStatus": ""}}),
]

CONNECT_TIMEOUT = 12   # 建连超时
RECV_WINDOW     = 8    # 收帧窗口(秒)
MAX_FRAMES      = 6    # 最多抓几帧
PRINT_LIMIT     = 2500 # 单帧打印字符上限


def summarize(obj, depth=0, maxdepth=2):
    """递归给出结构摘要:键 -> 类型/长度。"""
    pad = "  " * (depth + 1)
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                lines.append(f"{pad}{k}: dict({len(v)} keys)")
                if depth < maxdepth:
                    lines.extend(summarize(v, depth + 1, maxdepth))
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: list({len(v)})")
                if v and depth < maxdepth:
                    lines.append(f"{pad}  [0]:")
                    lines.extend(summarize(v[0], depth + 2, maxdepth))
            else:
                val = repr(v)
                if len(val) > 60:
                    val = val[:60] + "..."
                lines.append(f"{pad}{k}: {type(v).__name__} = {val}")
    else:
        lines.append(f"{pad}{type(obj).__name__}: {repr(obj)[:80]}")
    return lines


def probe(name, url, msg):
    print(f"\n{'='*70}")
    print(f"[{name}] -> {url}")
    print(f"  send: {json.dumps(msg, ensure_ascii=False)}")
    ws = None
    try:
        ws = websocket.create_connection(url, timeout=CONNECT_TIMEOUT)
        ws.send(json.dumps(msg))
        ws.settimeout(RECV_WINDOW)
        frames = []
        try:
            for _ in range(MAX_FRAMES):
                frames.append(ws.recv())
        except websocket.WebSocketTimeoutException:
            pass
        print(f"  -> connected OK, received {len(frames)} frame(s)")
        for i, f in enumerate(frames):
            raw = f if isinstance(f, str) else f.decode("utf-8", "replace")
            try:
                obj = json.loads(raw)
                pretty = json.dumps(obj, ensure_ascii=False, indent=2)
                if len(pretty) > PRINT_LIMIT:
                    pretty = pretty[:PRINT_LIMIT] + "\n  ... [truncated]"
                print(f"  --- frame {i} (JSON, {len(raw)} bytes) ---")
                print("  " + pretty.replace("\n", "\n  "))
                print(f"  --- frame {i} 结构摘要 ---")
                print("\n".join(summarize(obj)))
            except Exception:
                print(f"  --- frame {i} (非JSON, {len(raw)} bytes) ---")
                print("  " + raw[:PRINT_LIMIT].replace("\n", "\n  "))
        if not frames:
            print("  -> 无数据返回(可能需认证,或服务器静默)")
        return True
    except (websocket.WebSocketException, socket.error, OSError) as e:
        print(f"  -> 连接失败: {type(e).__name__}: {e}")
        return False
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    results = []
    for name, url, msg in SUBS:
        ok = probe(name, url, msg)
        results.append((name, ok))
    print(f"\n{'='*70}\n汇总:")
    for name, ok in results:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
