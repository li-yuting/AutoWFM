from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from peakflow.models import forecast_ratio


def _month_spike_ratio(dates):
    """仅月初 1-3 号冲高、无星期效应的占比序列。"""
    return pd.Series(
        [np.exp(0.6 if 1 <= d.day <= 3 else 0.0) for d in dates], index=dates
    )


def _flat_ratio(dates):
    """恒定占比序列(无星期、无月内效应)。"""
    return pd.Series(0.8, index=dates)


def test_month_seasonal_early_gt_late():
    dates = pd.date_range("2026-01-01", "2026-03-31", freq="D")
    s = _month_spike_ratio(dates)
    # 同为周六: 4-03(3号, 月初冲高) vs 4-24(24号, 平稳)
    future = [pd.Timestamp("2026-04-03"), pd.Timestamp("2026-04-24")]
    out = forecast_ratio(s, future, use_month_seasonal=True)
    assert out[0] > out[1], f"月初占比应高于月末: {out[0]:.3f} vs {out[1]:.3f}"


def test_month_seasonal_flat_on_equals_off():
    dates = pd.date_range("2026-01-01", "2026-03-31", freq="D")
    s = _flat_ratio(dates)
    future = pd.date_range("2026-04-01", "2026-04-30", freq="D")
    off = forecast_ratio(s, future, use_month_seasonal=False)
    on = forecast_ratio(s, future, use_month_seasonal=True)
    assert np.allclose(off, on), "恒定占比时开关应一致"


def test_month_seasonal_disabled_under_min_months():
    dates = pd.date_range("2026-01-01", "2026-01-25", freq="D")  # 仅1个月
    s = _month_spike_ratio(dates)
    future = [pd.Timestamp("2026-02-02"), pd.Timestamp("2026-02-23")]
    on = forecast_ratio(s, future, use_month_seasonal=True)
    off = forecast_ratio(s, future, use_month_seasonal=False)
    assert np.allclose(on, off), "不足 DOM_MIN_MONTHS 时应退化为无日序项"


if __name__ == "__main__":
    test_month_seasonal_early_gt_late()
    test_month_seasonal_flat_on_equals_off()
    test_month_seasonal_disabled_under_min_months()
    print("OK: all peakflow month-seasonality tests passed")
