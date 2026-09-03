import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from peakflow import models
from peakflow import config
from tests.helpers import make_series


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


def main():
    test_client_volumes_conservation()
    test_client_volumes_non_negative_and_keys()
    test_client_volumes_declining_share_stays_positive()
    test_client_volumes_rising_share()
    test_ratio_basic()
    test_ratio_decreasing_trend()
    test_ratio_short_history_falls_back_to_last()
    test_mean_recent_ratio()
    test_mean_recent_ratio_clamps()
    print("test_peakflow_models OK")


if __name__ == "__main__":
    main()
