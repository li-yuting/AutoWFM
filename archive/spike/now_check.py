# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"D:\PythonProject\AutoWFM")
import yaml, scheduler
c = yaml.safe_load(open("config.yaml", encoding="utf-8"))
now = scheduler._now(c)
print("now:", now.strftime("%Y-%m-%d %H:%M %A"), "weekday=", now.weekday())
for s in c["subs"]:
    print(f"  {s['name']}: in_window={scheduler._in_window(c, s, now)}")
print("detail(global):", scheduler._in_window(c, None, now))
