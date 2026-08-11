# -*- coding: utf-8 -*-
"""看板数据层：只读 data/*.db + 预估流入量.csv，做小时/按日聚合。

底层 SQLite 访问已委托给 collector.repository.SQLiteReadOnlyRepository(Repository 模式)。
聚合逻辑(build_day/build_month/方案D 增量等)不依赖存储实现,只调本模块的 _rows_in/_cols 等。
"""
import csv
import sqlite3
from datetime import date as _date
from pathlib import Path

from collector.repository import SQLiteReadOnlyRepository

def _repo_for(data_dir):
    """为指定 data_dir 创建只读 Repository(每次调用新建,无状态)。"""
    return SQLiteReadOnlyRepository(data_dir)

def _connect(data_dir, source):
    """向后兼容:直接返回 sqlite3 连接(仅 latest_data_date 等少数处用)。"""
    return sqlite3.connect(str(Path(data_dir) / f"{source}.db"))

def _cols(con):
    return [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]

def _rows_in(data_dir, source, prefix):
    """某天(prefix=YYYY-MM-DD)或某月(prefix=YYYY-MM)该源所有行(升序)+列名。无表/无数据返回 ([], [])。
    委托给 SQLiteReadOnlyRepository.rows_in。"""
    return _repo_for(data_dir).rows_in(source, prefix)


def _rows_in_day(data_dir, source, date_str):
    return _rows_in(data_dir, source, date_str)


def _rows_in_month(data_dir, source, ym):
    return _rows_in(data_dir, source, ym)

def _hourly_agg(data_dir, source, date_str, keep="last"):
    """{小时: {列: 值}}，keep="last"取每小时最大时间戳，keep="first"取最小。"""
    rows, cols = _rows_in_day(data_dir, source, date_str)
    out = {}
    for r in rows:
        row = dict(zip(cols, r))
        hh = int(row["时间"][11:13])
        if keep == "first":
            out.setdefault(hh, row)
        else:
            out[hh] = row  # 升序覆盖 -> 保留最大
    return out


def hourly_latest(data_dir, source, date_str):
    return _hourly_agg(data_dir, source, date_str, "last")


def hourly_first(data_dir, source, date_str):
    return _hourly_agg(data_dir, source, date_str, "first")

def hourly_avg(data_dir, source, date_str):
    """{小时: {列: 该小时所有快照均值}}，瞬时量(签入/空闲/在线)用。无表/无数据返回 {}。"""
    rows, cols = _rows_in_day(data_dir, source, date_str)
    buckets = {}
    for r in rows:
        row = dict(zip(cols, r))
        buckets.setdefault(int(row["时间"][11:13]), []).append(row)
    out = {}
    for hh, rs in buckets.items():
        n = len(rs)
        out[hh] = {c: round(sum((r[c] or 0) for r in rs) / n) for c in cols if c != "时间"}
    return out

def _rows_in_month(data_dir, source, ym):
    """委托给 SQLiteReadOnlyRepository.rows_in(与 _rows_in 同实现,prefix=YYYY-MM)。"""
    return _repo_for(data_dir).rows_in(source, ym)

def daily_latest(data_dir, source, ym):
    rows, cols = _rows_in_month(data_dir, source, ym)
    out = {}
    for r in rows:
        row = dict(zip(cols, r))
        day = int(row["时间"][8:10])
        out[day] = row  # 升序，保留最大
    return out

def daily_avg(data_dir, source, ym):
    rows, cols = _rows_in_month(data_dir, source, ym)
    buckets = {}
    for r in rows:
        row = dict(zip(cols, r))
        day = int(row["时间"][8:10])
        buckets.setdefault(day, []).append(row)
    out = {}
    for day, rs in buckets.items():
        n = len(rs)
        out[day] = {c: round(sum((r[c] or 0) for r in rs) / n) for c in cols if c != "时间"}
    return out

def latest_data_date(data_dir="data"):
    """热线/在线 db 中最新的日期(YYYY-MM-DD)。委托给 SQLiteReadOnlyRepository.latest_date。
    这两组窗口 9:00 起，故当天 9 点前取到的是昨天(最近有数据日)，9 点后取今天。
    任一库无数据则回落到今天。"""
    return _repo_for(data_dir).latest_date()

