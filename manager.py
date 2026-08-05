"""AutoWFM 桌面管理器。

管理采集器(`collector.main`)与看板(`dashboard.app`)两个子进程:
- 自动启停:每天到采集器最早窗口(工作日 08:30 / 周末 09:00,从 config.yaml 推导)启动,
  到全局窗口结束(21:00)停止。
- 崩溃重启:运行时段内进程意外退出自动重启;启动后 30s 内崩溃计为一次失败,
  连续 3 次失败则暂停自动重启并弹出置顶告警。
- 手工控制:每个任务可手动 启动/停止/重启。

运行:
    .\\.venv\\Scripts\\python.exe manager.py
"""
from __future__ import annotations
import datetime as dt
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

from collector._utils import parse_hhmm, fmt_hhmm, load_cfg

try:
    from pystray import Icon, Menu, MenuItem
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except Exception:  # 依赖未装时降级为普通窗口
    _TRAY_AVAILABLE = False

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
CONFIG_PATH = ROOT / "config.yaml"
LOG_DIR = ROOT / "logs"
COLLECTOR_LOG = LOG_DIR / "autowfm.log"
DASHBOARD_LOG = LOG_DIR / "dashboard.log"
MANAGER_LOG = LOG_DIR / "manager.log"
SHIFT_LOG = LOG_DIR / "shift.log"

MONITOR_INTERVAL_MS = 5000
GRACE_SECONDS = 30          # 启动后存活不足此秒数即退出 -> 计为一次失败重启
MAX_FAILURES = 3
LOG_PREVIEW_LINES = 80

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(MANAGER_LOG),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)
log = logging.getLogger("manager")

AUTOSTART_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/AutoWFM.lnk"


def autostart_enabled() -> bool:
    return AUTOSTART_LNK.exists()


