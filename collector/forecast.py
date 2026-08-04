"""进线量预测: 热线/在线未来30天转人工量预测，用于排班。

集成自 D:\\PythonProject\\进线量预估\\forecast.py。改动:
- 路径改为相对 AutoWFM 项目根(BASE_DIR = collector/ 的父目录)。
- _run_business 硬断言失败由 raise SystemExit 改为 raise RuntimeError
  (SystemExit 会杀死常驻调度器进程)。
- 新增 check_next_day_diff: 供 scheduler 21:05 调用, 跑次日预测并与
  data/预估流入量.csv 次日全天累计预估量对比, 差异超阈值发企微告警。

预测逻辑(extract_volume/fit_model/forecast/self_check 等)与原文件一致, 未改。
"""
from __future__ import annotations
import csv
import datetime
import logging
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3
import numpy as np
from collector import notify
import pandas as pd
import statsmodels.formula.api as smf

log = logging.getLogger("autowfm")

# 业务常量
MARKETING_STOP = pd.Timestamp("2026-07-11")
COLLECTION_START, COLLECTION_END = 5, 25
FORECAST_DAYS = 7
RECENT_DAYS = 7
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 路径(项目根 = collector/ 的父目录)
BASE_DIR = Path(__file__).resolve().parent.parent
HOTLINE_DB = BASE_DIR / "data" / "热线.db"
ONLINE_DB = BASE_DIR / "data" / "在线.db"
HOLIDAYS_FILE = BASE_DIR / "holidays.txt"
OUTPUT_DIR = BASE_DIR / "output"


def extract_volume(path):
    """读 SQLite(t 表)每日21:00累计转人工量，无21:00的日期跳过。返回[date, volume]。"""
    con = sqlite3.connect(path)
    try:
        df = pd.read_sql_query("SELECT 时间, 转人工量 FROM t WHERE 时间 LIKE '% 21:00'", con)
    finally:
        con.close()
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    df["date"] = df["时间"].dt.normalize()
    df["volume"] = pd.to_numeric(df["转人工量"], errors="coerce")
    df = df.dropna(subset=["volume"])
    return df[["date", "volume"]].reset_index(drop=True)


def build_features(dates):
    """构建特征[weekday, collection, post_mkt]，index 为日期。"""
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame({
        "weekday": [WEEKDAY_NAMES[d.weekday()] for d in idx],
        "collection": ["是" if COLLECTION_START <= d.day <= COLLECTION_END else "否" for d in idx],
        "post_mkt": ["是" if d >= MARKETING_STOP else "否" for d in idx],
    }, index=idx)


def fit_model(history):
    """拟合 log(volume) ~ C(weekday)+C(collection)+C(post_mkt)。返回 RegressionResults。"""
    h = history.dropna(subset=["volume"]).copy()
    h = h[h["volume"] > 0]
    feat = build_features(h["date"])
    feat["log_v"] = np.log(h["volume"].values)
    return smf.ols("log_v ~ C(weekday) + C(collection) + C(post_mkt)", data=feat).fit()


def predict_shape(model, dates):
    """S_t = exp(回归预测 log V)，pd.Series，index 为日期。"""
    feat = build_features(dates)
    pred_log = model.predict(feat)
    return pd.Series(np.exp(pred_log.values), index=pd.DatetimeIndex(dates), name="shape")


def forecast(history, model, future_dates):
    """再锚定预测: 预测量_t = A × (S_t / M)。返回[预测日期, 形状比例, 预测转人工量]。"""
    h = history.dropna(subset=["volume"]).sort_values("date").reset_index(drop=True)
    recent = h.tail(RECENT_DAYS)
    A = recent["volume"].mean()
    S_recent = predict_shape(model, recent["date"])
    M = S_recent.mean()
    S_future = predict_shape(model, future_dates)
    ratio = S_future / M
    pred = (A * ratio).round().astype(int)
    return pd.DataFrame({
        "预测日期": pd.DatetimeIndex(future_dates),
        "形状比例": ratio.values,
        "预测转人工量": pred.values,
    })


