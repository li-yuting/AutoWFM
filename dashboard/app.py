# -*- coding: utf-8 -*-
"""承接情况看板 Flask 应用。

数据来源:通过 dashboard.api_client 调 FastAPI 服务(:8081),不再直连 SQLite。
API 不可用时降级为直连 queries(向后兼容,需 data/ 目录存在)。
"""
import datetime
import os
import logging
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from dashboard import queries
from dashboard import api_client

# 加载 .env(若存在),使直接 python -m dashboard.app 也能读到 AUTOWFM_DASH_TOKEN
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
DATA_DIR = "data"
log = logging.getLogger("autowfm.dashboard")

# 看板 Bearer Token 认证: 读 AUTOWFM_DASH_TOKEN(由 manager 通过环境变量注入,或本地 .env 提供)。
# 留空 -> 不启用认证(本地开发); 设值 -> 除白名单外所有请求需带 Authorization: Bearer <token>。
_DASH_TOKEN = os.environ.get("AUTOWFM_DASH_TOKEN", "").strip()
# 认证豁免路径(健康检查等)
_PUBLIC_PATHS = {"/health"}


@app.before_request
def _check_auth():
    """Bearer Token 校验;未配置 token 时放行(向后兼容本地开发)。"""
    if not _DASH_TOKEN:
        return None
    if request.path in _PUBLIC_PATHS:
        return None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[len("Bearer "):].strip() == _DASH_TOKEN:
        return None
    abort(401, description="未授权: 缺少或无效的 Bearer Token")


@app.errorhandler(401)
def _unauthorized(e):
    return ("未授权: 需要有效的 Bearer Token", 401)

# 流入率/接通率 阈值高亮：< 下限红、> 上限绿。val 形如 '89.47%'，key 含指标名(流入率/接通率)。
_RATE_THRESHOLDS = {"流入率": (90, 105), "接通率": (92, 95)}

def rate_class(val, key):
    """按流入率/接通率数值返回高亮 CSS 类；非率值或键不匹配返回空串。"""
    if not isinstance(val, str) or "%" not in val:
        return ""
    try:
        n = float(val.replace("%", ""))
    except ValueError:
        return ""
    for name, (lo, hi) in _RATE_THRESHOLDS.items():
        if name in key:
            if n < lo:
                return "text-danger"
            if n > hi:
                return "text-success"
            return ""
    return ""

app.jinja_env.filters["rate_class"] = rate_class

@app.route("/health")
def health():
    """健康检查端点:返回 {"status":"ok"}。免认证(供管理器/负载均衡探活)。"""
    return jsonify({"status": "ok"})

@app.route("/")
def index():
    # 优先调 FastAPI;不可用时降级直连 queries(向后兼容)
    try:
        latest = api_client.get_latest_date()
    except api_client.ApiUnavailableError as e:
        log.warning(f"API 不可用,降级直连 queries: {e}")
        latest = queries.latest_data_date(DATA_DIR)
    if not request.args:
        return redirect(url_for("index", view="day", date=latest))
    view = request.args.get("view", "day")
    date = request.args.get("date")
    if view == "month":
        date = date or datetime.date.today().strftime("%Y-%m")
        try:
            data = api_client.get_month(date)
        except api_client.ApiUnavailableError as e:
            log.warning(f"API 不可用,降级直连 queries: {e}")
            data = queries.build_month(date, DATA_DIR)
    else:
        date = date or latest
        try:
            data = api_client.get_day(date)
        except api_client.ApiUnavailableError as e:
            log.warning(f"API 不可用,降级直连 queries: {e}")
            data = queries.build_day(date, DATA_DIR)
    return render_template("dashboard.html", view=view, date=date, data=data, latest_date=latest)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=os.environ.get("AUTOWFM_DEBUG", "1") == "1")
