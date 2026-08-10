# -*- coding: utf-8 -*-
"""看板 API 客户端:封装对 FastAPI 服务(:8081)的 HTTP 调用。

看板 dashboard/app.py 通过本模块调 API,而非直连 SQLite,实现存储-展示解耦。
API 不可用时抛 ApiUnavailableError,看板层捕获后显示降级提示。
"""
import os
import logging

import requests

log = logging.getLogger("autowfm.dashboard")

# API 基址:默认 http://127.0.0.1:8081,可由环境变量覆盖
# 用 127.0.0.1 而非 localhost: Windows 上 localhost 优先解析 IPv6(::1),但服务
# 监听 IPv4(0.0.0.0),IPv6 连接失败后回退 IPv4 每次延迟约 2 秒。
API_BASE_URL = os.environ.get("AUTOWFM_API_URL", "http://127.0.0.1:8081").rstrip("/")
_TIMEOUT = 8  # 秒


class ApiUnavailableError(Exception):
    """API 服务不可用(连接失败/超时/非 2xx)。"""


def _headers():
    """构造认证 headers(AUTOWFM_DASH_TOKEN 设了才带)。"""
    token = os.environ.get("AUTOWFM_DASH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get(path, **params):
    """GET 请求,返回 JSON;失败抛 ApiUnavailableError。"""
    url = f"{API_BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=_TIMEOUT)
        if resp.status_code == 401:
            raise ApiUnavailableError(f"API 认证失败(401):检查 AUTOWFM_DASH_TOKEN")
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as e:
        raise ApiUnavailableError(f"无法连接 API 服务({API_BASE_URL}):{e}") from e
    except requests.exceptions.Timeout as e:
        raise ApiUnavailableError(f"API 请求超时({_TIMEOUT}s):{e}") from e
    except requests.exceptions.RequestException as e:
        raise ApiUnavailableError(f"API 请求异常:{e}") from e


def get_latest_date():
    """获取最新数据日期(YYYY-MM-DD)。"""
    return _get("/api/latest-date")["date"]


def get_day(date_str):
    """获取日视图数据(build_day 完整 JSON)。"""
    return _get("/api/day", date=date_str)


def get_month(ym):
    """获取月视图数据(build_month 完整 JSON)。"""
    return _get("/api/month", date=ym)
