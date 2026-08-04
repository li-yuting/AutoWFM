# -*- coding: utf-8 -*-
"""一次性回填: 用 v14 的 CSV 把 7 月 1 日以来的历史数据灌进 AutoWFM 的 9 个库。

可回填 7 个库(热线/热线明细/12378/12378明细/常规/贷后/在线)。
会话记录/工单明细 无 CSV 来源(requests 导出,v14 未存)。
预估流入量.csv 无对应 AutoWFM 库,跳过。

缺列(v14 从未记录)用 NULL:
  热线/12378: 累计呼入量/外呼量/外呼接通量
  在线: 咨询

按 时间 去重: 跳过 db 已有的时间戳(AutoWFM 实时行更全,优先保留)。
用法: python spike\backfill_from_csv.py [--apply]
"""
import csv, sqlite3, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from storage import SCHEMAS  # noqa: E402

DATA = ROOT / "data"
CSVDIR = Path(r"D:\PythonProject\hfqwfm\everyday\data")
SINCE = "2026-07-01 00:00"  # 字符串比较,YYYY-MM-DD HH:MM 可排序

# db -> (csv 文件, 技能过滤(None=不过滤), {db列: csv列索引 或 None=缺列})
SPECS = {
    "热线":     ("热线登录数据.csv", None, {"转人工量": 2, "接通量": 3, "排队量": 4,
                                        "累计呼入量": None, "外呼量": None, "外呼接通量": None}),
    "热线明细": ("热线登录数据.csv", None, {"签入": 5, "通话": 6, "空闲": 7, "离席": 8,
                                        "话后": 9, "振铃": 10, "置忙": 11}),
    "12378":    ("12378登录数据.csv", None, {"转人工量": 2, "接通量": 3, "排队量": 4,
                                        "累计呼入量": None, "外呼量": None, "外呼接通量": None}),
    "12378明细": ("12378登录数据.csv", None, {"签入": 5, "通话": 6, "空闲": 7, "离席": 8,
                                        "话后": 9, "振铃": 10, "置忙": 11}),
    "常规":     ("二线登录数据.csv", "常规转接组", {"签入": 2, "通话": 3, "空闲": 4, "离席": 5,
                                              "话后": 6, "振铃": 7, "置忙": 8}),
    "贷后":     ("二线登录数据.csv", "贷后转接组", {"签入": 2, "通话": 3, "空闲": 4, "离席": 5,
                                              "话后": 6, "振铃": 7, "置忙": 8}),
    "在线":     ("在线接通数据.csv", "统计监控-在线", {"转人工量": 2, "转人工失败": 4, "排队": 5,
                                                "在线": 7, "小休": 8, "话后": 9, "示忙": 10,
                                                "就餐": 11, "培训": 12, "回访": 13, "咨询": None}),
}


def to_int(s):
    s = (s or "").strip()
    if s == "" or s == "-":
        return None
    return int(round(float(s)))


def existing_times(dbpath):
    if not dbpath.exists():
        return set()
    conn = sqlite3.connect(str(dbpath))
    try:
        rows = conn.execute("SELECT 时间 FROM t").fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()
    return {r[0][:16] for r in rows}


def read_rows(csv_name, skill, col_map):
    """返回 [(时间, {db列: 值}), ...],已按 SINCE + 技能过滤,且按时间戳去重(保留首条)。"""
    max_idx = max(i for i in col_map.values() if i is not None)
    out = []
    seen = set()  # ponytail: 07-10 16:15/21:13 等异常双写,CSV 内去重
    with open(CSVDIR / csv_name, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip().startswith("2026"):
                continue
            t = row[0].strip()[:16]
            if t < SINCE or t in seen:
                continue
            if len(row) <= max_idx:
                continue  # 列数不足(如 七鱼 7 列),跳过
            if skill is not None and row[1].strip() != skill:
                continue
            seen.add(t)
            vals = {c: (to_int(row[idx]) if idx is not None else None) for c, idx in col_map.items()}
            out.append((t, vals))
    return out


def main():
    apply = "--apply" in sys.argv
    print(f"模式: {'写入' if apply else 'DRY-RUN(只预览)'}\n")
    total_in = 0
    for source, (csv_name, skill, col_map) in SPECS.items():
        cols = SCHEMAS[source]  # 含 时间
        dbpath = DATA / f"{source}.db"
        have = existing_times(dbpath)
        rows = read_rows(csv_name, skill, col_map)
        new = [(t, v) for t, v in rows if t not in have]
        total_in += len(new)
        sample = new[0] if new else (None, None)
        print(f"[{source}] <- {csv_name}" + (f" 技能={skill}" if skill else ""))
        print(f"    CSV 命中 {len(rows)} 行, db 已有 {len(have)} 个时间戳, 待插入 {len(new)} 行")
        if sample[0]:
            print(f"    样本 时间={sample[0]}  值={sample[1]}")
        miss = [c for c, i in col_map.items() if i is None]
        if miss:
            print(f"    缺列(置 NULL): {miss}")
        if apply and new:
            conn = sqlite3.connect(str(dbpath))
            try:
                col_def = ",".join(f'"{c}" {"TEXT" if c == "时间" else "INTEGER"}' for c in cols)
                conn.execute(f"CREATE TABLE IF NOT EXISTS t ({col_def})")
                quoted = ",".join('"' + c + '"' for c in cols)
                ph = ",".join("?" * len(cols))
                payload = [[t] + [v[c] for c in cols[1:]] for t, v in new]
                conn.executemany(f"INSERT INTO t ({quoted}) VALUES ({ph})", payload)
                conn.commit()
            finally:
                conn.close()
        print()
    print(f"总计待插入 {total_in} 行" + ("  [已写入]" if apply else "  [未写入,加 --apply 执行]"))


if __name__ == "__main__":
    main()