def _forecast_rows(data_dir, line, date_str):
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["线路"] == line and r["时间"].startswith(date_str):
                out.append((r["时间"], int(r["时段预估量"]), int(r["累计预估量"])))
    out.sort(key=lambda x: x[0])
    return out

def load_forecast(data_dir, line, date_str):
    """{小时: 累计预估量}，每小时取时间戳最大一行。"""
    out = {}
    for ts, _inc, cum in _forecast_rows(data_dir, line, date_str):
        out[int(ts[11:13])] = cum  # 升序覆盖 -> 最大
    return out

def forecast_increment(data_dir, line, date_str):
    """{小时: 该小时时段预估量之和}。
    口径：H 点 = H:15 + H:30 + H:45 + (H+1):00 四行时段预估量之和。
    CSV 时间戳为时段结束点(09:15 行=[09:00,09:15))，故 H:00 行计入 H-1 点，
    使 H 点覆盖 [H:00, H+1:00) 整小时，与转人工量等整小时增量对齐。"""
    out = {}
    for ts, inc, _cum in _forecast_rows(data_dir, line, date_str):
        hh = int(ts[11:13])
        if int(ts[14:16]) == 0:   # H:00 行 -> 计入 H-1 点
            hh -= 1
        if hh < 0:
            continue
        out[hh] = out.get(hh, 0) + inc
    return out

def forecast_cum_up_to(data_dir, line, date_str, cutoff_ts):
    """累计预估量：取 时间 <= cutoff_ts 的最后一行(15分钟粒度)。
    cutoff_ts 形如 '2026-07-29 10:20'(含分钟)；无 CSV、早于首行或 cutoff 为空返回 0。
    卡片时段预测量用它按实际数据时间取已到达的 :00/:15/:30/:45 累计，
    而非当前小时 :45 的未来值。"""
    if not cutoff_ts:
        return 0
    cum = 0
    for ts, _inc, c in _forecast_rows(data_dir, line, date_str):  # 升序
        if ts <= cutoff_ts:
            cum = c
        else:
            break
    return cum

def _forecast_12378_agg(data_dir, date_str, keep="last"):
    """{小时: 累计转人工量}，取 date_str 前 7 天当天 12378.db 每小时数据。"""
    from datetime import date, timedelta
    y, m, dd = (int(x) for x in date_str.split("-"))
    prev = (date(y, m, dd) - timedelta(days=7)).strftime("%Y-%m-%d")
    snap = _hourly_agg(data_dir, "12378", prev, keep)
    return {hh: row["转人工量"] for hh, row in snap.items()}


def forecast_12378(data_dir, date_str):
    return _forecast_12378_agg(data_dir, date_str, "last")


def forecast_12378_first(data_dir, date_str):
    return _forecast_12378_agg(data_dir, date_str, "first")

def _val(row, col):
    return row[col] if row else None

def _hours_for(name, date_str):
    """12378 工作日 8-20、周末 9-17；其余 9-20。返回小时列表。"""
    y, m, dd = (int(x) for x in date_str.split("-"))
    if name == "12378" and _date(y, m, dd).weekday() >= 5:
        return list(range(9, 18))
    if name == "12378":
        return list(range(8, 21))
    return list(range(9, 21))

def _inc_d(first, latest, hours):
    """每小时增量(方案D)：H点 = end[H] − start[H]。
    start[H]=first[H](H小时首条累计)；end[H]=first[H+1](下一小时首条,即(H+1):00快照)若存在,
    否则 latest[H](H小时最新,当前小时实时进度)。first[H]缺失->None。
    计数器隔夜归零；首小时少算开头(首采非整点,如热线9:05)；缺口自然为None。
    已完成小时精确覆盖[H:00,(H+1):00)；当前小时显示整点至今的实时增量(整点瞬间为0)。"""
    out = {}
    for h in hours:
        f = first.get(h)
        if f is None:
            out[h] = None
            continue
        e = first.get(h + 1)          # 下一小时首条(可能超出hours,如20->21)
        if e is None:
            e = latest.get(h)          # 当前小时最新 -> 实时进度
        out[h] = (e - f) if e is not None else None
    return out

