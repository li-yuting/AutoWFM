# -*- coding: utf-8 -*-
"""承接情况看板 Flask 应用。"""
import datetime
import os
from flask import Flask, render_template, request, redirect, url_for
from dashboard import queries

app = Flask(__name__)
DATA_DIR = "data"

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

@app.route("/")
def index():
    latest = queries.latest_data_date(DATA_DIR)
    if not request.args:
        return redirect(url_for("index", view="day", date=latest))
    view = request.args.get("view", "day")
    date = request.args.get("date")
    if view == "month":
        date = date or datetime.date.today().strftime("%Y-%m")
        data = queries.build_month(date, DATA_DIR)
    else:
        date = date or latest
        data = queries.build_day(date, DATA_DIR)
    return render_template("dashboard.html", view=view, date=date, data=data, latest_date=latest)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=os.environ.get("AUTOWFM_DEBUG", "1") == "1")
