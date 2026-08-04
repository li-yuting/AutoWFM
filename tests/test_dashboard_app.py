# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_routes():
    from dashboard.app import app
    client = app.test_client()
    r = client.get("/")
    assert r.status_code in (301, 302), r.status_code
    r = client.get("/?view=day&date=2026-07-27")
    assert r.status_code == 200, r.status_code
    assert b"chart_in_12378" in r.data, "缺少 12378 图容器"
    assert b"chart_out_" in r.data
    r = client.get("/?view=month&date=2026-07")
    assert r.status_code == 200, r.status_code

def test_rate_class():
    from dashboard.app import rate_class
    # 流入率: <90 红, >105 绿；边界(90/105)不高亮
    assert rate_class("89.47%", "热线_流入率") == "text-danger"
    assert rate_class("90.00%", "热线_流入率") == ""
    assert rate_class("100.00%", "热线_流入率") == ""
    assert rate_class("105.00%", "热线_流入率") == ""
    assert rate_class("105.01%", "热线_流入率") == "text-success"
    # 接通率: <92 红, >95 绿；边界(92/95)不高亮
    assert rate_class("91.99%", "在线_接通率") == "text-danger"
    assert rate_class("92.00%", "12378_接通率") == ""
    assert rate_class("95.00%", "热线_接通率") == ""
    assert rate_class("95.01%", "在线_接通率") == "text-success"
    # 卡片键(无组前缀)同样生效
    assert rate_class("80.00%", "流入率") == "text-danger"
    assert rate_class("96.00%", "接通率") == "text-success"
    # 非率值: 数值/None/键非流入·接通率 -> 不高亮
    assert rate_class(100, "热线_转人工量") == ""
    assert rate_class(None, "流入率") == ""
    assert rate_class("50.00%", "12378_工单量") == ""

def main():
    test_routes()
    test_rate_class()
    print("dashboard_app OK")

if __name__ == "__main__":
    main()