def _inc_col_d(first, latest, col, hours):
    """从 {小时:row} 的 first/latest 序列取列 col，再求方案D增量。
    first/latest 须为完整 dict(不限 hours)，因 _inc_d 需查 first[H+1]。"""
    f_vals = {h: row.get(col) for h, row in first.items()}
    l_vals = {h: row.get(col) for h, row in latest.items()}
    return _inc_d(f_vals, l_vals, hours)

def build_day(date_str, data_dir="data"):
    # --- 接听各组 hourly 快照 ---
    rx = hourly_latest(data_dir, "热线", date_str)
    rx_f = hourly_first(data_dir, "热线", date_str)
    rx_seat = hourly_avg(data_dir, "热线明细", date_str)
    im = hourly_latest(data_dir, "在线", date_str)
    im_f = hourly_first(data_dir, "在线", date_str)
    im_a = hourly_avg(data_dir, "在线", date_str)
    z = hourly_latest(data_dir, "12378", date_str)
    z_f = hourly_first(data_dir, "12378", date_str)
    z_seat = hourly_avg(data_dir, "12378明细", date_str)
    gd = hourly_latest(data_dir, "工单明细", date_str)
    gd_f = hourly_first(data_dir, "工单明细", date_str)
    hl = hourly_latest(data_dir, "会话记录", date_str)
    hl_f = hourly_first(data_dir, "会话记录", date_str)

    fc_rx = load_forecast(data_dir, "热线", date_str)
    fc_im = load_forecast(data_dir, "在线", date_str)
    fc_z = forecast_12378(data_dir, date_str)
    fc_z_first = forecast_12378_first(data_dir, date_str)

    h_other = list(range(9, 21))
    h_12378 = _hours_for("12378", date_str)

    # 量类指标=每小时增量(方案D：end[H]-start[H]，start=H首条，end=(H+1)首条或H最新；当前小时实时)
    inc_rx_zrg = _inc_col_d(rx_f, rx, "转人工量", h_other)
    inc_rx_succ = _inc_col_d(rx_f, rx, "接通量", h_other)
    inc_im_zrg = _inc_col_d(im_f, im, "转人工量", h_other)
    inc_im_fail = _inc_col_d(im_f, im, "转人工失败", h_other)
    inc_im_succ = {h: (inc_im_zrg[h] - inc_im_fail[h]) if (inc_im_zrg[h] is not None and inc_im_fail[h] is not None) else None for h in h_other}
    inc_z_zrg = _inc_col_d(z_f, z, "转人工量", h_12378)
    inc_z_succ = _inc_col_d(z_f, z, "接通量", h_12378)
    inc_z_hf = _inc_col_d(gd_f, gd, "12378回访组", h_12378)
    inc_c2_gda = _inc_col_d(gd_f, gd, "转接一组", h_other)
    inc_c2_gdb = _inc_col_d(gd_f, gd, "转接二组", h_other)
    inc_c2_gdc = _inc_col_d(gd_f, gd, "回访组一组", h_other)
    inc_c2_gd = {h: ((inc_c2_gda[h] or 0) + (inc_c2_gdb[h] or 0) + (inc_c2_gdc[h] or 0)) if inc_c2_gdc[h] is not None else None for h in h_other}
    inc_c2_hla = _inc_col_d(hl_f, hl, "转接一组", h_other)
    inc_c2_hlb = _inc_col_d(hl_f, hl, "转接二组", h_other)
    inc_c2_hlc = _inc_col_d(hl_f, hl, "回访组一组", h_other)
    inc_c2_hl = {h: ((inc_c2_hla[h] or 0) + (inc_c2_hlb[h] or 0) + (inc_c2_hlc[h] or 0)) if inc_c2_hla[h] is not None else None for h in h_other}
    inc_dh_gda = _inc_col_d(gd_f, gd, "贷后转接组", h_other)
    inc_dh_gdb = _inc_col_d(gd_f, gd, "贷后回访组", h_other)
    inc_dh_gd = {h: ((inc_dh_gda[h] or 0) + (inc_dh_gdb[h] or 0)) if inc_dh_gdb[h] is not None else None for h in h_other}
    inc_dh_hla = _inc_col_d(hl_f, hl, "贷后转接组", h_other)
    inc_dh_hlb = _inc_col_d(hl_f, hl, "贷后回访组", h_other)
    inc_dh_hl = {h: ((inc_dh_hla[h] or 0) + (inc_dh_hlb[h] or 0)) if inc_dh_hla[h] is not None else None for h in h_other}
    inc_ks_gd = _inc_col_d(gd_f, gd, "二线客诉处理组", h_other)
    inc_cg2_gd = _inc_col_d(gd_f, gd, "常规工单处理组", h_other)
    # 预测量=每时段增量：CSV 用 forecast_increment；12378 用 7 天前累计的方案D增量
    fc_rx_inc = forecast_increment(data_dir, "热线", date_str)
    fc_im_inc = forecast_increment(data_dir, "在线", date_str)
    fc_z_inc = _inc_d(fc_z_first, fc_z, h_12378)

    inbound = {
        "热线": {
            "hours": h_other,
            "预测量": {h: fc_rx_inc.get(h, 0) for h in h_other},
            "转人工量": inc_rx_zrg,
            "转人工成功量": inc_rx_succ,
            "签入": {h: _val(rx_seat.get(h), "签入") for h in h_other},
            "空闲": {h: _val(rx_seat.get(h), "空闲") for h in h_other},
        },
        "在线": {
            "hours": h_other,
            "预测量": {h: fc_im_inc.get(h, 0) for h in h_other},
            "转人工量": inc_im_zrg,
            "转人工成功量": inc_im_succ,
            "在线": {h: _val(im_a.get(h), "在线") for h in h_other},
        },
        "12378": {
            "hours": h_12378,
            "预测量": fc_z_inc,
            "转人工量": inc_z_zrg,
            "转人工成功量": inc_z_succ,
            "签入": {h: _val(z_seat.get(h), "签入") for h in h_12378},
            "空闲": {h: _val(z_seat.get(h), "空闲") for h in h_12378},
            "工单量": inc_z_hf,
        },
    }

    # --- 外呼各组 ---
    cg = hourly_latest(data_dir, "常规", date_str)
    cg_a = hourly_avg(data_dir, "常规", date_str)
    dh = hourly_latest(data_dir, "贷后", date_str)
    dh_a = hourly_avg(data_dir, "贷后", date_str)
    outbound = {
        "常规二线": {
            "hours": h_other,
            "工单量": inc_c2_gd,
            "转接量": inc_c2_hl,
            "签入": {h: _val(cg_a.get(h), "签入") for h in h_other},
            "空闲": {h: _val(cg_a.get(h), "空闲") for h in h_other},
        },
        "贷后二线": {
            "hours": h_other,
            "工单量": inc_dh_gd,
            "转接量": inc_dh_hl,
            "签入": {h: _val(dh_a.get(h), "签入") for h in h_other},
            "空闲": {h: _val(dh_a.get(h), "空闲") for h in h_other},
        },
        "二线客诉": {"hours": h_other, "工单量": inc_ks_gd},
        "常规工单": {"hours": h_other, "工单量": inc_cg2_gd},
    }

    # --- current_hour：接听三组实际数据的最大小时 ---
    data_hours = sorted(set(list(rx) + list(im) + list(z)))
    cur = data_hours[-1] if data_hours else None

    def _in(full, cum, zrg, succ):
        return {
            "预测量": full, "时段预测量": cum,
            "流入率": _rate(zrg, cum),
            "转人工量": zrg, "转人工成功量": succ,
            "接通率": _rate(succ, zrg),
        }

    card_in = None; card_out = None
    if cur is not None:
        # 预测量=全天总量(max)；时段预测量=截止当前实际数据时间的累计(15分钟粒度，
        # 取该源最新WS时间戳之前最近的 :00/:15/:30/:45 行累计，而非当前小时 :45 未来值)
        rx_full = max(fc_rx.values()) if fc_rx else 0
        im_full = max(fc_im.values()) if fc_im else 0
        z_full = max(fc_z.values()) if fc_z else 0
        rx_ts = rx[cur]["时间"] if rx.get(cur) else None
        im_ts = im[cur]["时间"] if im.get(cur) else None
        rx_cum = forecast_cum_up_to(data_dir, "热线", date_str, rx_ts)
        im_cum = forecast_cum_up_to(data_dir, "在线", date_str, im_ts)
        # 12378 时间周期与其他组不同，按 12378 自身最新小时取值
        z_cur = max(z) if z else None
        z_cum = (fc_z.get(z_cur) or 0) if z_cur is not None else 0
        rx_zrg = rx[cur].get("转人工量") if rx.get(cur) else 0
        rx_succ = rx[cur].get("接通量") if rx.get(cur) else 0
        im_zrg = im[cur].get("转人工量") if im.get(cur) else 0
        im_succ = ((im[cur].get("转人工量") or 0) - (im[cur].get("转人工失败") or 0)) if im.get(cur) else 0
        z_zrg = z[z_cur].get("转人工量") if z_cur is not None else 0
        z_succ = z[z_cur].get("接通量") if z_cur is not None else 0
        card_in = {
            "total": _in(rx_full + im_full + z_full, rx_cum + im_cum + z_cum,
                         rx_zrg + im_zrg + z_zrg, rx_succ + im_succ + z_succ),
            "groups": {
                "热线": _in(rx_full, rx_cum, rx_zrg, rx_succ),
                "在线": _in(im_full, im_cum, im_zrg, im_succ),
                "12378": _in(z_full, z_cum, z_zrg, z_succ),
            },
        }
        # 外呼各组
        # 工单明细/会话记录是明细导出库，可能滞后 WS 一个 tick，按各自最新小时取值(同 12378 的 z_cur)
        gd_cur = max(gd) if gd else None
        hl_cur = max(hl) if hl else None
        def _gd(col):
            return gd.get(gd_cur).get(col) if gd.get(gd_cur) else 0
        def _hl2(c1, c2, c3=None):
            if not hl.get(hl_cur):
                return 0
            total = (hl.get(hl_cur).get(c1) or 0) + (hl.get(hl_cur).get(c2) or 0)
            if c3:
                total += (hl.get(hl_cur).get(c3) or 0)
            return total
        c2_gd = (_gd("转接一组") or 0) + (_gd("转接二组") or 0) + (_gd("回访组一组") or 0)
        c2_hl = _hl2("转接一组", "转接二组", "回访组一组")
        c2_in = cg.get(cur).get("签入") if cg.get(cur) else 0
        c2_free = cg.get(cur).get("空闲") if cg.get(cur) else 0
        dh2_gd = (_gd("贷后转接组") or 0) + (_gd("贷后回访组") or 0)
        dh2_hl = _hl2("贷后转接组", "贷后回访组")
        dh2_in = dh.get(cur).get("签入") if dh.get(cur) else 0
        dh2_free = dh.get(cur).get("空闲") if dh.get(cur) else 0
        ks_gd = _gd("二线客诉处理组") or 0
        cg2_gd = _gd("常规工单处理组") or 0
        card_out = {
            "total": {
                "工单量": c2_gd + dh2_gd + ks_gd + cg2_gd,
                "转接量": c2_hl + dh2_hl,
                "签入": c2_in + dh2_in, "空闲": c2_free + dh2_free,
            },
            "groups": {
                "常规二线": {"工单量": c2_gd, "转接量": c2_hl, "签入": c2_in, "空闲": c2_free},
                "贷后二线": {"工单量": dh2_gd, "转接量": dh2_hl, "签入": dh2_in, "空闲": dh2_free},
                "二线客诉": {"工单量": ks_gd},
                "常规工单": {"工单量": cg2_gd},
            },
        }

    # --- 两张明细表（只展示已采集的小时，未到时段不显示）---
    _src_hours = (set(rx) | set(rx_seat) | set(im) | set(z) | set(z_seat)
                  | set(gd) | set(hl) | set(cg) | set(dh))
    table_hours = [h for h in range(8, 21) if h in _src_hours]
    in_rows, out_rows = _table_rows(inbound, outbound, table_hours, "小时")

    return {
        "date": date_str, "current_hour": cur,
        "inbound": inbound, "outbound": outbound,
        "card": {"inbound": card_in, "outbound": card_out},
        "tables": {"inbound": in_rows, "outbound": out_rows},
        "headers": {"inbound": _table_header(in_rows[0].keys()) if in_rows else {"label": "小时", "groups": []},
                    "outbound": _table_header(out_rows[0].keys()) if out_rows else {"label": "小时", "groups": []}},
    }

