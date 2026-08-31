from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from peakflow import config, dashboard, loader, report
from peakflow import fetch as fetch_mod
from peakflow.forecast import three_band_forecast

CHANNEL_FILES = [("在线", "在线各类用户.csv"), ("热线", "热线各类用户.csv")]


def run_forecast(fetch: bool = False, out_dir: Path | None = None) -> Path:
    if fetch:
        src = fetch_mod.sync_from_autotableau()
        print(f"已同步数据，来源: {src}")
    if out_dir is None:
        out_dir = config.OUTPUT_DIR
    histories, results, sigmas = {}, {}, {}
    history_days = 0
    for channel, fname in CHANNEL_FILES:
        path = config.DATA_DIR / fname
        hist = loader.load_channel_data(path)
        histories[channel] = hist
        history_days = int(hist["date"].nunique())
        if history_days < config.MIN_HISTORY:
            print(f"警告: {channel} 历史仅 {history_days} 天 (< {config.MIN_HISTORY})，预测可能不稳")
        last_date = hist["date"].max()
        future_dates = [last_date + dt.timedelta(days=i)
                        for i in range(1, config.HORIZON + 1)]
        results[channel], sigmas[channel] = three_band_forecast(hist, future_dates)
        print(f"{channel}: 预测完成，数据截止 {last_date.date()}，未来 {len(future_dates)} 天")
    # 注意: last_date / history_days 取自 for 循环的最后一个渠道(热线)。
    # 两渠道数据截止日/历史天数不同时, 这里只反映热线; 设计评审确认保持原样(与 PeakFlow 一致)。
    meta = {
        "生成时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "数据截止日期": str(last_date.date()),
        "历史天数": history_days,
        "预测天数": config.HORIZON,
        "趋势拟合天数": config.TREND_FIT_DAYS,
        "转人工占比窗口": config.RATIO_WINDOW,
        "回测窗口": config.BACKTEST_WINDOW,
        "区间倍数 k": config.SIGMA_K,
        "模型": "趋势+周季节分解外推（客户量/咨询占比），固定转人工占比",
    }
    out_dir = Path(out_dir)
    run_day = dt.date.today().isoformat()
    out_dir = out_dir / run_day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"预测_{dt.date.today():%Y%m%d}_未来{config.HORIZON}天.xlsx"
    report.write_report(results["在线"], results["热线"], sigmas, meta, out_path)
    dashboard_path = out_path.with_suffix(".html")
    dashboard.write_dashboard(
        dashboard.build_dashboard_data(histories, results, meta), dashboard_path
    )
    print("网页:", dashboard_path)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="客服中心 30 天在线/热线咨询量预测")
    ap.add_argument("--fetch", action="store_true",
                    help="先从 AutoTableau 同步最新数据，再预测")
    args = ap.parse_args(argv)
    out = run_forecast(fetch=args.fetch)
    print("完成:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
