# -*- coding: utf-8 -*-
"""看板静态资源守卫:Chart.js 必须本地伺服,不得回退公网 CDN;模板必须可编译。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(ROOT, "dashboard", "templates", "dashboard.html")
_JS = os.path.join(ROOT, "dashboard", "static", "chart.umd.min.js")

def test_chartjs_vendored():
    assert os.path.isfile(_JS), f"缺少本地 Chart.js: {_JS}"
    with open(_JS, "rb") as f:
        data = f.read()
    assert len(data) > 100_000, f"chart.umd.min.js 疑似不完整: {len(data)} 字节"
    assert b"Chart" in data, "chart.umd.min.js 内容异常"

def test_template_no_cdn():
    with open(_TPL, encoding="utf-8") as f:
        src = f.read()
    assert "cdn.jsdelivr.net" not in src, "模板不得引用公网 CDN"
    assert "chart.umd.min.js" in src, "模板应引用本地 Chart.js"

def test_template_compiles():
    from dashboard.app import app
    app.jinja_env.get_template("dashboard.html")   # 编译(不渲染),捕获 Jinja 语法错误

def test_ready_signal_wired():
    with open(_TPL, encoding="utf-8") as f:
        src = f.read()
    assert "_pendingCharts" in src and "_chartDone" in src, "就绪信号计数器缺失"
    assert "onComplete:_chartDone" in src, "图表动画完成回调未接线"
    assert 'dataset.ready = "1"' in src, "body[data-ready] 置位逻辑缺失"

def main():
    test_chartjs_vendored()
    test_template_no_cdn()
    test_template_compiles()
    test_ready_signal_wired()
    print("ALL dashboard static tests OK")

if __name__ == "__main__":
    main()
