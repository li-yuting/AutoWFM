"""企微 webhook 推送(定时 markdown 报表 + 排队告警)+ Playwright 截图。"""
import base64, csv, datetime, hashlib, logging, sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from collector._utils import in_window

log = logging.getLogger("autowfm")


def _n(v):
    """None/缺值 -> 0,否则原值。"""
    return v or 0


def _pct(num, den):
    """num/den 百分比,2 位小数;den 为 0/None/假值 返回 '0.00%'。"""
    return f"{num / den * 100:.2f}%" if den else "0.00%"


def latest_snapshot(data_dir, source, date_str):
    """当天该源 时间 最大的一行(dict);无表/无数据返回 None。"""
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return None
    con = sqlite3.connect(str(path))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
        if not cols:
            return None
        row = con.execute(
            f'SELECT {",".join(chr(34) + c + chr(34) for c in cols)} FROM t '
            f'WHERE "时间" LIKE ? ORDER BY "时间" DESC LIMIT 1',
            (f"{date_str}%",),
        ).fetchone()
    finally:
        con.close()
    return dict(zip(cols, row)) if row else None


def latest_two(data_dir, source, date_str):
    """当天该源 时间 倒序前两条(dict);无表/无数据返回 []。供转人工量停滞检测对比。"""
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
        if not cols:
            return []
        rows = con.execute(
            f'SELECT {",".join(chr(34) + c + chr(34) for c in cols)} FROM t '
            f'WHERE "时间" LIKE ? ORDER BY "时间" DESC LIMIT 2',
            (f"{date_str}%",),
        ).fetchall()
    finally:
        con.close()
    return [dict(zip(cols, row)) for row in rows]


def forecast_at(data_dir, line, now_str):
    """预估流入量.csv 中 线路==line 且 时间==now_str 的 累计预估量;未命中返回 0。"""
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("线路") == line and row.get("时间") == now_str:
                try:
                    return int(row["累计预估量"])
                except (ValueError, KeyError):
                    return 0
    return 0


def _render_firstline(now_str, hot, hot_seat, ol, f_hot, f_ol):
    if not hot:
        return ""
    hs = hot_seat or {}
    zrg = _n(hot.get("转人工量")); jtl = _n(hot.get("接通量")); pd = _n(hot.get("排队量"))
    s = (f"# 当前时间: {now_str}    \n"
         f"统计监控`热线`:     \n"
         f">预测量: {f_hot:.0f}, 转人工量：{zrg:.0f}    \n"
         f">流入率：{_pct(zrg, f_hot)}    \n"
         f">接通量：{jtl:.0f}, 接通率：{_pct(jtl, zrg)}    \n"
         f">排队量：{pd:.0f}    \n"
         f">签入人数：{_n(hs.get('签入'))}     \n"
         f">通话人数：{_n(hs.get('通话'))}, 话后人数：{_n(hs.get('话后'))}    \n"
         f">空闲人数：{_n(hs.get('空闲'))}, 置忙人数：{_n(hs.get('置忙'))}    \n\n")
    if ol:
        ozrg = _n(ol.get("转人工量")); osb = _n(ol.get("转人工失败")); ocg = ozrg - osb
        s += (f"统计监控`在线`:     \n"
              f">预测量: {f_ol:.0f}, 转人工量：{ozrg:.0f}    \n"
              f">流入率：{_pct(ozrg, f_ol)}    \n"
              f">接通量：{ocg:.0f}, 接通率：{_pct(ocg, ozrg)}    \n"
              f">排队量：{_n(ol.get('排队')):.0f}    \n"
              f">正在咨询人数：{_n(ol.get('咨询')):.0f}    \n"
              f">在线人数：{_n(ol.get('在线')):.0f}, 回访人数：{_n(ol.get('回访')):.0f}    \n"
              f">话后人数：{_n(ol.get('话后')):.0f}, 小休人数：{_n(ol.get('小休')):.0f}    \n"
              f">示忙人数：{_n(ol.get('示忙')):.0f}, 就餐人数：{_n(ol.get('就餐')):.0f}    \n")
    return s


