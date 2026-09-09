import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from peakflow import models
from peakflow import config
from tests.helpers import make_series


def _with_holiday(rows, fn):
    """把 rows 写成临时节假日.csv 并指向 config.HOLIDAY_FILE，执行 fn 后还原。"""
    import shutil
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".holiday_tmp")
    os.makedirs(tmp, exist_ok=True)
    path = os.path.join(tmp, "节假日.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("日期,类型\n")
        for d, t in rows:
            f.write(f"{d},{t}\n")
    orig = config.HOLIDAY_FILE
    config.HOLIDAY_FILE = path
    models._holiday_map.cache_clear()
    try:
        return fn()
    finally:
        config.HOLIDAY_FILE = orig
        models._holiday_map.cache_clear()
        shutil.rmtree(tmp, ignore_errors=True)


def _ratio_with_weekday_season(n_days=84):
    """确定性周季节占比序列：周日最低(0.62)，周一最高(1.00)。"""
    idx = pd.date_range("2026-06-01", periods=n_days)  # 2026-06-01 为周一
    mult = {0: 1.00, 1: 1.05, 2: 1.02, 3: 0.98, 4: 0.92, 5: 0.78, 6: 0.62}
    vals = 0.05 * np.array([mult[w] for w in idx.weekday])
    return pd.Series(vals, index=idx)


def _future_dates(series, n=30):
    last = series.index.max()
    return [last + dt.timedelta(days=i) for i in range(1, n + 1)]


def _future_dates_from(last, n=30):
    return [last + dt.timedelta(days=i) for i in range(1, n + 1)]


def _make_history_df(n_days=70, total=1_000_000.0):
    dates = pd.date_range("2026-06-01", periods=n_days)
    x = np.linspace(0.0, 1.0, n_days)
    s_m1 = 0.15 - 0.10 * x           # M1 份额下降
    s_over = 0.35 + 0.10 * x         # over_30 份额上升
    s_out = 0.10 - 0.08 * x          # repay_3out 份额下降
    rest = 1.0 - (s_m1 + s_over + s_out)
    others = [t for t in config.CLIENT_TYPES if t not in ("M1", "over_30", "repay_3out")]
    share_of = {"M1": s_m1, "over_30": s_over, "repay_3out": s_out}
    for t in others:
        share_of[t] = rest / len(others)
    rows = []
    for j, d in enumerate(dates):
        for t in config.CLIENT_TYPES:
            rows.append({"date": d, "client_type": t,
                         "client_count": total * share_of[t][j]})
    return pd.DataFrame(rows)


def test_client_volumes_conservation():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    total_flat = df.groupby("date")["client_count"].sum().iloc[-config.TOTAL_WINDOW:].mean()
    for i in range(len(fd)):
        s = sum(vols[t][i] for t in config.CLIENT_TYPES)
        assert abs(s - total_flat) < 1e-6, f"day {i} 总量不守恒: {s} vs {total_flat}"


def test_client_volumes_non_negative_and_keys():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    assert set(vols.keys()) == set(config.CLIENT_TYPES)
    for t in config.CLIENT_TYPES:
        assert len(vols[t]) == len(fd)
        assert np.all(vols[t] >= 0)


def test_client_volumes_declining_share_stays_positive():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    # repay_3out 份额持续下降: 客户量不应塌缩到 0, 且整体递减
    assert np.all(vols["repay_3out"] > 0)
    assert vols["repay_3out"][-5:].mean() < vols["repay_3out"][:5].mean()


def test_client_volumes_rising_share():
    df = _make_history_df()
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    # over_30 份额上升: 客户量应递增
    assert vols["over_30"][-5:].mean() > vols["over_30"][:5].mean()


def test_client_volumes_zero_total_fallback():
    # 客户量全 0 时走兜底, 返回全 0 且长度正确, 不崩
    df = _make_history_df()
    df["client_count"] = 0.0
    fd = _future_dates_from(df["date"].max())
    vols = models.forecast_client_volumes(df, fd)
    for t in config.CLIENT_TYPES:
        assert len(vols[t]) == len(fd)
        assert np.all(vols[t] == 0.0)



def test_ratio_basic():
    s = make_series(70, level=1000.0, slope=0.0)
    r = (0.05 + s / s.max() * 0.02)  # 约 0.05~0.07，正数
    idx = s.index
    r = pd.Series(r.values, index=idx)
    out = models.forecast_ratio(r, _future_dates(s))
    assert len(out) == 30
    assert not np.any(np.isnan(out))
    assert np.all((out > 0) & (out <= 1.0))


def test_ratio_decreasing_trend():
    # 占比序列持续下滑，外推应低于历史起点
    idx = pd.date_range("2026-06-01", periods=70)
    vals = np.linspace(0.08, 0.02, 70)
    r = pd.Series(vals, index=idx)
    out = models.forecast_ratio(r, [idx.max() + dt.timedelta(days=i) for i in range(1, 31)])
    assert out.mean() < 0.06


def test_ratio_short_history_falls_back_to_last():
    idx = pd.date_range("2026-06-01", periods=10)
    r = pd.Series(np.full(10, 0.05), index=idx)
    fd = [idx.max() + dt.timedelta(days=i) for i in range(1, 6)]
    out = models.forecast_ratio(r, fd)
    assert len(out) == 5
    assert np.allclose(out, 0.05, atol=1e-6)


def test_mean_recent_ratio():
    idx = pd.date_range("2026-06-01", periods=20)
    s = pd.Series(np.full(20, 0.3), index=idx)
    assert abs(models.mean_recent_ratio(s, window=7) - 0.3) < 1e-9


def test_mean_recent_ratio_clamps():
    idx = pd.date_range("2026-06-01", periods=20)
    s = pd.Series(np.full(20, 1.5), index=idx)
    assert models.mean_recent_ratio(s, window=7) == 1.0


def test_ratio_holiday_monday_drops_toward_sunday():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)                       # 8/24 起 21 天
    d_mon = next(d for d in fd if d.weekday() == 0)  # 第一个周一
    i = fd.index(d_mon)
    plain = models.forecast_ratio(r, fd)

    def run_with_holiday():
        out = models.forecast_ratio(r, fd)
        assert out[i] < plain[i] * 0.9, "节假日周一应明显低于普通周一"
        assert 0.4 < out[i] / plain[i] < 0.95, "节假日周一应接近周日档(≈0.6x)"
        return out

    _with_holiday([(d_mon.date().isoformat(), "法定休假")], run_with_holiday)


