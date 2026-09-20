# -*- coding: utf-8 -*-
"""看板数据层：只读 data/*.db + 预估流入量.csv，做小时/按日聚合。

底层 SQLite 访问委托给 collector.repository.SQLiteReadOnlyRepository；
百分比/卡片口径与「预估流入量.csv」的取值在 collector.metrics
（与企微推送共用同一份口径，不再各写一套）。

取数原则：**一个源一次取行、一份 CSV 一次解析**。
- `_hourly_series` / `_daily_series`：一次取行派生 latest/first/avg；
- `_rows_between`：一次取回整个区间再按日分组（如 12378 的 7 天前对照）。
"""
from calendar import monthrange
from datetime import date as _date, timedelta

from collector import metrics
from collector.repository import SQLiteReadOnlyRepository

def _rows_in(data_dir, source, prefix):
    """某天(prefix=YYYY-MM-DD)或某月(prefix=YYYY-MM)该源所有行(升序)+列名。
    无表/无数据返回 ([], [])。只读 Repository 每次新建,无状态。"""
    return SQLiteReadOnlyRepository(data_dir).rows_in(source, prefix)


def _rows_between(data_dir, source, start, end):
    """[start, end) 区间内该源所有行(升序)+列名；start/end 形如 YYYY-MM-DD。
    用列本身做区间比较(而不是对列套 substr/LIKE)，才能吃到「时间」列的索引。"""
    return SQLiteReadOnlyRepository(data_dir).rows_between(source, start, end)


def _pick(rows, cols, keyfn, keep="last"):
    """{key: 行}；keep="first" 保留最小时间戳，否则升序覆盖保留最大。"""
    out = {}
    for r in rows:
        row = dict(zip(cols, r))
        k = keyfn(row)
        if keep == "first":
            out.setdefault(k, row)
        else:
            out[k] = row
    return out


def _avg_buckets(rows, cols, keyfn):
    """{key: {列: 该桶所有快照均值}}，瞬时量(签入/空闲/在线)用。无表/无数据返回 {}。"""
    buckets = {}
    for r in rows:
        row = dict(zip(cols, r))
        buckets.setdefault(keyfn(row), []).append(row)
    return {k: {c: round(sum((r[c] or 0) for r in rs) / len(rs)) for c in cols if c != "时间"}
            for k, rs in buckets.items()}


def _hourly_series(data_dir, source, date_str):
    """当日「一次取行」派生 (latest, first, avg)。

    原先 latest/first/avg 三个函数各自开连接、各扫一遍当日全表（热线 2 遍、在线 3 遍），
    现在同源只查一次库。返回 (latest, first, avg)：前两者是 {小时: row}，
    avg 是 {小时: {列: 该小时均值}}。
    """
    rows, cols = _rows_in(data_dir, source, date_str)
    key = lambda r: int(r["时间"][11:13])
    return (_pick(rows, cols, key, "last"),
            _pick(rows, cols, key, "first"),
            _avg_buckets(rows, cols, key))


def _daily_series(data_dir, source, ym):
    """当月「一次取行」派生 (latest, avg)，{日: ...}。"""
    rows, cols = _rows_in(data_dir, source, ym)
    key = lambda r: int(r["时间"][8:10])
    return _pick(rows, cols, key, "last"), _avg_buckets(rows, cols, key)


def hourly_latest(data_dir, source, date_str):
    """{小时: 该小时最大时间戳的一行}。"""
    return _hourly_series(data_dir, source, date_str)[0]


def hourly_first(data_dir, source, date_str):
    """{小时: 该小时最小时间戳的一行}。"""
    return _hourly_series(data_dir, source, date_str)[1]


def hourly_avg(data_dir, source, date_str):
    """{小时: {列: 该小时所有快照均值}}，瞬时量(签入/空闲/在线)用。无表/无数据返回 {}。"""
    return _hourly_series(data_dir, source, date_str)[2]


def daily_latest(data_dir, source, ym):
    """{日: 当日最大时间戳的一行}。"""
    return _daily_series(data_dir, source, ym)[0]


def daily_avg(data_dir, source, ym):
    """{日: {列: 当日所有快照均值}}。"""
    return _daily_series(data_dir, source, ym)[1]


