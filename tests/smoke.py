# -*- coding: utf-8 -*-
"""一次性跑 9 路(不走调度),验证采集+入库。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collector import main as M
from collector import ws as W
from collector import detail as D
from collector import storage

def main():
    cfg = M.load_cfg(); M.setup_logging(cfg)
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M"); today = now.strftime("%Y-%m-%d")
    print("== WS ==")
    with ThreadPoolExecutor(max_workers=7) as p:
        futs = {p.submit(W.collect_one, s, cfg): s for s in cfg["subs"]}
        for f in as_completed(futs):
            s = futs[f]; val = f.result()
            if val:
                storage.insert(s["name"], {"时间": now_str, **val}, cfg["storage"]["dir"])
                print(f"  OK   {s['name']}: {val}")
            else:
                print(f"  FAIL {s['name']}")
    print("== detail ==")
    with ThreadPoolExecutor(max_workers=2) as p:
        futs = {p.submit(D.download_and_count, n, m, cfg["secrets"], today, cfg["detail"]["timeout"]): n
                for n, m in cfg["detail_modes"].items()}
        for f in as_completed(futs):
            n = futs[f]
            try:
                c = f.result()
                storage.insert(n, {"时间": now_str, **c}, cfg["storage"]["dir"])
                print(f"  OK   {n}: {c}")
            except Exception as e:
                print(f"  FAIL {n}: {e}")
    print("done")

if __name__ == "__main__": main()
