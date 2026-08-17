import datetime as dt
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from peakflow import models
from tests.helpers import make_series


def _future_dates(series, n=30):
    last = series.index.max()
    return [last + dt.timedelta(days=i) for i in range(1, n + 1)]


def test_client_volume_basic():
    s = make_series(70, level=100.0, slope=0.5)
    out = models.forecast_client_volume(s, _future_dates(s))
    assert len(out) == 30
    assert not np.any(np.isnan(out))
    assert np.all(out >= 0)


def test_client_volume_follows_trend_and_weekday():
    s = make_series(70, level=100.0, slope=1.0)
    fd = _future_dates(s)
    out = models.forecast_client_volume(s, fd)
    assert out[-1] > out[0]
    wd = [d.weekday() for d in fd[:7]]
    sat = fd[wd.index(5)]
    sun = fd[wd.index(6)]
    mon = fd[wd.index(0)]
    o = {d: out[i] for i, d in enumerate(fd)}
    assert o[mon] > o[sat] and o[mon] > o[sun]


def test_client_volume_short_history_raises():
    s = make_series(10, level=100.0)
    raised = False
    try:
        models.forecast_client_volume(s, _future_dates(s))
    except ValueError as e:
        assert "历史不足" in str(e)
        raised = True
    assert raised, "Expected ValueError was not raised"


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
    test_client_volume_basic()
    test_client_volume_follows_trend_and_weekday()
    test_client_volume_short_history_raises()
    test_ratio_basic()
    test_ratio_decreasing_trend()
    test_ratio_short_history_falls_back_to_last()
    test_mean_recent_ratio()
    test_mean_recent_ratio_clamps()
    print("test_peakflow_models OK")


if __name__ == "__main__":
    main()