def latest_data_date(data_dir="data"):
    """热线/在线 db 中最新的日期(YYYY-MM-DD)。委托给 SQLiteReadOnlyRepository.latest_date。
    这两组窗口 9:00 起，故当天 9 点前取到的是昨天(最近有数据日)，9 点后取今天。
    任一库无数据则回落到今天。"""
    return SQLiteReadOnlyRepository(data_dir).latest_date()

def _forecast_rows(data_dir, line, date_str):
    """该线路、该日期前缀(YYYY-MM-DD)的 [(时间, 时段预估量, 累计预估量)]，按时间升序。

    CSV 的解析由 collector.metrics 按 (mtime, size) 缓存：一次请求里
    load_forecast / forecast_increment / forecast_cum_up_to 反复取同一份文件，
    现在只真正解析一次。只有命中的行才 int()（与旧行为一致：脏行不波及无关查询）。
    """
    out = []
    for ts, ln, inc, cum in metrics.forecast_rows(data_dir):
        if ln == line and ts and ts.startswith(date_str):
            out.append((ts, int(inc), int(cum)))
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

def _forecast_12378_prev_day(date_str):
    """对照日：date_str 往前 7 天(YYYY-MM-DD)。"""
    return (_date.fromisoformat(date_str) - timedelta(days=7)).strftime("%Y-%m-%d")


def forecast_12378_pair(data_dir, date_str):
    """({小时: 收盘累计转人工量}, {小时: 首条累计转人工量})，均取 7 天前那一天。

    日视图要同时用「收盘值」(全天量)与「首条值」(方案D增量起点)，原先各查一次同一个库；
    现在一次取行派生。无数据时两个都返回 {}。
    """
    latest, first, _ = _hourly_series(data_dir, "12378", _forecast_12378_prev_day(date_str))
    return ({hh: row["转人工量"] for hh, row in latest.items()},
            {hh: row["转人工量"] for hh, row in first.items()})


def forecast_12378(data_dir, date_str):
    return forecast_12378_pair(data_dir, date_str)[0]


def forecast_12378_first(data_dir, date_str):
    return forecast_12378_pair(data_dir, date_str)[1]

def _pluck(src, c, xs):
    """{x: src[x][c]}，src[x] 缺失或列缺失时 None。"""
    return {x: (src.get(x) or {}).get(c) for x in xs}

def _hours_for(name, date_str):
    """12378 工作日 8-20、周末 9-17；其余 9-20。返回小时列表。"""
    if name == "12378" and _date.fromisoformat(date_str).weekday() >= 5:
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

def _inc_sum(first, latest, cols, hours):
    """多列方案D增量逐小时求和；任一分量为 None -> None（同源行完整,None 模式一致）。"""
    incs = {c: _inc_col_d(first, latest, c, hours) for c in cols}
    return {h: (None if any(incs[c][h] is None for c in cols)
                else sum((incs[c][h] or 0) for c in cols)) for h in hours}

