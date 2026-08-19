# -*- coding: utf-8 -*-
"""回填 工单明细/会话记录 历史缺失数据（5 分钟颗粒度）。

按时间列(工单明细=创建日期, 会话记录=开始时间)的 5 分钟分桶，生成业务窗口内
每 5 分钟的累计快照(值=该刻度之前创建/开始的累计) + 23:59 全天总计。dashboard
方案D 用 first[H+1]-first[H] 即得 H 小时新建量。

由根目录 backfill.py(薄 CLI) 和 manager.py(数据补全页) 共同调用。
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import time
from collector import storage

TIME_COL = {
    "工单明细": ("创建日期", "%Y-%m-%d %H:%M:%S"),
    "会话记录": ("开始时间", "%H:%M:%S"),
}

SLEEP = 2  # 每天下载后间隔秒数，避免请求过快；测试可置 0


def iter_days(start, end):
    """生成日期列表 YYYY-MM-DD，含首尾。start==end 返回单日。"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    out = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def day_row_count(source, day, data_dir):
    """该 source db 中某天的行数。无库返回 0。"""
    p = Path(data_dir) / f"{source}.db"
    if not p.exists():
        return 0
    c = sqlite3.connect(str(p))
    try:
        return c.execute('SELECT COUNT(*) FROM t WHERE "时间" LIKE ?', (f'{day}%',)).fetchone()[0]
    finally:
        c.close()


def clear_day(source, day, data_dir):
    """删除该 source db 中某天的所有行。"""
    p = Path(data_dir) / f"{source}.db"
    if not p.exists():
        return
    c = sqlite3.connect(str(p))
    try:
        c.execute('DELETE FROM t WHERE "时间" LIKE ?', (f'{day}%',))
        c.commit()
    finally:
        c.close()


def build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end, cutoff=None):
    """5 分钟分桶 -> 业务窗口累计快照 + 23:59 全天总计。
    返回 (rows, total)：rows=窗口刻度(+可选 23:59) 的 dict 列表，total={组:全天全量}。
    全天总计用 filter 后全量(含时间列缺失的记录)，与实时 count_groups 口径一致；
    累计快照仅含能分桶的记录(时间列缺失的不计入任何刻度)。
    cutoff="HH:MM"：仅生成时刻 ≤ cutoff 的刻度，且不追加 23:59 全天总计行
    (用于补全「当天」时，只补过去时段、不写未来数据)。cutoff=None 保持原整天行为。"""
    import pandas as pd
    from collector.detail import _apply_row_exclude, _match_group
    d = df
    if fcfg.get("channel_column"):
        d = d[d[fcfg["channel_column"]].isin(fcfg["channels"])]
    gc = fcfg["group_column"]
    d = d.copy()
    d[gc] = d[gc].map(lambda v: _match_group(v, groups))  # 前缀折叠，与实时 count_groups 同口径
    d = d[d[gc].notna()]
    d = _apply_row_exclude(d, fcfg)  # 与实时 count_groups 同步行级排除口径
    ts = pd.to_datetime(d[time_col], format=fmt, errors="coerce")
    mods = ts.dt.hour * 60 + ts.dt.minute          # minute_of_day, NaN for 缺失
    slots = mods // 5                                # 0..287, NaN for 缺失
    bucket = {}
    for slot in range(288):
        cnt = d[slots == slot][gc].value_counts()
        bucket[slot] = {g: int(cnt.get(g, 0)) for g in groups}
    cum = {}
    running = {g: 0 for g in groups}
    for slot in range(288):
        cum[slot] = dict(running)
        for g in groups:
            running[g] += bucket[slot][g]
    full_cnt = d[gc].value_counts()
    total = {g: int(full_cnt.get(g, 0)) for g in groups}
    sh, sm = (int(x) for x in win_start.split(":"))
    eh, em = (int(x) for x in win_end.split(":"))
    start_slot = (sh * 60 + sm) // 5
    end_slot = (eh * 60 + em) // 5
    # cutoff：补全当天时，只输出 ≤ 当前时刻的刻度，不写未来时段
    if cutoff is not None:
        ch, cm = (int(x) for x in cutoff.split(":"))
        cutoff_slot = (ch * 60 + cm) // 5
        end_slot = min(end_slot, cutoff_slot)
    rows = []
    for slot in range(start_slot, end_slot + 1):
        hh, mm = divmod(slot * 5, 60)
        vals = {"时间": f"{day} {hh:02d}:{mm:02d}"}
        vals.update(cum[slot])
        rows.append(vals)
    if cutoff is None:
        vals = {"时间": f"{day} 23:59"}
        vals.update(total)
        rows.append(vals)
    return rows, total


def download_day(mcfg, secrets, day, timeout=60):
    """下载某天明细 Excel，返回原始 df。"""
    import requests
    from collector.detail import _parse_excel
    data = dict(mcfg["data"])
    data["token"] = secrets["token"]
    data["tenementId"] = secrets["tenementId"]
    dv = day if mcfg["date_format"] == "%Y-%m-%d" else f"{day} 00:00:00"
    data[mcfg["date_fields"]["start"]] = dv
    data[mcfg["date_fields"]["end"]] = dv
    resp = requests.post(mcfg["url"], json=data, timeout=timeout)
    resp.raise_for_status()
    return _parse_excel(resp.content)


def backfill_source(source, cfg, days, data_dir, overwrite=True, progress_cb=None, now=None):
    """回填某 source 的若干天。返回 {"成功":int,"失败":int,"失败日期":[str]}。
    overwrite=True：每天下载成功后先 clear_day 再写；下载失败则 continue 下一天。
    now=None 时取当前时间；补全「当天」时只写 ≤ 当前时刻的刻度(不写未来时段/23:59)。"""
    mcfg = cfg["detail_modes"][source]
    secrets = cfg["secrets"]
    fcfg = mcfg["filter"]
    groups = fcfg["groups"]
    time_col, fmt = TIME_COL[source]
    win_start = cfg["schedule"]["window_start"]
    win_end = cfg["schedule"]["window_end"]
    if progress_cb is None:
        progress_cb = lambda s: None
    if now is None:
        now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    ok = fail = 0
    fail_days = []
    for day in days:
        if not overwrite and day_row_count(source, day, data_dir) > 0:
            progress_cb(f"{source} {day}: 已有数据，跳过")
            continue
        try:
            df = download_day(mcfg, secrets, day)
        except Exception as e:
            progress_cb(f"{source} {day}: 下载失败 {e}")
            fail += 1
            fail_days.append(day)
            continue
        rows, total = build_snapshots(df, day, fcfg, groups, time_col, fmt, win_start, win_end,
                                      cutoff=(now.strftime("%H:%M") if day == today_str else None))
        clear_day(source, day, data_dir)
        for vals in rows:
            storage.insert(source, vals, data_dir)
        progress_cb(f"{source} {day}: 写入 {len(rows)} 行 | "
                    + " ".join(f"{g}={total[g]}" for g in groups))
        ok += 1
        time.sleep(SLEEP)
    return {"成功": ok, "失败": fail, "失败日期": fail_days}