def load_holidays(path=HOLIDAYS_FILE):
    """读 holidays.txt 返回 set[Timestamp]。每行一个 YYYY-MM-DD，# 注释，空行跳过。"""
    holidays = set()
    p = Path(path)
    if not p.exists():
        return holidays
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        holidays.add(pd.Timestamp(line).normalize())
    return holidays


def self_check(forecast_df, history):
    """自检: 硬断言(预测>0) + 区间校验(周内/周末最近7天 ±15%)。返回(ok, out_of_band_dates)。"""
    vols = forecast_df["预测转人工量"]
    if (vols <= 0).any():
        return False, []
    h = history.dropna(subset=["volume"]).sort_values("date").tail(RECENT_DAYS)
    wd = h[h["date"].dt.weekday < 5]["volume"]    # 周一~周五
    we = h[h["date"].dt.weekday >= 5]["volume"]   # 周六~周日
    out = []
    for _, row in forecast_df.iterrows():
        d = pd.Timestamp(row["预测日期"])
        v = row["预测转人工量"]
        ref = wd if d.weekday() < 5 else we
        if len(ref) == 0:
            continue
        lo, hi = ref.min() * 0.85, ref.max() * 1.15
        if not (lo <= v <= hi):
            out.append(d)
    return True, out


def reference_days(target_date, history, n=3):
    """同星期+同催收状态的历史实际值，取最近n个。格式 'MM-DD(周X):值 / ...'。"""
    td = pd.Timestamp(target_date)
    tgt_weekday = WEEKDAY_NAMES[td.weekday()]
    tgt_coll = "是" if COLLECTION_START <= td.day <= COLLECTION_END else "否"
    h = history.dropna(subset=["volume"]).sort_values("date", ascending=False)
    matches = []
    for _, row in h.iterrows():
        d = pd.Timestamp(row["date"])
        if WEEKDAY_NAMES[d.weekday()] != tgt_weekday:
            continue
        d_coll = "是" if COLLECTION_START <= d.day <= COLLECTION_END else "否"
        if d_coll != tgt_coll:
            continue
        matches.append(f"{d.strftime('%m-%d')}({WEEKDAY_NAMES[d.weekday()]}):{int(row['volume'])}")
        if len(matches) >= n:
            break
    return " / ".join(matches) if matches else "无"


def _run_business(business, path, days=FORECAST_DAYS):
    """单业务: 提取->拟合->预测->自检->补充列。返回(预测DataFrame, 最后数据日)。"""
    history = extract_volume(path)
    history = history.sort_values("date").reset_index(drop=True)
    model = fit_model(history)
    last_date = history["date"].max()
    future = pd.date_range(last_date + pd.Timedelta(days=1), periods=days, freq="D")
    fc = forecast(history, model, future)

    # 节假日（显式传模块全局 HOLIDAYS_FILE，使 monkeypatch 生效）
    holidays = load_holidays(HOLIDAYS_FILE)
    fc["节假日"] = fc["预测日期"].isin(holidays).map({True: "是", False: "否"})
    for d in fc.loc[fc["节假日"] == "是", "预测日期"]:
        print(f"[节假日提醒-{business}] {pd.Timestamp(d).strftime('%Y-%m-%d')} 命中节假日，预测未自动下调，请人工复核")

    # 自检
    ok, out_dates = self_check(fc, history)
    if not ok:
        print(f"[硬断言失败-{business}] 存在预测量<=0，公式有误")
        raise RuntimeError(f"{business} 自检硬断言失败，预测量<=0")
    out_set = set(out_dates)
    fc["超界标记"] = fc["预测日期"].isin(out_set).map({True: "是", False: "否"})
    for d in out_dates:
        print(f"[警告-{business}] {d.strftime('%Y-%m-%d')} 预测超界(超出最近7天同类型±15%)")

    # 业务/星期/催收期/参考
    fc["业务"] = business
    feat = build_features(fc["预测日期"])
    fc["星期"] = feat["weekday"].values
    fc["催收期"] = feat["collection"].values
    fc["历史同类型日参考"] = [reference_days(d, history) for d in fc["预测日期"]]
    return fc, last_date