def build_day(date_str, data_dir="data"):
    # --- 接听各组 hourly 快照：每个源只取一次库，再派生 latest/first/avg ---
    rx, rx_f, _ = _hourly_series(data_dir, "热线", date_str)
    _, _, rx_seat = _hourly_series(data_dir, "热线明细", date_str)   # 只用瞬时均值
    im, im_f, im_a = _hourly_series(data_dir, "在线", date_str)
    z, z_f, _ = _hourly_series(data_dir, "12378", date_str)
    _, _, z_seat = _hourly_series(data_dir, "12378明细", date_str)
    gd, gd_f, _ = _hourly_series(data_dir, "工单明细", date_str)
    hl, hl_f, _ = _hourly_series(data_dir, "会话记录", date_str)

    fc_rx = load_forecast(data_dir, "热线", date_str)
    fc_im = load_forecast(data_dir, "在线", date_str)
    fc_z, fc_z_first = forecast_12378_pair(data_dir, date_str)   # 7 天前那天只查一次库

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
    inc_c2_gd = _inc_sum(gd_f, gd, ("转接一组", "转接二组", "回访组一组"), h_other)
    inc_c2_hl = _inc_sum(hl_f, hl, ("转接一组", "转接二组", "回访组一组"), h_other)
    inc_dh_gd = _inc_sum(gd_f, gd, ("贷后转接组", "贷后回访组"), h_other)
    inc_dh_hl = _inc_sum(hl_f, hl, ("贷后转接组", "贷后回访组"), h_other)
    inc_ks_gd = _inc_col_d(gd_f, gd, "二线客诉处理组", h_other)
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
            "签入": _pluck(rx_seat, "签入", h_other),
            "空闲": _pluck(rx_seat, "空闲", h_other),
        },
        "在线": {
            "hours": h_other,
            "预测量": {h: fc_im_inc.get(h, 0) for h in h_other},
            "转人工量": inc_im_zrg,
            "转人工成功量": inc_im_succ,
            "在线": _pluck(im_a, "在线", h_other),
        },
        "12378": {
            "hours": h_12378,
            "预测量": fc_z_inc,
            "转人工量": inc_z_zrg,
            "转人工成功量": inc_z_succ,
            "签入": _pluck(z_seat, "签入", h_12378),
            "空闲": _pluck(z_seat, "空闲", h_12378),
            "工单量": inc_z_hf,
        },
    }

    # --- 外呼各组 ---
    cg, _, cg_a = _hourly_series(data_dir, "常规", date_str)
    dh, _, dh_a = _hourly_series(data_dir, "贷后", date_str)
    outbound = {
        "常规二线": {
            "hours": h_other,
            "工单量": inc_c2_gd,
            "转接量": inc_c2_hl,
            "签入": _pluck(cg_a, "签入", h_other),
            "空闲": _pluck(cg_a, "空闲", h_other),
        },
        "贷后二线": {
            "hours": h_other,
            "工单量": inc_dh_gd,
            "转接量": inc_dh_hl,
            "签入": _pluck(dh_a, "签入", h_other),
            "空闲": _pluck(dh_a, "空闲", h_other),
        },
        "二线客诉": {"hours": h_other, "工单量": inc_ks_gd},
    }

    # --- current_hour：接听三组实际数据的最大小时 ---
    data_hours = sorted(set(list(rx) + list(im) + list(z)))
    cur = data_hours[-1] if data_hours else None

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
        groups_in = {
            "热线": metrics.card_in(rx_full, rx_cum, rx_zrg, rx_succ),
            "在线": metrics.card_in(im_full, im_cum, im_zrg, im_succ),
            "12378": metrics.card_in(z_full, z_cum, z_zrg, z_succ),
        }
        # 合计卡片 = 各组同口径求和（口径在 collector.metrics，日/月视图共用）
        card_in = {"total": metrics.merge_cards(list(groups_in.values())), "groups": groups_in}
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
        card_out = metrics.outbound_card(
            {
                "常规二线": {"工单量": c2_gd, "转接量": c2_hl, "签入": c2_in, "空闲": c2_free},
                "贷后二线": {"工单量": dh2_gd, "转接量": dh2_hl, "签入": dh2_in, "空闲": dh2_free},
                "二线客诉": {"工单量": ks_gd},
            },
            {"签入": c2_in + dh2_in, "空闲": c2_free + dh2_free},
        )

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
    """看板版百分比：无分母时给 None（模板渲染成空档），口径同 collector.metrics.pct。"""
    return metrics.pct(num, den)

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

# 明细表字段序（决定表头列序，与 _table_header 分组一致）
_IN_FIELDS = {
    "热线": ["预测量", "流入率", "转人工量", "转人工成功量", "接通率", "签入", "空闲"],
    "在线": ["预测量", "流入率", "转人工量", "转人工成功量", "接通率", "在线"],
    "12378": ["工单量", "转人工量", "转人工成功量", "接通率", "签入", "空闲"],
}
_OUT_FIELDS = {
    "常规二线": ["工单量", "转接量", "签入", "空闲"],
    "贷后二线": ["工单量", "转接量", "签入", "空闲"],
    "二线客诉": ["工单量"],
}


def _metric_row(group, src, x, fields):
    """按字段序取该组一行指标；流入率/接通率为派生量。"""
    d = {k: v.get(x) for k, v in src[group].items() if isinstance(v, dict)}
    row = {}
    for f in fields:
        if f == "流入率":
            row[f"{group}_流入率"] = _rate(d.get("转人工量"), d.get("预测量"))
        elif f == "接通率":
            row[f"{group}_接通率"] = _rate(d.get("转人工成功量"), d.get("转人工量"))
        else:
            row[f"{group}_{f}"] = d.get(f)
    return row