def set_autostart(enabled: bool) -> None:
    """创建/删除开机自启快捷方式(指向 pythonw.exe manager.py,无控制台)。"""
    if enabled:
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if not pythonw.exists():
            pythonw = Path(sys.executable)
        ps = (
            "$ws=New-Object -ComObject WScript.Shell;"
            f"$l=$ws.CreateShortcut('{AUTOSTART_LNK}');"
            f"$l.TargetPath='{pythonw}';"
            f"$l.Arguments='manager.py';"
            f"$l.WorkingDirectory='{ROOT}';"
            f"$l.WindowStyle=7;"  # 最小化
            f"$l.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        log.info("开机自启已开启: %s", AUTOSTART_LNK)
    else:
        if AUTOSTART_LNK.exists():
            AUTOSTART_LNK.unlink()
            log.info("开机自启已关闭")


def _make_tray_image():
    """生成简单托盘图标(绿色圆,无需 .ico 文件)。"""
    img = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
    ImageDraw.Draw(img).ellipse((10, 10, 54, 54), fill=(22, 128, 60, 255))
    return img


# ── 调度(纯函数,可测试)─────────────────────────────────────────────

def compute_auto_start(cfg: dict, now: dt.datetime) -> int:
    """采集器当天最早采集时间(分钟):所有 sub 窗口起点的最小值。"""
    g_start = parse_hhmm(cfg["schedule"]["window_start"])
    starts = []
    for s in cfg["subs"]:
        sch = s.get("schedule")
        if sch and ("weekday" in sch or "weekend" in sch):
            w = sch["weekday"] if now.weekday() < 5 else sch["weekend"]
            starts.append(parse_hhmm(w["start"]))
        else:
            starts.append(g_start)
    return min(starts) if starts else g_start


def auto_stop_minutes(cfg: dict) -> int:
    return parse_hhmm(cfg["schedule"]["window_end"])


def in_run_window(cfg: dict, now: dt.datetime) -> bool:
    """运行时段 [auto_start, auto_stop)。"""
    start = compute_auto_start(cfg, now)
    stop = auto_stop_minutes(cfg)
    mins = now.hour * 60 + now.minute
    return start <= mins < stop


def schedule_text(cfg: dict, now: dt.datetime) -> str:
    start = compute_auto_start(cfg, now)
    stop = auto_stop_minutes(cfg)
    day = "工作日" if now.weekday() < 5 else "周末"
    return f"每日计划({day}): {fmt_hhmm(start)} 自动启动 → {fmt_hhmm(stop)} 自动停止"


# ── 受管任务 ────────────────────────────────────────────────────────

class ManagedTask:
    def __init__(self, name: str, module: str, log_path: Path, capture_log: bool,
                 env_extra: dict | None = None, script: str | None = None,
                 cwd: Path | None = None, match_key: str | None = None,
                 auto_enabled: bool = True):
        self.name = name
        self.module = module                      # "collector.main" / "dashboard.app"
        self.script = script                      # 若非 None,改为运行脚本(如排班 app.py)
        self.cwd = cwd or ROOT                    # 运行工作目录(脚本所属项目目录)
        self.match_key = match_key                # 若非 None,external-PID 按此串匹配(否则取脚本名/module)
        self.auto_enabled = auto_enabled          # False -> 仅手动启停,不自动启停/自动重启
        self.cmd = [PYTHON, "-m", module] if script is None else [PYTHON, script]
        self.log_path = log_path
        self.capture_log = capture_log            # True -> 把子进程 stdout 写入 log_path
        self.env_extra = env_extra or {}

        self.process: subprocess.Popen | None = None
        self.external_pid: int | None = None
        self.user_stopped = False
        self.restart_failures = 0
        self.started_at: dt.datetime | None = None
        self.popup_shown = False                  # 3 次失败告警去重
        self._log_handle = None

        self._current_date: dt.date | None = None # 当前已重置到的日期
        self.auto_started_today = False           # 当天是否已触发过自动启动
        self.auto_stopped_today = False           # 当天是否已触发过自动停止

    # ---- 进程状态 ----
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _env(self) -> dict:
        env = dict(os.environ)
        env.update(self.env_extra)
        return env

    def _find_external_pid(self) -> int | None:
        """查找未由本 UI 启动、但命令行匹配的 python 进程(避免重复拉起,尤其看板端口占用)。"""
        if os.name != "nt":
            return None
        # 优先用显式 match_key,否则脚本模式取脚本文件名,模块模式取 module 字符串
        if self.match_key:
            match_key = self.match_key
        elif self.script:
            match_key = self.script.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        else:
            match_key = self.module
        match_key = match_key.replace('"', '`"')
        command = (
            "Get-CimInstance Win32_Process | "
            f"Where-Object {{$_.CommandLine -like '*{match_key}*' -and ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe')}} | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                cwd=str(ROOT), capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode != 0:
                return None
            lines = r.stdout.strip().splitlines()
            return int(lines[0]) if lines else None
        except Exception:
            return None

    def _kill_external(self) -> None:
        if not self.external_pid or os.name != "nt":
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.external_pid), "/T", "/F"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log.info("%s: 已停止外部进程 pid=%s", self.name, self.external_pid)
        except Exception:
            log.exception("%s: 停止外部进程失败 pid=%s", self.name, self.external_pid)

    def _cleanup_process(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        self.process = None
        self.started_at = None

    # ---- 启停 ----
    def start(self, automatic: bool = False) -> bool:
        if self.is_running():
            return True
        ext = self._find_external_pid()
        if ext:
            self.external_pid = ext
            self.user_stopped = False
            self.restart_failures = 0
            self.popup_shown = False
            log.info("%s: 发现外部进程 pid=%s,接管", self.name, ext)
            return True

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            if self.capture_log:
                self._log_handle = open(self.log_path, "ab")
                stdout = self._log_handle
            else:
                stdout = subprocess.DEVNULL
            self.process = subprocess.Popen(
                self.cmd, cwd=str(self.cwd),
                stdout=stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=creationflags, env=self._env(),
            )
            self.started_at = dt.datetime.now().astimezone()
            self.user_stopped = False
            self.popup_shown = False
            action = "自动重启" if automatic else "手动启动"
            log.info("%s: %s pid=%s", self.name, action, self.process.pid)
            return True
        except Exception as exc:
            log.exception("%s: 启动失败: %s", self.name, exc)
            return False

    def stop(self, automatic: bool = False) -> None:
        if not automatic:
            self.user_stopped = True
        if self.external_pid and not self.is_running():
            self._kill_external()
            self.external_pid = None
            return
        if not self.is_running():
            self._cleanup_process()
            self.external_pid = None
            return
        pid = self.process.pid
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            log.info("%s: 已停止 pid=%s", self.name, pid)
        except Exception:
            log.exception("%s: 停止失败 pid=%s", self.name, pid)
        finally:
            self._cleanup_process()
            self.external_pid = None

    def restart(self) -> None:
        self.stop()
        self.user_stopped = False
        self.restart_failures = 0
        self.popup_shown = False
        self.start()

    # ---- 监控一拍 ----
    def tick(self, in_window: bool, now: dt.datetime) -> list[dict]:
        """每 MONITOR_INTERVAL_MS 调一次;返回事件列表({"type":"alert"|"info","msg":...})。"""
        events: list[dict] = []
        today = now.date()

        # 1. 跨天重置
        if self._current_date != today:
            self._current_date = today
            self.auto_started_today = False
            self.auto_stopped_today = False
            self.user_stopped = False
            self.restart_failures = 0
            self.popup_shown = False
            log.info("%s: 跨天重置,日期=%s", self.name, today)

        # 仅手动启停的任务:不做任何自动启停/自动重启,完全由手动控制
        if not self.auto_enabled:
            return events

        # 2. 自动停止（每天触发一次）
        if not in_window and not self.auto_stopped_today:
            if self.is_running() or self.external_pid:
                self.stop(automatic=True)
                events.append({"type": "info", "msg": f"{self.name}: 超出运行时段,已自动停止"})
            self.auto_stopped_today = True
            return events

        # 3. 自动启动（每天触发一次）
        if in_window and not self.auto_started_today:
            if not self.is_running() and self.external_pid is None and not self.user_stopped:
                self.start(automatic=True)
                events.append({"type": "info", "msg": f"{self.name}: 到达运行时段,已自动启动"})
            self.auto_started_today = True

        # 4. 崩溃重启与健康检查（仅在窗口内）
        if in_window:
            if self.process is not None:
                rc = self.process.poll()
                if rc is None:
                    # 仍在运行;跑过 grace 即视为健康,清零失败计数
                    if self.started_at and (now - self.started_at).total_seconds() >= GRACE_SECONDS:
                        if self.restart_failures:
                            self.restart_failures = 0
                else:
                    # 进程已退出
                    self._cleanup_process()
                    if self.user_stopped:
                        return events
                    uptime = (now - self.started_at).total_seconds() if self.started_at else 0
                    if uptime < GRACE_SECONDS:
                        self.restart_failures += 1
                        log.warning("%s: 启动后 %.0fs 即退出(rc=%s),失败 %d/%d",
                                    self.name, uptime, rc, self.restart_failures, MAX_FAILURES)
                    else:
                        self.restart_failures = 0
                        log.warning("%s: 运行 %.0fs 后退出(rc=%s),正常重启", self.name, uptime, rc)
                    if self.restart_failures >= MAX_FAILURES:
                        if not self.popup_shown:
                            self.popup_shown = True
                            events.append({"type": "alert",
                                           "msg": f"{self.name} 连续重启 {MAX_FAILURES} 次失败,已暂停自动重启。\n请检查日志:{self.log_path}"})
                        return events
                    self.start(automatic=True)
            elif self.external_pid is None:
                # 没有任何进程
                if not self.user_stopped and self.restart_failures >= MAX_FAILURES:
                    if not self.popup_shown:
                        self.popup_shown = True
                        events.append({"type": "alert",
                                       "msg": f"{self.name} 连续重启 {MAX_FAILURES} 次失败,已暂停自动重启。\n请检查日志:{self.log_path}"})
                elif not self.user_stopped:
                    self.start(automatic=True)
        return events


# 任务定义:看板用 AUTOWFM_DEBUG=0 关掉 reloader,单进程便于崩溃检测/停止
TASK_DEFS = [
    dict(name="采集器", module="collector.main", log_path=COLLECTOR_LOG, capture_log=False),
    dict(name="看板", module="dashboard.app", log_path=DASHBOARD_LOG, capture_log=True,
         env_extra={"AUTOWFM_DEBUG": "0"}),
    dict(name="排班", module="", log_path=SHIFT_LOG, capture_log=True,
         script="shift_manager.py", cwd=ROOT, match_key="app.py",
         env_extra={"AUTOWFM_MANAGED": "1"}, auto_enabled=False),
]


# ── UI ──────────────────────────────────────────────────────────────

class ManagerUI:
    def __init__(self, root: tk.Tk, cfg: dict) -> None:
        self.root = root
        self.cfg = cfg
        self.root.title("AutoWFM 管理器")
        self.root.geometry("980x680")
        self.root.minsize(820, 560)

        self.tasks: list[ManagedTask] = [ManagedTask(**d) for d in TASK_DEFS]
        self._vars: list[dict[str, tk.StringVar]] = []
        self._log_boxes: list[scrolledtext.ScrolledText] = []
        self._nav_buttons: list[tk.Button] = []
        self._nav_pages: list[tk.Frame] = []
        self.tray_icon = None
        self._tray_first_hide = True

        self.schedule_var = tk.StringVar(value=schedule_text(cfg, dt.datetime.now()))
        self.autostart_var = tk.StringVar(value=self._autostart_label())
        self._forecast_date = ""  # 已跑过预测的日期 YYYY-MM-DD，防重复
        self._forecast_running = False  # 去重锁
        self._backfill_running = False  # 数据补全去重锁
        self._build_ui()
        self._build_tray()
        self._refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_unmap)

    def _build_ui(self) -> None:
        top = tk.Frame(self.root, padx=12, pady=10)
        top.pack(fill=tk.X)
        tk.Label(top, text="AutoWFM 管理器", font=("Microsoft YaHei UI", 15, "bold")).pack(side=tk.LEFT)
        tk.Label(top, textvariable=self.schedule_var, padx=16, fg="#555555").pack(side=tk.LEFT)
        ttk.Separator(self.root).pack(fill=tk.X, padx=12)

        strip = tk.Frame(self.root, padx=12, pady=6)
        strip.pack(fill=tk.X)
        strip.grid_columnconfigure(6, weight=1)  # 列6 弹性间距,把按钮推到右侧
        for r, task in enumerate(self.tasks):
            vars_ = {
                "status": tk.StringVar(value="未运行"),
                "pid": tk.StringVar(value="PID: -"),
                "fail": tk.StringVar(value="失败: 0/3"),
                "src": tk.StringVar(value=""),
            }
            self._vars.append(vars_)
            dot = tk.Label(strip, text="●", font=("Microsoft YaHei UI", 12), fg="#666666")
            dot.grid(row=r, column=0, padx=(0, 6), sticky="w")
            vars_["status_dot"] = dot
            tk.Label(strip, text=task.name, font=("Microsoft YaHei UI", 11, "bold")).grid(row=r, column=1, padx=(0, 10), sticky="w")
            status_lbl = tk.Label(strip, textvariable=vars_["status"], font=("Microsoft YaHei UI", 13, "bold"))
            status_lbl.grid(row=r, column=2, padx=(0, 12), sticky="w")
            vars_["status_label"] = status_lbl
            tk.Label(strip, textvariable=vars_["pid"]).grid(row=r, column=3, padx=(0, 12), sticky="w")
            tk.Label(strip, textvariable=vars_["fail"]).grid(row=r, column=4, padx=(0, 12), sticky="w")
            tk.Label(strip, textvariable=vars_["src"], fg="#777777").grid(row=r, column=5, sticky="w")
            btns = tk.Frame(strip)
            btns.grid(row=r, column=7, sticky="e")
            tk.Button(btns, text="启动", width=8, command=lambda t=task: self._manual_start(t)).pack(side=tk.LEFT, padx=3)
            tk.Button(btns, text="停止", width=8, command=lambda t=task: self._manual_stop(t)).pack(side=tk.LEFT, padx=3)
            tk.Button(btns, text="重启", width=8, command=lambda t=task: self._manual_restart(t)).pack(side=tk.LEFT, padx=3)
        ttk.Separator(self.root).pack(fill=tk.X, padx=12)

        # 主区: 左导航 + 右内容
        main = tk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 4))
        nav = tk.Frame(main, width=120)
        nav.pack(side=tk.LEFT, fill=tk.Y)
        nav.pack_propagate(False)
        content = tk.Frame(main)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        nav_items: list[tuple[str, tk.Frame]] = []
        for task in self.tasks:
            page = tk.Frame(content)
            page.grid(row=0, column=0, sticky="nsew")
            box = scrolledtext.ScrolledText(page, wrap=tk.WORD)
            box.pack(fill=tk.BOTH, expand=True)
            box.configure(state=tk.DISABLED)
            self._log_boxes.append(box)
            nav_items.append((f"{task.name} 日志", page))
        fc_page = tk.Frame(content)
        fc_page.grid(row=0, column=0, sticky="nsew")
        self._build_forecast_page(fc_page)
        nav_items.append(("进线量预测", fc_page))
        bf_page = tk.Frame(content)
        bf_page.grid(row=0, column=0, sticky="nsew")
        self._build_backfill_page(bf_page)
        nav_items.append(("数据补全", bf_page))

        self._nav_pages = [page for _, page in nav_items]
        self._nav_buttons = []
        for idx, (label, _page) in enumerate(nav_items):
            btn = tk.Button(nav, text=label, relief=tk.RAISED,
                            command=lambda i=idx: self._show_page(i))
            btn.pack(fill=tk.X, pady=(0, 4))
            self._nav_buttons.append(btn)
        self._show_page(0)
        ttk.Separator(self.root).pack(fill=tk.X, padx=12)

        bar = tk.Frame(self.root, padx=12, pady=6)
        bar.pack(fill=tk.X)
        tk.Button(bar, text="刷新日志", command=self._load_logs).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        tk.Button(bar, textvariable=self.autostart_var, command=self._toggle_autostart).pack(side=tk.LEFT)
        tk.Button(bar, text="重启控制台", command=self._restart_manager).pack(side=tk.LEFT, padx=8)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        tk.Label(bar, text=f"管理器日志: {MANAGER_LOG}", fg="#777777").pack(side=tk.RIGHT)

    def _show_page(self, idx: int) -> None:
        """切换左侧导航到第 idx 个内容页(raise_ 叠放帧 + 高亮选中按钮)。"""
        self._nav_pages[idx].tkraise()
        for i, btn in enumerate(self._nav_buttons):
            btn.configure(relief=tk.SUNKEN if i == idx else tk.RAISED)

    # ---- 手动操作 ----
    def _manual_start(self, task: ManagedTask) -> None:
        task.restart_failures = 0
        task.popup_shown = False
        task.start(automatic=False)
        self._update_status()

    def _manual_stop(self, task: ManagedTask) -> None:
        task.stop(automatic=False)
        self._update_status()

    def _manual_restart(self, task: ManagedTask) -> None:
        task.restart()
        self._update_status()

    # ---- 监控循环 ----
    def _refresh(self) -> None:
        try:
            now = dt.datetime.now().astimezone()  # aware,与 started_at 对齐,避免 now-started_at 时区不一致抛 TypeError
            self.schedule_var.set(schedule_text(self.cfg, now))
            in_win = in_run_window(self.cfg, now)
            for task in self.tasks:
                for ev in task.tick(in_win, now):
                    if ev["type"] == "alert":
                        self._show_alert(f"{task.name} 告警", ev["msg"])
                    else:
                        log.info(ev["msg"])
            self._update_status()
            self._load_logs()
            self._check_forecast(now)
        except Exception:
            log.exception("监控循环异常")
        self.root.after(MONITOR_INTERVAL_MS, self._refresh)

    def _update_status(self) -> None:
        for task, vars_ in zip(self.tasks, self._vars):
            if task.is_running():
                vars_["status"].set("运行中")
                vars_["status_label"].configure(fg="#16803c")
                vars_["status_dot"].configure(fg="#16803c")
                vars_["pid"].set(f"PID: {task.process.pid}")
                vars_["src"].set("UI 管理")
            elif task.external_pid:
                vars_["status"].set("运行中(外部)")
                vars_["status_label"].configure(fg="#16803c")
                vars_["status_dot"].configure(fg="#16803c")
                vars_["pid"].set(f"PID: {task.external_pid}")
                vars_["src"].set("外部启动")
            else:
                if task.restart_failures >= MAX_FAILURES:
                    vars_["status"].set("已暂停重启")
                    vars_["status_label"].configure(fg="#aa2222")
                    vars_["status_dot"].configure(fg="#aa2222")
                elif task.user_stopped:
                    vars_["status"].set("已停止")
                    vars_["status_label"].configure(fg="#666666")
                    vars_["status_dot"].configure(fg="#666666")
                else:
                    vars_["status"].set("未运行")
                    vars_["status_label"].configure(fg="#aa2222")
                    vars_["status_dot"].configure(fg="#aa2222")
                vars_["pid"].set("PID: -")
                vars_["src"].set("")
            vars_["fail"].set(f"失败: {task.restart_failures}/{MAX_FAILURES}")

    # ---- 日志预览 ----
    def _load_logs(self) -> None:
        for task, box in zip(self.tasks, self._log_boxes):
            lines = self._tail(task.log_path, LOG_PREVIEW_LINES)
            if not lines:
                hint = "尚未找到日志。" if not task.capture_log else f"等待 {task.name} 输出(写入 {task.log_path})。"
                lines = [hint + "\n"]
            box.configure(state=tk.NORMAL)
            box.delete("1.0", tk.END)
            box.insert(tk.END, "".join(lines))
            box.see(tk.END)
            box.configure(state=tk.DISABLED)

    @staticmethod
    def _tail(path: Path, max_lines: int) -> list[str]:
        if not path.exists():
            return []
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-max_lines:]
        except Exception as exc:
            return [f"读取日志失败: {exc}\n"]

    # ---- 次日预测量差异对比(21:05 自动触发,不依赖采集器进程)----
    def _check_forecast(self, now: dt.datetime) -> None:
        """每天 21:05 后跑一次 check_next_day_diff，不与采集器进程绑定。"""
        today = now.strftime("%Y-%m-%d")
        if self._forecast_date == today or self._forecast_running:
            return
        if now.hour < 21 or (now.hour == 21 and now.minute < 5):
            return
        self._forecast_running = True
        threading.Thread(target=self._run_forecast_diff, args=(today,), daemon=True, name="forecast-diff").start()

    def _run_forecast_diff(self, today: str) -> None:
        try:
            from collector.forecast import check_next_day_diff
            check_next_day_diff(self.cfg)
            log.info("[forecast-diff] 次日预测量差异对比完成 (%s)", today)
        except Exception:
            log.exception("[forecast-diff] 次日预测量差异对比异常")
        finally:
            self._forecast_date = today
            self._forecast_running = False

    # ---- 告警弹窗(置顶)----
    def _show_alert(self, title: str, message: str) -> None:
        log.warning("alert: %s - %s", title, message.replace("\n", " "))
        try:
            if self.root.state() in ("iconic", "withdrawn"):
                self.root.deiconify()
                self.root.lift()
        except Exception:
            pass
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.attributes("-topmost", True)
        popup.geometry("480x240")
        popup.resizable(False, False)
        popup.grab_set()
        tk.Label(popup, text=title, font=("Microsoft YaHei UI", 14, "bold"), fg="#aa2222").pack(pady=(18, 8))
        tk.Label(popup, text=f"{message}\n\n时间: {now:%Y-%m-%d %H:%M:%S}", justify=tk.LEFT, wraplength=440).pack(padx=20, pady=8, fill=tk.X)
        tk.Button(popup, text="知道了", width=12, command=popup.destroy).pack(pady=12)
        popup.lift()
        popup.focus_force()

    # ---- 托盘 + 退出 ----
    def _on_close(self) -> None:
        # 关闭按钮:有托盘则最小化到托盘(任务继续),否则走退出确认
        if self.tray_icon is not None:
            self._hide_to_tray()
        else:
            self._quit_with_confirm()

    def _on_unmap(self, event: tk.Event) -> None:
        # 最小化按钮也隐藏到托盘
        if event.widget is not self.root:
            return
        try:
            if self.root.state() == "iconic" and self.tray_icon is not None:
                self._hide_to_tray()
        except Exception:
            pass

    def _build_tray(self) -> None:
        if not _TRAY_AVAILABLE:
            self.tray_icon = None
            return
        try:
            self.tray_icon = Icon(
                "autowfm_manager", _make_tray_image(), "AutoWFM 管理器",
                menu=Menu(
                    MenuItem("显示窗口", self._tray_show, default=True),
                    Menu.SEPARATOR,
                    MenuItem("退出", self._tray_quit),
                ),
            )
            threading.Thread(target=self.tray_icon.run, daemon=True, name="tray").start()
        except Exception:
            log.exception("托盘初始化失败")
            self.tray_icon = None

    def _hide_to_tray(self) -> None:
        self.root.withdraw()
        if self.tray_icon is not None and self._tray_first_hide:
            self._tray_first_hide = False
            try:
                self.tray_icon.notify("已最小化到托盘,采集继续运行。双击图标恢复窗口。", "AutoWFM 管理器")
            except Exception:
                pass

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_show(self, icon, item) -> None:
        self.root.after(0, self._restore_window)   # 托盘线程回调,切回主线程

    def _tray_quit(self, icon, item) -> None:
        self.root.after(0, self._quit_with_confirm)

    def _quit_with_confirm(self) -> None:
        self._restore_window()
        running = [t for t in self.tasks if t.is_running() or t.external_pid]
        if running:
            names = "、".join(t.name for t in running)
            if not messagebox.askyesno("退出管理器", f"{names} 仍在运行。退出管理器会停止这些任务,是否继续?"):
                return
            for t in running:
                t.stop()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.root.destroy()

    # ---- 开机自启 ----
    def _autostart_label(self) -> str:
        return "开机自启: 已开启" if autostart_enabled() else "开机自启: 已关闭"

    def _toggle_autostart(self) -> None:
        try:
            set_autostart(not autostart_enabled())
        except Exception as exc:
            log.exception("切换开机自启失败: %s", exc)
            messagebox.showerror("开机自启", f"切换失败:\n{exc}")
        self.autostart_var.set(self._autostart_label())

    # ---- 进线量预测(手动触发;定时 21:05 差异对比由本管理器自动跑,见 _check_forecast)----
    def _build_forecast_page(self, page: tk.Frame) -> None:
        top = tk.Frame(page, padx=10, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="预测未来天数:").pack(side=tk.LEFT)
        self.forecast_days_var = tk.StringVar(value="7")
        tk.Spinbox(top, from_=1, to=30, width=5, textvariable=self.forecast_days_var).pack(side=tk.LEFT, padx=6)
        self.btn_forecast = tk.Button(top, text="运行预测", width=10, command=self._run_forecast)
        self.btn_forecast.pack(side=tk.LEFT, padx=6)
        self.forecast_status_var = tk.StringVar(value="就绪")
        tk.Label(top, textvariable=self.forecast_status_var, fg="#555555").pack(side=tk.LEFT, padx=10)
        self.forecast_box = scrolledtext.ScrolledText(page, wrap=tk.WORD, font=("Consolas", 10))
        self.forecast_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self.forecast_box.configure(state=tk.DISABLED)
        self._set_forecast_text(
            "输入天数后点「运行预测」(默认 7 天)。预测基于 data/热线.db、在线.db 每日 21:00 累计转人工量,\n"
            "需有足够历史(建议 ≥7 天)。每天 21:05 管理器自动跑次日预测量差异对比(超 10% 发企微告警)。\n"
            "此处为手动触发全量预测,结果同时写入 output/进线量预测_YYYYMMDD.csv。")

    def _set_forecast_text(self, text: str) -> None:
        self.forecast_box.configure(state=tk.NORMAL)
        self.forecast_box.delete("1.0", tk.END)
        self.forecast_box.insert(tk.END, text)
        self.forecast_box.configure(state=tk.DISABLED)

    def _set_forecast_status(self, s: str) -> None:
        self.forecast_status_var.set(s)

    def _run_forecast(self) -> None:
        try:
            days = int(self.forecast_days_var.get())
        except ValueError:
            self._set_forecast_status("天数需为整数"); return
        except Exception as exc:
            log.exception("启动预测失败")
            self._set_forecast_status(f"启动失败: {exc}")
            return
        if not (1 <= days <= 30):
            self._set_forecast_status("天数应在 1-30"); return
        self._set_forecast_status("运行中...")
        self._set_forecast_text("正在预测,请稍候...")
        self.btn_forecast.configure(state=tk.DISABLED)

        def worker():
            try:
                from collector import forecast
                out, out_path = forecast.run_forecast(days=days, write_csv=True)
                summary = self._forecast_summary(out, out_path)
                self.root.after(0, self._on_forecast_done, summary, None)
            except Exception as exc:
                log.exception("手动预测失败")
                self.root.after(0, self._on_forecast_done, "", exc)

        threading.Thread(target=worker, daemon=True, name="forecast").start()

    def _on_forecast_done(self, summary: str, err: Exception | None) -> None:
        self.btn_forecast.configure(state=tk.NORMAL)
        if err is not None:
            self._set_forecast_status("失败")
            self._set_forecast_text(
                f"预测失败: {err}\n\n常见原因:\n"
                "- data/热线.db / 在线.db 缺少每日 21:00 的累计转人工量历史\n"
                "- 历史不足导致回归拟合失败\n请查看 logs/manager.log")
        else:
            self._set_forecast_status("完成")
            self._set_forecast_text(summary)

    @staticmethod
    def _forecast_summary(out, out_path) -> str:
        """把预测 DataFrame 格式化为文本摘要(各业务合计/日均/超界日期 + CSV 路径)。"""
        import pandas as pd
        lines = []
        for business in ["热线", "在线"]:
            sub = out[out["业务"] == business]
            if sub.empty:
                lines.append(f"{business}: 无预测数据"); continue
            n = len(sub)
            total = int(sub["预测转人工量"].sum())
            avg = round(total / n) if n else 0
            out_dates = sub[sub["超界标记"] == "是"]["预测日期"]
            ds = ", ".join(pd.Timestamp(d).strftime("%m-%d") for d in out_dates) or "无"
            lines.append(f"{business}: {n}天合计 {total}  日均 {avg}")
            lines.append(f"  超界日期: {ds}")
        if out_path:
            lines.append(f"CSV: {out_path}")
        return "\n".join(lines)

    # ---- 数据补全(手动触发,5 分钟颗粒度回填)----
    def _build_backfill_page(self, page: tk.Frame) -> None:
        top = tk.Frame(page, padx=10, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="开始日期:").pack(side=tk.LEFT)
        self.bf_start_var = tk.StringVar()
        tk.Entry(top, width=12, textvariable=self.bf_start_var).pack(side=tk.LEFT, padx=4)
        tk.Label(top, text="结束日期:").pack(side=tk.LEFT)
        self.bf_end_var = tk.StringVar()
        tk.Entry(top, width=12, textvariable=self.bf_end_var).pack(side=tk.LEFT, padx=4)
        self.bf_src_hl = tk.BooleanVar(value=True)
        self.bf_src_gd = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="会话记录", variable=self.bf_src_hl).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(top, text="工单明细", variable=self.bf_src_gd).pack(side=tk.LEFT, padx=4)
        self.btn_backfill = tk.Button(top, text="开始补全", width=10, command=self._run_backfill)
        self.btn_backfill.pack(side=tk.LEFT, padx=6)
        self.bf_status_var = tk.StringVar(value="就绪")
        tk.Label(top, textvariable=self.bf_status_var, fg="#555555").pack(side=tk.LEFT, padx=10)
        self.bf_box = scrolledtext.ScrolledText(page, wrap=tk.WORD, font=("Consolas", 10))
        self.bf_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self.bf_box.configure(state=tk.DISABLED)
        self._set_backfill_text(
            "输入开始/结束日期(YYYY-MM-DD，结束留空=单日)，勾选源后点「开始补全」。\n"
            "按 5 分钟颗粒度回填历史数据(总是覆盖)。含今天时建议先停采集器。")

    def _set_backfill_text(self, text: str) -> None:
        self.bf_box.configure(state=tk.NORMAL)
        self.bf_box.delete("1.0", tk.END)
        self.bf_box.insert(tk.END, text)
        self.bf_box.configure(state=tk.DISABLED)

    def _append_backfill_text(self, text: str) -> None:
        self.bf_box.configure(state=tk.NORMAL)
        self.bf_box.insert(tk.END, text + "\n")
        self.bf_box.see(tk.END)
        self.bf_box.configure(state=tk.DISABLED)

    def _set_backfill_status(self, s: str) -> None:
        self.bf_status_var.set(s)

    def _run_backfill(self) -> None:
        if self._backfill_running:
            return
        from datetime import datetime as _dt
        start = self.bf_start_var.get().strip()
        end = self.bf_end_var.get().strip() or start
        try:
            _dt.strptime(start, "%Y-%m-%d")
            _dt.strptime(end, "%Y-%m-%d")
        except ValueError:
            self._set_backfill_status("日期格式错误"); return
        if start > end:
            self._set_backfill_status("开始>结束"); return
        sources = []
        if self.bf_src_hl.get(): sources.append("会话记录")
        if self.bf_src_gd.get(): sources.append("工单明细")
        if not sources:
            self._set_backfill_status("请至少勾一个源"); return
        today = _dt.now().strftime("%Y-%m-%d")
        warn = ("\n\n⚠ 含今天；若采集器在跑，今天的快照会与采集器 5 分钟快照混合(口径一致)，"
                "建议先停采集器再补今天。") if start <= today <= end else ""
        self._set_backfill_status("运行中...")
        self._set_backfill_text("正在补全，请稍候..." + warn)
        self.btn_backfill.configure(state=tk.DISABLED)
        self._backfill_running = True

        def worker():
            try:
                from collector import backfill
                days = backfill.iter_days(start, end)
                data_dir = self.cfg["storage"]["dir"]
                parts = []
                for src in sources:
                    res = backfill.backfill_source(src, self.cfg, days, data_dir,
                                                   overwrite=True, progress_cb=self._on_backfill_progress)
                    parts.append(f"{src}: 成功 {res['成功']} 失败 {res['失败']}"
                                 + (f"({','.join(res['失败日期'])})" if res['失败日期'] else ""))
                self.root.after(0, self._on_backfill_done, "\n".join(parts), None)
            except Exception as exc:
                log.exception("手动补全失败")
                self.root.after(0, self._on_backfill_done, "", exc)

        threading.Thread(target=worker, daemon=True, name="backfill").start()

    def _on_backfill_progress(self, text: str) -> None:
        self.root.after(0, self._append_backfill_text, text)

    def _on_backfill_done(self, summary: str, err: Exception | None) -> None:
        self._backfill_running = False
        self.btn_backfill.configure(state=tk.NORMAL)
        if err is not None:
            self._set_backfill_status("失败")
            self._append_backfill_text(f"\n补全失败: {err}\n详见 logs/manager.log")
        else:
            self._set_backfill_status("完成")
            self._append_backfill_text("\n=== 汇总 ===\n" + summary)

    # ---- 重启控制台(拉新进程、不停子进程,新窗口自动接管)----
    def _restart_manager(self) -> None:
        if not messagebox.askyesno("重启控制台",
                                   "将重启 AutoWFM 管理器。\n采集器/看板会继续运行,由新窗口自动接管。是否继续?"):
            return
        try:
            subprocess.Popen([PYTHON, "manager.py"], cwd=str(ROOT),
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        except Exception as exc:
            log.exception("重启控制台失败")
            messagebox.showerror("重启控制台", f"启动新管理器失败:\n{exc}")
            return
        if self.tray_icon is not None:
            try: self.tray_icon.stop()
            except Exception: pass
            self.tray_icon = None
        self.root.destroy()


def _activation_gate() -> bool:
    """秘钥校验闸口:返回 True 放行,False 需退出。

    系统日期 < 授权起始日(2026-10-01)直接放行;到期后必须输入有效秘钥。
    校验失败则提示并返回 False,由 main() 退出,不启动任何功能。
    """
    from collector import license as _lic
    if _lic.check_license():
        return True
    # 到期,需输入秘钥。
    root = tk.Tk()
    root.withdraw()
    expiry = _lic.ui_expiry_date()
    for _attempt in range(3):
        key = simpledialog.askstring(
            "AutoWFM 需要激活",
            f"当前版本自 {expiry} 起需要激活秘钥。\n请输入秘钥后继续:",
            parent=root,
        )
        if key is None:  # 用户取消
            break
        if _lic.is_activated(key):
            root.destroy()
            log.info("秘钥激活成功")
            return True
        messagebox.showerror("激活失败", "秘钥无效,请重新输入。")
    root.destroy()
    messagebox.showerror("未激活", f"未提供有效秘钥,程序无法使用。\n授权起始日: {expiry}")
    return False


def main() -> None:
    if not _activation_gate():
        return
    try:
        cfg = load_cfg(CONFIG_PATH)
    except Exception as exc:
        root = tk.Tk()
        messagebox.showerror("启动失败", f"无法读取 config.yaml:\n{exc}")
        return
    # pythonw 无 stderr，Tkinter 默认 report_callback_exception 会静默吞掉 UI 异常 -> 记到 manager.log
    def _log_tk_exception(self, exc, val, tb):
        log.error("未捕获的 UI 异常", exc_info=(exc, val, tb))
    tk.Tk.report_callback_exception = _log_tk_exception
    root = tk.Tk()
    ManagerUI(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