def run_forecast(days=FORECAST_DAYS, write_csv=True):
    """跑热线+在线未来 days 天预测。返回 (DataFrame, csv_path|None)。
    供 manager.py 手动触发;main() 也走这里。check_next_day_diff 仍直接调 _run_business。"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    frames = []
    overall_last = None
    for business, path in [("热线", HOTLINE_DB), ("在线", ONLINE_DB)]:
        fc, last_date = _run_business(business, path, days=days)
        frames.append(fc)
        overall_last = last_date if overall_last is None else max(overall_last, last_date)

    cols = ["预测日期", "星期", "催收期", "节假日", "业务",
            "形状比例", "预测转人工量", "超界标记", "历史同类型日参考"]
    out = pd.concat(frames, ignore_index=True)[cols]
    out_path = None
    if write_csv:
        out_path = OUTPUT_DIR / f"进线量预测_{overall_last.strftime('%Y%m%d')}.csv"
        out.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out, out_path


def main():
    out, out_path = run_forecast(FORECAST_DAYS)
    print(f"已输出: {out_path} ({len(out)} 行)")


def _csv_next_day_total(data_dir, line, next_date_str):
    """预估流入量.csv 中 next_date 该线路 累计预估量 的最大值(=全天预估总量)。
    无文件/无该日该线路数据返回 None。时间列形如 '2026-07-30 09:15'，用 startswith 匹配。"""
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return None
    max_cum = None
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("线路") == line and row.get("时间", "").startswith(next_date_str):
                try:
                    v = int(row["累计预估量"])
                except (ValueError, KeyError):
                    continue
                if max_cum is None or v > max_cum:
                    max_cum = v
    return max_cum


def check_next_day_diff(cfg, now=None):
    """跑次日(明天)转人工量预测, 与 预估流入量.csv 次日全天累计预估量对比,
    相对差异 |forecast-csv|/csv 超 cfg['forecast']['diff_threshold'] 则发企微 text
    告警(走 main_key, @ cfg['forecast']['alert_recipient'])。供 scheduler 21:05 调用,
    任何异常都吞掉, 不影响调度器。"""
    try:
        tz = ZoneInfo(cfg["schedule"]["timezone"])
        now = now or datetime.datetime.now(tz)
        next_date = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        next_ts = pd.Timestamp(next_date)
        data_dir = cfg["storage"]["dir"]
        fc_cfg = cfg["forecast"]
        threshold = fc_cfg["diff_threshold"]
        recipient = fc_cfg["alert_recipient"]
        wh_key = cfg["notify"]["webhook"]["main_key"]

        lines = []
        for business, db_name in [("热线", "热线"), ("在线", "在线")]:
            try:
                db_path = str(Path(data_dir) / f"{db_name}.db")
                fc, _last = _run_business(business, db_path)
            except Exception:
                log.exception(f"[forecast] {business} 预测失败, 跳过")
                continue
            row = fc[fc["预测日期"] == next_ts]
            if row.empty:
                log.warning(f"[forecast] {business} 次日 {next_date} 无预测行, 跳过")
                continue
            f_val = int(row["预测转人工量"].iloc[0])
            c_val = _csv_next_day_total(data_dir, business, next_date)
            if not c_val:
                log.warning(f"[forecast] {business} CSV 次日 {next_date} 无累计预估量, 跳过")
                continue
            diff = abs(f_val - c_val) / c_val
            if diff > threshold:
                lines.append(f"{business}: 预测={f_val} CSV={c_val} 差异={diff*100:.1f}%")

        if lines:
            msg = (f"⚠️ 次日预测量差异告警 {now.strftime('%Y-%m-%d %H:%M')}\n"
                   f"次日({next_date}) forecast 与 CSV 预估差异超阈值({threshold*100:.0f}%):\n"
                   + "\n".join(lines))
            log.info(notify._send_text(wh_key, recipient, msg))
        else:
            log.info(f"[forecast] 次日 {next_date} 差异均 <= {threshold*100:.0f}%, 无告警")
    except Exception:
        log.exception("[forecast] check_next_day_diff 异常")


if __name__ == "__main__":
    main()