def _rate(num, den):
    if num is None or den is None or den == 0:
        return None
    return f"{num / den * 100:.2f}%"

def _table_header(keys):
    """两行表头：首列(小时/日,rowspan2) + 按组 colspan。返回 {label, groups:[{name,values}]}。"""
    keys = list(keys)
    groups = []
    for k in keys[1:]:
        g, _, v = k.partition("_")
        if groups and groups[-1]["name"] == g:
            groups[-1]["values"].append(v)
        else:
            groups.append({"name": g, "values": [v]})
    return {"label": keys[0], "groups": groups}

def _table_rows(inbound, outbound, xs, label):
    """构造两张明细表的行。xs=小时/日列表，label='小时'/'日'。键顺序决定表头列序。"""
    in_rows, out_rows = [], []
    for x in xs:
        in_rows.append({
            label: x,
            "热线_预测量": inbound["热线"]["预测量"].get(x),
            "热线_流入率": _rate(inbound["热线"]["转人工量"].get(x), inbound["热线"]["预测量"].get(x)),
            "热线_转人工量": inbound["热线"]["转人工量"].get(x),
            "热线_转人工成功量": inbound["热线"]["转人工成功量"].get(x),
            "热线_接通率": _rate(inbound["热线"]["转人工成功量"].get(x), inbound["热线"]["转人工量"].get(x)),
            "热线_签入": inbound["热线"]["签入"].get(x), "热线_空闲": inbound["热线"]["空闲"].get(x),
            "在线_预测量": inbound["在线"]["预测量"].get(x),
            "在线_流入率": _rate(inbound["在线"]["转人工量"].get(x), inbound["在线"]["预测量"].get(x)),
            "在线_转人工量": inbound["在线"]["转人工量"].get(x),
            "在线_转人工成功量": inbound["在线"]["转人工成功量"].get(x),
            "在线_接通率": _rate(inbound["在线"]["转人工成功量"].get(x), inbound["在线"]["转人工量"].get(x)),
            "在线_在线": inbound["在线"]["在线"].get(x),
            "12378_工单量": inbound["12378"]["工单量"].get(x),
            "12378_转人工量": inbound["12378"]["转人工量"].get(x),
            "12378_转人工成功量": inbound["12378"]["转人工成功量"].get(x),
            "12378_接通率": _rate(inbound["12378"]["转人工成功量"].get(x), inbound["12378"]["转人工量"].get(x)),
            "12378_签入": inbound["12378"]["签入"].get(x), "12378_空闲": inbound["12378"]["空闲"].get(x),
        })
        out_rows.append({
            label: x,
            "常规二线_工单量": outbound["常规二线"]["工单量"].get(x), "常规二线_转接量": outbound["常规二线"]["转接量"].get(x),
            "常规二线_签入": outbound["常规二线"]["签入"].get(x), "常规二线_空闲": outbound["常规二线"]["空闲"].get(x),
            "贷后二线_工单量": outbound["贷后二线"]["工单量"].get(x), "贷后二线_转接量": outbound["贷后二线"]["转接量"].get(x),
            "贷后二线_签入": outbound["贷后二线"]["签入"].get(x), "贷后二线_空闲": outbound["贷后二线"]["空闲"].get(x),
            "二线客诉_工单量": outbound["二线客诉"]["工单量"].get(x),
            "常规工单_工单量": outbound["常规工单"]["工单量"].get(x),
        })
    return in_rows, out_rows

