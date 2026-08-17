import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as dt

import numpy as np
import pandas as pd

from peakflow import config, forecast
from tests.helpers import make_history


def _future_dates(hist, n=30):
    last = hist["date"].max()
    return [last + dt.timedelta(days=i) for i in range(1, n + 1)]


def test_point_forecast_shape():
    hist = make_history(35)
    fd = _future_dates(hist)
    out = forecast.point_forecast(hist, fd)
    assert len(out) == len(config.CLIENT_TYPES) * len(fd)
    assert set(out["client_type"]) == set(config.CLIENT_TYPES)
    assert set(out.columns) == {"date", "client_type", "client_vol", "ratio", "inbound", "transfer"}
    assert not out["inbound"].isna().any()
    assert (out["inbound"] >= 0).all()
    print("PASS test_point_forecast_shape")


def test_point_forecast_formula():
    hist = make_history(35, transfer_rate=0.3)
    fd = _future_dates(hist)
    out = forecast.point_forecast(hist, fd)
    m1 = out[out["client_type"] == "M1"].iloc[0]
    assert np.allclose(m1["inbound"], m1["client_vol"] * m1["ratio"])
    assert np.allclose(m1["transfer"], m1["inbound"] * 0.3)
    print("PASS test_point_forecast_formula")


def test_total_by_date():
    hist = make_history(35)
    fd = _future_dates(hist)
    out = forecast.point_forecast(hist, fd)
    tot = forecast.total_by_date(out)
    assert len(tot) == len(fd)
    d0 = fd[0]
    sub = out[out["date"] == d0]
    row = tot[tot["date"] == d0].iloc[0]
    assert np.allclose(row["inbound"], sub["inbound"].sum())
    print("PASS test_total_by_date")


def test_weekly_summary():
    hist = make_history(35)
    fd = _future_dates(hist)
    out = forecast.point_forecast(hist, fd)
    tot = forecast.total_by_date(out)
    wk = forecast.weekly_summary(tot)
    assert set(wk.columns) == {"week", "inbound", "transfer"}
    assert np.allclose(wk["inbound"].sum(), tot["inbound"].sum())
    print("PASS test_weekly_summary")


def test_backtest_sigma():
    hist = make_history(40)
    sigma = forecast.backtest_sigma(hist)
    assert set(sigma.keys()) == set(config.CLIENT_TYPES)
    for t, v in sigma.items():
        assert v["sigma_in"] >= 0
        assert v["sigma_tr"] >= 0
        assert v["mape"] >= 0
    print("PASS test_backtest_sigma")


def test_three_band_forecast():
    hist = make_history(40)
    fd = _future_dates(hist)
    out = forecast.three_band_forecast(hist, fd)
    cols = {"date", "client_type", "client_vol", "ratio", "inbound",
            "inbound_low", "inbound_high", "transfer", "transfer_low", "transfer_high"}
    assert set(out.columns) == cols
    assert (out["inbound_low"] <= out["inbound"]).all()
    assert (out["inbound"] <= out["inbound_high"]).all()
    assert (out["transfer_low"] <= out["transfer"]).all()
    assert (out["transfer"] <= out["transfer_high"]).all()
    assert not out.isna().any().any()
    print("PASS test_three_band_forecast")


def main():
    test_point_forecast_shape()
    test_point_forecast_formula()
    test_total_by_date()
    test_weekly_summary()
    test_backtest_sigma()
    test_three_band_forecast()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