def test_ratio_makeup_saturday_rises_toward_monday():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)
    d_sat = next(d for d in fd if d.weekday() == 5)  # 第一个周六
    i = fd.index(d_sat)
    plain = models.forecast_ratio(r, fd)

    def run_with_makeup():
        out = models.forecast_ratio(r, fd)
        assert out[i] > plain[i] * 1.05, "补班周六应明显高于普通周六"
        return out

    _with_holiday([(d_sat.date().isoformat(), "法定补班")], run_with_makeup)


def test_ratio_unflagged_dates_identical_with_and_without_calendar():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)  # 2026-08-24..09-13，无一落在真实/临时日历

    def run():
        return models.forecast_ratio(r, fd)

    base = run()  # 此刻 config.HOLIDAY_FILE 指向真实仓库文件（或缺失），fd 内无标记日
    # 临时日历只含无关日期 → 输出与无日历完全一致
    out = _with_holiday([("2030-01-01", "法定休假")], run)
    assert np.allclose(base, out, atol=1e-12)


def test_ratio_missing_holiday_file_falls_back():
    r = _ratio_with_weekday_season()
    fd = _future_dates(r, 21)

    def run():
        return models.forecast_ratio(r, fd)

    base = models.forecast_ratio(r, fd)

    def run_with_missing():
        return models.forecast_ratio(r, fd)

    import tempfile
    orig = config.HOLIDAY_FILE
    config.HOLIDAY_FILE = os.path.join(tempfile.gettempdir(), "definitely_missing_holidays.csv")
    models._holiday_map.cache_clear()
    try:
        out = run_with_missing()
    finally:
        config.HOLIDAY_FILE = orig
        models._holiday_map.cache_clear()
    assert np.allclose(base, out, atol=1e-12), "文件缺失应回退为现状行为"


def main():
    test_client_volumes_conservation()
    test_client_volumes_non_negative_and_keys()
    test_client_volumes_declining_share_stays_positive()
    test_client_volumes_rising_share()
    test_client_volumes_zero_total_fallback()
    test_ratio_basic()
    test_ratio_decreasing_trend()
    test_ratio_short_history_falls_back_to_last()
    test_mean_recent_ratio()
    test_mean_recent_ratio_clamps()
    test_ratio_holiday_monday_drops_toward_sunday()
    test_ratio_makeup_saturday_rises_toward_monday()
    test_ratio_unflagged_dates_identical_with_and_without_calendar()
    test_ratio_missing_holiday_file_falls_back()
    print("test_peakflow_models OK")


if __name__ == "__main__":
    main()