def _render_secondline(now_str, groups, z12378, z12378_seat):
    s = f"# 当前时间：{now_str}    \n"
    for label, transfer, ticket, seat in groups:
        st = seat or {}
        s += (f"签入情况`{label}`:     \n"
              f">转接量：{transfer}, 工单量：{ticket}    \n"
              f">签入人数：{_n(st.get('签入'))}     \n"
              f">通话人数：{_n(st.get('通话'))}, 话后人数：{_n(st.get('话后'))}    \n"
              f">空闲人数：{_n(st.get('空闲'))}, 置忙人数：{_n(st.get('置忙'))}    \n"
              f">离席人数：{_n(st.get('离席'))}, 振铃人数：{_n(st.get('振铃'))}    \n\n")
    if z12378:
        zs = z12378_seat or {}
        zzrg = _n(z12378.get("转人工量")); zjtl = _n(z12378.get("接通量"))
        s += (f"统计监控`12378`:     \n"
              f">转人工量：{zzrg:.0f}    \n"
              f">接通量：{zjtl:.0f}, 接通率：{_pct(zjtl, zzrg)}    \n"
              f">排队量：{_n(z12378.get('排队量')):.0f}    \n"
              f">签入人数：{_n(zs.get('签入'))}     \n"
              f">通话人数：{_n(zs.get('通话'))}, 话后人数：{_n(zs.get('话后'))}    \n"
              f">空闲人数：{_n(zs.get('空闲'))}, 置忙人数：{_n(zs.get('置忙'))}    \n")
    return s


def build_firstline_msg(data_dir, now_str, date_str):
    hot = latest_snapshot(data_dir, "热线", date_str)
    if not hot:
        return ""
    hot_seat = latest_snapshot(data_dir, "热线明细", date_str)
    ol = latest_snapshot(data_dir, "在线", date_str)
    f_hot = forecast_at(data_dir, "热线", now_str)
    f_ol = forecast_at(data_dir, "在线", now_str)
    return _render_firstline(now_str, hot, hot_seat, ol, f_hot, f_ol)


def build_secondline_msg(data_dir, now_str, date_str):
    sess = latest_snapshot(data_dir, "会话记录", date_str)
    tickets = latest_snapshot(data_dir, "工单明细", date_str)
    groups = []
    for label, seat_src, sess_cols, ticket_col in [
        ("常规转接组", "常规", ("转接一组", "转接二组", "回访组一组"), "回访组一组"),
        ("贷后转接组", "贷后", ("贷后转接组", "贷后回访组"), "贷后回访组"),
    ]:
        seat = latest_snapshot(data_dir, seat_src, date_str)
        if not seat:
            continue
        transfer = sum(_n(sess.get(c)) for c in sess_cols) if sess else 0
        ticket = _n(tickets.get(ticket_col)) if tickets else 0
        groups.append((label, transfer, ticket, seat))
    z12378 = latest_snapshot(data_dir, "12378", date_str)
    z12378_seat = latest_snapshot(data_dir, "12378明细", date_str) if z12378 else None
    if not groups and not z12378:
        return ""
    return _render_secondline(now_str, groups, z12378, z12378_seat)


def _webhook(key, payload):
    kind = payload.get("msgtype", "?")
    try:
        resp = requests.post(f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}",
                             json=payload, timeout=30)
        if resp.status_code != 200:
            log.warning(f"[推送] {kind} -> {key} 失败 HTTP {resp.status_code}: {resp.text}")
            return f"webhook 失败: HTTP {resp.status_code}"
        r = resp.json()
        ec = r.get("errcode")
        if ec != 0:
            log.warning(f"[推送] {kind} -> {key} 失败 errcode={ec}: {r.get('errmsg', '')}")
            return f"webhook 失败: errcode={ec}"
        log.info(f"[推送] {kind} -> {key} 成功")
        return resp.text
    except Exception as e:
        log.warning(f"[推送] {kind} -> {key} 异常: {e}")
        return f"webhook 失败: {e}"


def _send_md(text, key):
    return _webhook(key, {"msgtype": "markdown", "markdown": {"content": text}})


