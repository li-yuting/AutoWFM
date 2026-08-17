# -*- coding: utf-8 -*-
"""API 服务层测试:验证 FastAPI 端点 + Bearer Token 认证。"""
import sys, os, sqlite3, shutil, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_peakflow_main.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")

def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d


def _seed(d, source, cols, rows):
    con = sqlite3.connect(Path(d) / f"{source}.db")
    quoted = ",".join(f'"{c}"' for c in cols)
    ph = ",".join("?" * len(cols))
    con.execute(f'CREATE TABLE t ({quoted})')
    con.executemany(f'INSERT INTO t ({quoted}) VALUES ({ph})', rows)
    con.commit(); con.close()


def _seed_minimal(d):
    """种入最小数据使 build_day/latest_date 不报错。"""
    _seed(d, "热线", ["时间", "转人工量", "接通量", "排队量", "累计呼入量", "外呼量", "外呼接通量"],
          [("2026-07-27 09:05", 10, 9, 0, 80, 0, 0)])
    _seed(d, "在线", ["时间", "转人工量", "转人工失败", "排队", "咨询", "在线", "小休", "示忙", "话后", "就餐", "培训", "回访"],
          [("2026-07-27 09:05", 5, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0)])


def _client_with_data(d, token=""):
    """创建 TestClient,注入 DATA_DIR 和可选 token。"""
    os.environ["AUTOWFM_DATA_DIR"] = d
    os.environ["AUTOWFM_DASH_TOKEN"] = token
    # 必须在设环境变量后 import,使 api.app 读到正确的 token/data_dir
    import importlib
    import api.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    return TestClient(appmod.app), appmod


def test_health_no_auth():
    """/health 免认证,始终 200。"""
    d = _tmp()
    client, _ = _client_with_data(d, token="secret123")
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}


def test_no_token_401():
    """启用 token 时:无 token -> 401。"""
    d = _tmp()
    _seed_minimal(d)
    client, _ = _client_with_data(d, token="secret123")
    r = client.get("/api/latest-date")
    assert r.status_code == 401, f"无 token 应 401, 实际 {r.status_code}"


def test_wrong_token_401():
    """错误 token -> 401。"""
    d = _tmp()
    _seed_minimal(d)
    client, _ = _client_with_data(d, token="secret123")
    r = client.get("/api/latest-date",
                   headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401, r.status_code


def test_correct_token_200():
    """正确 token -> 200,返回 latest-date。"""
    d = _tmp()
    _seed_minimal(d)
    client, _ = _client_with_data(d, token="secret123")
    r = client.get("/api/latest-date",
                   headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200, r.text
    assert r.json()["date"] == "2026-07-27", r.json()


def test_day_endpoint():
    """/api/day 返回 build_day 结构(含 card/inbound/outbound/tables)。"""
    d = _tmp()
    _seed_minimal(d)
    client, _ = _client_with_data(d, token="secret123")
    r = client.get("/api/day?date=2026-07-27",
                   headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "inbound" in data and "outbound" in data, data.keys()
    assert "card" in data and "tables" in data, data.keys()
    assert data["date"] == "2026-07-27"


def test_month_endpoint():
    """/api/month 返回 build_month 结构。"""
    d = _tmp()
    _seed_minimal(d)
    client, _ = _client_with_data(d, token="secret123")
    r = client.get("/api/month?date=2026-07",
                   headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "inbound" in data, data.keys()


def test_no_auth_when_token_empty():
    """token 留空时不启用认证,直接 200。"""
    d = _tmp()
    _seed_minimal(d)
    client, _ = _client_with_data(d, token="")
    r = client.get("/api/latest-date")
    assert r.status_code == 200, r.text


def main():
    test_health_no_auth()
    test_no_token_401()
    test_wrong_token_401()
    test_correct_token_200()
    test_day_endpoint()
    test_month_endpoint()
    test_no_auth_when_token_empty()
    # 清理环境变量
    os.environ.pop("AUTOWFM_DATA_DIR", None)
    os.environ.pop("AUTOWFM_DASH_TOKEN", None)
    shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("api OK")


if __name__ == "__main__":
    main()