def _forecast_daily(data_dir, line, ym):
    """{日: 当日最大累计预估量}。"""
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["线路"] == line and r["时间"][:7] == ym:
                out[int(r["时间"][8:10])] = int(r["累计预估量"])  # 升序覆盖
    return out

def _forecast_12378_daily(data_dir, ym):
    """{日: 7天前当日收盘累计转人工量}。"""
    from datetime import date as _d, timedelta as _t
    y, m = (int(x) for x in ym.split("-"))
    out = {}
    for dd in range(1, 32):
        try:
            cur = _d(y, m, dd)
        except ValueError:
            break
        prev = (cur - _t(days=7)).strftime("%Y-%m-%d")
        snap = hourly_latest(data_dir, "12378", prev)
        if snap:
            mx = max(snap.values(), key=lambda r: r["时间"])
            out[dd] = mx["转人工量"]
    return out

def build_month(ym, data_dir="data"):
    from calendar import monthrange
    _y, _m = (int(x) for x in ym.split("-"))
    days = list(range(1, monthrange(_y, _m)[1] + 1))

    def latest(name):
        return daily_latest(data_dir, name, ym)

    def avg(name):
        return daily_avg(data_dir, name, ym)

    rx_l, rx_a = latest("热线"), avg("热线明细")
    im_l, im_a = latest("在线"), avg("在线")
    z_l, z_a = latest("12378"), avg("12378明细")
    gd_l = latest("工单明细"); hl_l = latest("会话记录")
    cg_a, dh_a = avg("常规"), avg("贷后")

    fc_rx = _forecast_daily(data_dir, "热线", ym)
    fc_im = _forecast_daily(data_dir, "在线", ym)
    fc_z = _forecast_12378_daily(data_dir, ym)

    def col(src, c):
        return {dd: (src.get(dd, {}) or {}).get(c) for dd in days}

    inbound = {
        "热线": {"days": days, "预测量": fc_rx,
                 "转人工量": col(rx_l, "转人工量"), "转人工成功量": col(rx_l, "接通量"),
                 "签入": col(rx_a, "签入"), "空闲": col(rx_a, "空闲")},
        "在线": {"days": days, "预测量": fc_im,
                 "转人工量": col(im_l, "转人工量"),
                 "转人工成功量": {dd: ((im_l.get(dd, {}) or {}).get("转人工量") or 0) -
                                   ((im_l.get(dd, {}) or {}).get("转人工失败") or 0)
                                   if im_l.get(dd) else None for dd in days},
                 "在线": col(im_a, "在线")},
        "12378": {"days": days, "预测量": fc_z,
                  "转人工量": col(z_l, "转人工量"), "转人工成功量": col(z_l, "接通量"),
                  "签入": col(z_a, "签入"), "空闲": col(z_a, "空闲"),
                  "工单量": col(gd_l, "12378回访组")},
    }
    outbound = {
        "常规二线": {"days": days,
                   "工单量": {dd: ((gd_l.get(dd, {}) or {}).get("转接一组") or 0) +
                                 ((gd_l.get(dd, {}) or {}).get("转接二组") or 0) +
                                 ((gd_l.get(dd, {}) or {}).get("回访组一组") or 0)
                                 if gd_l.get(dd) else None for dd in days},
                   "转接量": {dd: ((hl_l.get(dd, {}) or {}).get("转接一组") or 0) +
                                 ((hl_l.get(dd, {}) or {}).get("转接二组") or 0) +
                                 ((hl_l.get(dd, {}) or {}).get("回访组一组") or 0)
                                 if hl_l.get(dd) else None for dd in days},
                   "签入": col(cg_a, "签入"), "空闲": col(cg_a, "空闲")},
        "贷后二线": {"days": days,
                   "工单量": {dd: ((gd_l.get(dd, {}) or {}).get("贷后转接组") or 0) +
                                 ((gd_l.get(dd, {}) or {}).get("贷后回访组") or 0)
                                 if gd_l.get(dd) else None for dd in days},
                   "转接量": {dd: ((hl_l.get(dd, {}) or {}).get("贷后转接组") or 0) +
                                 ((hl_l.get(dd, {}) or {}).get("贷后回访组") or 0)
                                 if hl_l.get(dd) else None for dd in days},
                   "签入": col(dh_a, "签入"), "空闲": col(dh_a, "空闲")},
        "二线客诉": {"days": days, "工单量": col(gd_l, "二线客诉处理组")},
        "常规工单": {"days": days, "工单量": col(gd_l, "常规工单处理组")},
    }

    def _sum(d):
        vals = [v for v in d.values() if v is not None]
        return sum(vals) if vals else None

    def _avg(d):
        vals = [v for v in d.values() if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def sum2(a, b):
        out = {}
        for dd in days:
            va, vb = a.get(dd), b.get(dd)
            out[dd] = None if (va is None and vb is None) else (va or 0) + (vb or 0)
        return out

    def _in(pred, zrg, succ):
        return {
            "预测量": pred, "时段预测量": pred,
            "流入率": _rate(zrg, pred),
            "转人工量": zrg, "转人工成功量": succ,
            "接通率": _rate(succ, zrg),
        }
    rx_pred = _sum(fc_rx) or 0; im_pred = _sum(fc_im) or 0; z_pred = _sum(fc_z) or 0
    rx_zrg = _sum(inbound["热线"]["转人工量"]) or 0
    im_zrg = _sum(inbound["在线"]["转人工量"]) or 0
    z_zrg = _sum(inbound["12378"]["转人工量"]) or 0
    rx_succ = _sum(inbound["热线"]["转人工成功量"]) or 0
    im_succ = _sum(inbound["在线"]["转人工成功量"]) or 0
    z_succ = _sum(inbound["12378"]["转人工成功量"]) or 0
    card_in = {
        "total": _in(rx_pred + im_pred + z_pred, rx_zrg + im_zrg + z_zrg, rx_succ + im_succ + z_succ),
        "groups": {
            "热线": _in(rx_pred, rx_zrg, rx_succ),
            "在线": _in(im_pred, im_zrg, im_succ),
            "12378": _in(z_pred, z_zrg, z_succ),
        },
    }
    c2_gd = _sum(outbound["常规二线"]["工单量"]) or 0
    dh2_gd = _sum(outbound["贷后二线"]["工单量"]) or 0
    ks_gd = _sum(outbound["二线客诉"]["工单量"]) or 0
    cg2_gd = _sum(outbound["常规工单"]["工单量"]) or 0
    c2_hl = _sum(outbound["常规二线"]["转接量"]) or 0
    dh2_hl = _sum(outbound["贷后二线"]["转接量"]) or 0
    card_out = {
        "total": {
            "工单量": c2_gd + dh2_gd + ks_gd + cg2_gd,
            "转接量": c2_hl + dh2_hl,
            "签入": _avg(sum2(outbound["常规二线"]["签入"], outbound["贷后二线"]["签入"])),
            "空闲": _avg(sum2(outbound["常规二线"]["空闲"], outbound["贷后二线"]["空闲"])),
        },
        "groups": {
            "常规二线": {"工单量": c2_gd, "转接量": c2_hl,
                       "签入": _avg(outbound["常规二线"]["签入"]), "空闲": _avg(outbound["常规二线"]["空闲"])},
            "贷后二线": {"工单量": dh2_gd, "转接量": dh2_hl,
                       "签入": _avg(outbound["贷后二线"]["签入"]), "空闲": _avg(outbound["贷后二线"]["空闲"])},
            "二线客诉": {"工单量": ks_gd},
            "常规工单": {"工单量": cg2_gd},
        },
    }

    # 两张明细表（行=日，列同日视图 5.4）
    in_rows, out_rows = _table_rows(inbound, outbound, days, "日")

    return {"date": ym, "current_hour": None, "days": days,
            "inbound": inbound, "outbound": outbound,
            "card": {"inbound": card_in, "outbound": card_out},
            "tables": {"inbound": in_rows, "outbound": out_rows},
            "headers": {"inbound": _table_header(in_rows[0].keys()),
                        "outbound": _table_header(out_rows[0].keys())}}