def _send_img(path, key):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        return f"webhook 失败: {e}"
    return _webhook(key, {"msgtype": "image",
                          "image": {"base64": base64.b64encode(data).decode(),
                                    "md5": hashlib.md5(data).hexdigest()}})


def _send_text(key, mobiles, msg):
    return _webhook(key, {"msgtype": "text",
                          "text": {"content": msg, "mentioned_mobile_list": mobiles}})


# 转人工量停滞告警去重:已提醒的源记录于此;转人工量恢复变化后移除,避免连续周期刷屏
_STALL_ALERTED = set()


def _check_stall_alert(now_str, date_str, data_dir, alert, rcpt, wh):
    """热线/在线:最新两条快照「转人工量」完全一致 -> 艾特负责人(独立告警,与排队阈值无关)。

    去重语义:每个源在「由变化转为停滞」的首次提醒一次;值恢复变化后重置,
    再次停滞再提醒。仅一条记录(无从对比)视为未停滞。"""
    if not alert.get("stall_check", True):
        return
    for src, key in (("热线", "hotline"), ("在线", "online")):
        rows = latest_two(data_dir, src, date_str)
        if len(rows) < 2:
            _STALL_ALERTED.discard(src)
            continue
        q = rows[0].get("转人工量")
        if q is not None and q == rows[1].get("转人工量"):
            if src in _STALL_ALERTED:
                continue
            _STALL_ALERTED.add(src)
            msg = (f"⚠️ 转人工量无变化 {now_str}\n"
                   f"{src} 转人工量：{q}（与上一周期采集值一致），数据疑似未更新，请关注")
            _send_text(wh["main_key"], rcpt[key], msg)
        else:
            _STALL_ALERTED.discard(src)