def _table_rows(inbound, outbound, xs, label):
    """构造两张明细表的行。xs=小时/日列表，label='小时'/'日'。字段序决定表头列序。"""
    in_rows, out_rows = [], []
    for x in xs:
        in_rows.append({label: x, **{k: v for g in _IN_FIELDS
                                     for k, v in _metric_row(g, inbound, x, _IN_FIELDS[g]).items()}})
        out_rows.append({label: x, **{k: v for g in _OUT_FIELDS
                                      for k, v in _metric_row(g, outbound, x, _OUT_FIELDS[g]).items()}})
    return in_rows, out_rows

def _forecast_daily(data_dir, line, ym):
    """{日: 当日最大累计预估量}。"""
    return {int(ts[8:10]): cum for ts, _inc, cum in _forecast_rows(data_dir, line, ym)}

def _forecast_12378_daily(data_dir, ym):
    """{日: 7天前当日收盘累计转人工量}。

    原先对本月的每一天各查一次 12378 库（一个月 = 30 次查询）；7 天前的日期会跨两个月，
    故一次把 [本月1日-7, 本月末-7] 整段取回，再按日分组取当天最后一条。
    """
    y, m = (int(x) for x in ym.split("-"))
    last = monthrange(y, m)[1]
    start = (_date(y, m, 1) - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (_date(y, m, last) - timedelta(days=6)).strftime("%Y-%m-%d")   # 开区间上界
    rows, cols = _rows_between(data_dir, "12378", start, end)
    if not rows:
        return {}
    closing = {}   # 行已按时间升序 -> 同日反复覆盖即得当天最后一条
    for r in rows:
        row = dict(zip(cols, r))
        closing[row["时间"][:10]] = row["转人工量"]
    out = {}
    for dd in range(1, last + 1):
        prev = (_date(y, m, dd) - timedelta(days=7)).strftime("%Y-%m-%d")
        if prev in closing:
            out[dd] = closing[prev]
    return out

def _col_sum(src, cols, xs):
    """{x: 各列之和}；行缺失时 None。"""
    return {x: (sum((src.get(x) or {}).get(c) or 0 for c in cols) if src.get(x) else None) for x in xs}

def build_month(ym, data_dir="data"):
    _y, _m = (int(x) for x in ym.split("-"))
    days = list(range(1, monthrange(_y, _m)[1] + 1))

    # --- 每个源只取一次库（原先 latest/avg 各查一次；在线要两个口径要查两次）---
    rx_l, _ = _daily_series(data_dir, "热线", ym)
    _, rx_a = _daily_series(data_dir, "热线明细", ym)
    im_l, im_a = _daily_series(data_dir, "在线", ym)
    z_l, _ = _daily_series(data_dir, "12378", ym)
    _, z_a = _daily_series(data_dir, "12378明细", ym)
    gd_l, _ = _daily_series(data_dir, "工单明细", ym)
    hl_l, _ = _daily_series(data_dir, "会话记录", ym)
    _, cg_a = _daily_series(data_dir, "常规", ym)
    _, dh_a = _daily_series(data_dir, "贷后", ym)

    fc_rx = _forecast_daily(data_dir, "热线", ym)
    fc_im = _forecast_daily(data_dir, "在线", ym)
    fc_z = _forecast_12378_daily(data_dir, ym)

    inbound = {
        "热线": {"days": days, "预测量": fc_rx,
                 "转人工量": _pluck(rx_l, "转人工量", days), "转人工成功量": _pluck(rx_l, "接通量", days),
                 "签入": _pluck(rx_a, "签入", days), "空闲": _pluck(rx_a, "空闲", days)},
        "在线": {"days": days, "预测量": fc_im,
                 "转人工量": _pluck(im_l, "转人工量", days),
                 "转人工成功量": {dd: ((im_l.get(dd, {}) or {}).get("转人工量") or 0) -
                                   ((im_l.get(dd, {}) or {}).get("转人工失败") or 0)
                                   if im_l.get(dd) else None for dd in days},
                 "在线": _pluck(im_a, "在线", days)},
        "12378": {"days": days, "预测量": fc_z,
                  "转人工量": _pluck(z_l, "转人工量", days), "转人工成功量": _pluck(z_l, "接通量", days),
                  "签入": _pluck(z_a, "签入", days), "空闲": _pluck(z_a, "空闲", days),
                  "工单量": _pluck(gd_l, "12378回访组", days)},
    }
    outbound = {
        "常规二线": {"days": days,
                   "工单量": _col_sum(gd_l, ("转接一组", "转接二组", "回访组一组"), days),
                   "转接量": _col_sum(hl_l, ("转接一组", "转接二组", "回访组一组"), days),
                   "签入": _pluck(cg_a, "签入", days), "空闲": _pluck(cg_a, "空闲", days)},
        "贷后二线": {"days": days,
                   "工单量": _col_sum(gd_l, ("贷后转接组", "贷后回访组"), days),
                   "转接量": _col_sum(hl_l, ("贷后转接组", "贷后回访组"), days),
                   "签入": _pluck(dh_a, "签入", days), "空闲": _pluck(dh_a, "空闲", days)},
        "二线客诉": {"days": days, "工单量": _pluck(gd_l, "二线客诉处理组", days)},
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

    rx_pred = _sum(fc_rx) or 0; im_pred = _sum(fc_im) or 0; z_pred = _sum(fc_z) or 0
    rx_zrg = _sum(inbound["热线"]["转人工量"]) or 0
    im_zrg = _sum(inbound["在线"]["转人工量"]) or 0
    z_zrg = _sum(inbound["12378"]["转人工量"]) or 0
    rx_succ = _sum(inbound["热线"]["转人工成功量"]) or 0
    im_succ = _sum(inbound["在线"]["转人工成功量"]) or 0
    z_succ = _sum(inbound["12378"]["转人工成功量"]) or 0
    groups_in = {
        "热线": metrics.card_in(rx_pred, rx_pred, rx_zrg, rx_succ),
        "在线": metrics.card_in(im_pred, im_pred, im_zrg, im_succ),
        "12378": metrics.card_in(z_pred, z_pred, z_zrg, z_succ),
    }
    card_in = {"total": metrics.merge_cards(list(groups_in.values())), "groups": groups_in}
    c2_gd = _sum(outbound["常规二线"]["工单量"]) or 0
    dh2_gd = _sum(outbound["贷后二线"]["工单量"]) or 0
    ks_gd = _sum(outbound["二线客诉"]["工单量"]) or 0
    c2_hl = _sum(outbound["常规二线"]["转接量"]) or 0
    dh2_hl = _sum(outbound["贷后二线"]["转接量"]) or 0
    card_out = metrics.outbound_card(
        {
            "常规二线": {"工单量": c2_gd, "转接量": c2_hl,
                       "签入": _avg(outbound["常规二线"]["签入"]), "空闲": _avg(outbound["常规二线"]["空闲"])},
            "贷后二线": {"工单量": dh2_gd, "转接量": dh2_hl,
                       "签入": _avg(outbound["贷后二线"]["签入"]), "空闲": _avg(outbound["贷后二线"]["空闲"])},
            "二线客诉": {"工单量": ks_gd},
        },
        # 月视图的签入/空闲是「各日均值再平均」，与日视图的「各组相加」口径不同，故在此算好传入
        {"签入": _avg(sum2(outbound["常规二线"]["签入"], outbound["贷后二线"]["签入"])),
         "空闲": _avg(sum2(outbound["常规二线"]["空闲"], outbound["贷后二线"]["空闲"]))},
    )

    # 两张明细表（行=日，列同日视图 5.4）
    in_rows, out_rows = _table_rows(inbound, outbound, days, "日")

    return {"date": ym, "current_hour": None, "days": days,
            "inbound": inbound, "outbound": outbound,
            "card": {"inbound": card_in, "outbound": card_out},
            "tables": {"inbound": in_rows, "outbound": out_rows},
            "headers": {"inbound": _table_header(in_rows[0].keys()),
                        "outbound": _table_header(out_rows[0].keys())}}
