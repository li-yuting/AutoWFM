from __future__ import annotations

import os
import sys
import tempfile
import threading
import uuid
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from reader import read_schedule
from scheduler import SchedulerConfig, run_scheduler
from validators import validate_schedule
from writer import write_schedule

# --- PyInstaller support: locate templates folder ---
if getattr(sys, "frozen", False):
    root = sys._MEIPASS
else:
    root = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder=os.path.join(root, "templates"))
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

# In-memory store: token -> output file path
_outputs: dict[str, str] = {}


def _cleanup_old_outputs() -> None:
    keep = set(_outputs.values())
    for f in Path(tempfile.gettempdir()).glob("autoshift_*.xlsx"):
        if str(f) not in keep:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


# WARN 类型名映射（按 check_id）；未映射的以 check_id 兜底
_WARN_LABELS = {
    "03": "需求差异",
    "04": "连续上班超限",
    "05": "连续休息超限",
    "08": "高强连续超限",
    "10": "均衡差异",
    "13": "未知班次",
    "14": "休息间隔过短",
}


def _build_reminders(warnings) -> dict | None:
    """根据验证警告生成简化提醒。班表 sheet 无空白（check_id 01）则返回 None。"""
    if not any(w.check_id == "01" for w in warnings):
        return None
    errors = []
    warn_counts: dict[str, int] = defaultdict(int)
    info_count = 0
    for w in warnings:
        date_str = ""
        if hasattr(w.date, "strftime"):
            date_str = w.date.strftime("%Y-%m-%d")
        if w.severity == "ERROR":
            errors.append({
                "check_id": w.check_id,
                "employee": w.employee,
                "date": date_str,
                "message": w.message,
            })
        elif w.severity == "WARN":
            warn_counts[w.check_id] += 1
        elif w.severity == "INFO" and w.check_id != "12":
            info_count += 1
    warn_groups = [
        {"check_id": cid, "label": _WARN_LABELS.get(cid, cid), "count": n}
        for cid, n in sorted(warn_counts.items())
    ]
    return {
        "errors": errors,
        "warn_groups": warn_groups,
        "info_count": info_count,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/shutdown", methods=["POST"])
def shutdown():
    os._exit(0)


@app.route("/run", methods=["POST"])
def run():
    if "file" not in request.files:
        return jsonify({"error": "请上传排班计划文件"}), 400

    file = request.files["file"]
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "请上传 .xlsx 格式的 Excel 文件"}), 400

    try:
        config = SchedulerConfig(
            preset_rest_days=int(request.form.get("preset_rest_days", 8)),
            max_consecutive_work_normal=int(request.form.get("max_consecutive_work_normal", 6)),
            max_consecutive_work_phase3=int(request.form.get("max_consecutive_work_phase3", 5)),
            max_consecutive_rest=int(request.form.get("max_consecutive_rest", 2)),
            min_work_days_between_rest_blocks=int(request.form.get("min_work_days_between_rest_blocks", 3)),
            max_high_consecutive=int(request.form.get("max_high_consecutive", 2)),
            balance_threshold=int(request.form.get("balance_threshold", 2)),
        )
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"参数格式错误: {e}"}), 400

    tmpdir = tempfile.mkdtemp(prefix="autoshift_")
    input_path = os.path.join(tmpdir, file.filename)
    file.save(input_path)

    out_name = request.form.get("output_name", "排班结果.xlsx").strip()
    if not out_name.endswith(".xlsx"):
        out_name += ".xlsx"
    output_path = os.path.join(tmpdir, out_name)

    try:
        schedule = read_schedule(input_path)
        run_scheduler(schedule, config)
        schedule.warnings.clear()
        validate_schedule(schedule, config)
        write_schedule(schedule, output_path)
    except Exception as e:
        return jsonify({"error": f"排班运行失败: {e}"}), 500

    errors = [w for w in schedule.warnings if w.severity == "ERROR"]
    warns = [w for w in schedule.warnings if w.severity == "WARN"]
    infos = [w for w in schedule.warnings if w.severity == "INFO"]

    reminders = _build_reminders(schedule.warnings)

    token = uuid.uuid4().hex
    _outputs[token] = output_path
    _cleanup_old_outputs()

    return jsonify({
        "token": token,
        "download_name": out_name,
        "summary": {
            "employees": len(schedule.employees),
            "dates": len(schedule.dates),
            "error_count": len(errors),
            "warn_count": len(warns),
            "info_count": len(infos),
        },
        "reminders": reminders,
    })


@app.route("/download/<token>")
def download(token: str):
    path = _outputs.pop(token, None)
    if not path or not os.path.isfile(path):
        return jsonify({"error": "下载链接已失效，请重新运行"}), 404
    out_name = os.path.basename(path)
    return send_file(path, as_attachment=True, download_name=out_name)


@app.route("/template")
def template():
    path = os.path.join(root, "排班计划.xlsx")
    if not os.path.isfile(path):
        return jsonify({"error": "模板文件不存在"}), 404
    return send_file(path, as_attachment=True, download_name="排班计划.xlsx")


def _open_browser():
    import webbrowser
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Timer(1.0, _open_browser).start()
    app.run(host="127.0.0.1", port=5000, use_reloader=False)