def _looks_blank(path, bands=6):
    """截图是否疑似空白:灰度后按水平条带统计灰度级数,任一条带 <=4 级判空白。

    未渲染区域呈纯色(1 级);正常条带必含文字/图表抗锯齿灰阶(远超 4 级)。
    Pillow 任何异常 -> False(fail-open:检测不了就放行,宁漏勿误杀)。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("L")
        w, h = im.size
        if not w or not h:
            return False
        band_h = max(1, h // bands)
        for i in range(bands):
            top, bottom = i * band_h, min((i + 1) * band_h, h)
            if top >= bottom:
                continue
            colors = im.crop((0, top, w, bottom)).getcolors(maxcolors=4096)
            if colors is not None and len(colors) <= 4:
                return True
        return False
    except Exception as e:
        log.warning(f"[截图] 空白检测异常(fail-open): {e}")
        return False


# 截图就绪等待(秒)与超时后兜底固定等待(毫秒,即旧行为的 5 秒)
READY_TIMEOUT = 20
FALLBACK_WAIT_MS = 5000


def _wait_ready(pg, timeout=READY_TIMEOUT):
    """等待看板就绪信号 body[data-ready]=1(全部图表动画完成);超时退化为固定等待并返回 False。

    只捕获 Playwright TimeoutError(旧版看板无信号属预期降级);其他异常向上抛。"""
    try:
        from playwright.sync_api import TimeoutError as PwTimeout
        pg.wait_for_function("document.body && document.body.dataset.ready === '1'",
                             timeout=timeout * 1000)
        return True
    except PwTimeout:
        log.warning(f"[截图] 就绪信号超时({timeout}s),退化为固定等待 {FALLBACK_WAIT_MS}ms")
        pg.wait_for_timeout(FALLBACK_WAIT_MS)
        return False


def _warm_raster(pg):
    """滚动到底再回顶,预热长页面光栅化(防全页截图出现未渲染条带);失败仅记日志。"""
    try:
        pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        pg.wait_for_timeout(300)
        pg.evaluate("window.scrollTo(0, 0)")
        pg.wait_for_timeout(200)
    except Exception as e:
        log.warning(f"[截图] 光栅化预热失败(忽略): {e}")


def take_screenshot(url, dash_token=None):
    """Playwright 截图 -> data/screenshot.png;失败返回 None。
    dash_token 非空时带 Authorization: Bearer header(看板启用认证后必需)。"""
    try:
        from playwright.sync_api import sync_playwright
        Path("data").mkdir(exist_ok=True)
        path = str(Path("data") / "screenshot.png")
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                pg = b.new_page(viewport={"width": 1920, "height": 1080})
                if dash_token:
                    pg.set_extra_http_headers({"Authorization": f"Bearer {dash_token}"})
                pg.goto(url, wait_until="networkidle", timeout=30000)
                pg.wait_for_timeout(5000)   # 等 Chart.js 渲染(本看板无 updateTime 标记)
                pg.screenshot(path=path, full_page=True)
                log.info(f"截图已保存: {path}")
                return path
            finally:
                b.close()
    except Exception as e:
        log.error(f"截图失败: {e}")
        return None


def check_alerts(cfg, now=None):
    """逐源判排队阈值,超阈值发 text 告警。热线/在线 走全局窗口(由调用方 ws_job 保证);
    12378 走自己的 schedule(防周末 18:00 后陈旧误报)。"""
    try:
        tz = ZoneInfo(cfg["schedule"]["timezone"])
        now = now or datetime.datetime.now(tz)
        now_str = now.strftime("%Y-%m-%d %H:%M")
        date_str = now.strftime("%Y-%m-%d")
        data_dir = cfg["storage"]["dir"]
        alert = cfg["notify"]["alert"]
        wh = cfg["notify"]["webhook"]
        rcpt = alert["recipients"]

        hot = latest_snapshot(data_dir, "热线", date_str)
        if hot:
            q = _n(hot.get("排队量")); idle = _n((latest_snapshot(data_dir, "热线明细", date_str) or {}).get("空闲"))
            if q >= alert["hotline_queue"] and idle < q:
                msg = f"⚠️ 排队告警 {now_str}\n热线排队：{q} 人（阈值 {alert['hotline_queue']}，空闲 {idle}）"
                _send_text(wh["main_key"], rcpt["hotline"], msg)

        ol = latest_snapshot(data_dir, "在线", date_str)
        if ol:
            q = _n(ol.get("排队"))
            if q >= alert["online_queue"]:
                msg = f"⚠️ 排队告警 {now_str}\n在线排队：{q} 人（阈值 {alert['online_queue']}）"
                _send_text(wh["main_key"], rcpt["online"], msg)

        sub12378 = next((s for s in cfg["subs"] if s["name"] == "12378"), None)
        if sub12378 and in_window(cfg, sub12378, now):
            z = latest_snapshot(data_dir, "12378", date_str)
            if z:
                q = _n(z.get("排队量")); idle = _n((latest_snapshot(data_dir, "12378明细", date_str) or {}).get("空闲"))
                if q >= alert["queue_12378"] and idle < q:
                    msg = f"⚠️ 12378排队告警 {now_str}\n12378排队：{q} 人（阈值 {alert['queue_12378']}，空闲 {idle}）"
                    _send_text(wh["secondary_key"], rcpt["12378"], msg)

        _check_stall_alert(now_str, date_str, data_dir, alert, rcpt, wh)
    except Exception:
        log.exception("check_alerts 异常")


def send_report(cfg, now=None):
    """定时推送入口:两条 markdown -> 各自 webhook -> 一张截图发两路。窗口由 push_job 挡。"""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.datetime.now(tz)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    date_str = now.strftime("%Y-%m-%d")
    data_dir = cfg["storage"]["dir"]
    wh = cfg["notify"]["webhook"]
    try:
        msg1 = build_firstline_msg(data_dir, now_str, date_str)
        if msg1:
            _send_md(msg1, wh["main_key"])
        msg2 = build_secondline_msg(data_dir, now_str, date_str)
        if msg2:
            _send_md(msg2, wh["secondary_key"])
        ss = take_screenshot(cfg["notify"]["screenshot_url"],
                             cfg.get("notify", {}).get("dash_token") or None)
        if ss:
            _send_img(ss, wh["main_key"])
            _send_img(ss, wh["secondary_key"])
    except Exception:
        log.exception("send_report 异常")
