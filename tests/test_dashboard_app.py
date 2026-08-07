# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _auth_headers():
    """若看板启用了 Bearer Token 认证(AUTOWFM_DASH_TOKEN 已设),返回带 token 的 headers。"""
    token = os.environ.get("AUTOWFM_DASH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}

def test_routes():
    from dashboard.app import app
    client = app.test_client()
    h = _auth_headers()
    r = client.get("/", headers=h)
    assert r.status_code in (301, 302), r.status_code
    r = client.get("/?view=day&date=2026-07-27", headers=h)
    assert r.status_code == 200, r.status_code
    assert b"chart_in_12378" in r.data, "缺少 12378 图容器"
    assert b"chart_out_" in r.data
    r = client.get("/?view=month&date=2026-07", headers=h)
    assert r.status_code == 200, r.status_code

def test_health():
    """/health 始终可访问(免认证),供健康探活。"""
    from dashboard.app import app
    client = app.test_client()
    r = client.get("/health")
    assert r.status_code == 200, r.status_code
    assert b"ok" in r.data, r.data

def test_auth_enforced():
    """启用 token 时:无 token -> 401;带正确 token -> 200。未启用时:均 200/302。"""
    from dashboard import app as appmod
    from dashboard.app import app
    client = app.test_client()
    token = appmod._DASH_TOKEN
    if token:
        # 启用认证:无 token 访问 / 应被拒(302 跳转也算需要认证,但 before_request 先拦截 -> 401)
        r_no = client.get("/?view=day&date=2026-07-27")
        assert r_no.status_code == 401, f"无 token 应返回 401, 实际 {r_no.status_code}"
        r_yes = client.get("/?view=day&date=2026-07-27", headers={"Authorization": f"Bearer {token}"})
        assert r_yes.status_code == 200, f"带 token 应返回 200, 实际 {r_yes.status_code}"
    else:
        # 未启用认证:均放行
        r = client.get("/?view=day&date=2026-07-27")
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
    test_health()
    test_auth_enforced()
    test_rate_class()
    print("dashboard_app OK")

if __name__ == "__main__":
    main()
