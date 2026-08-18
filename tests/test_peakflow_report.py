import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as dt
import shutil
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from peakflow import config, report
from tests.helpers import make_history

# Use workspace-local temp to avoid sandbox restrictions on system temp
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _future_dates(hist, n=10):
    last = hist["date"].max()
    return [last + dt.timedelta(days=i) for i in range(1, n + 1)]


def _sample():
    hist = make_history(35)
    fd = _future_dates(hist, 10)
    from peakflow.forecast import three_band_forecast, backtest_sigma
    df = three_band_forecast(hist, fd)
    sig = {"在线": backtest_sigma(hist), "热线": backtest_sigma(hist)}
    return df, sig


def test_write_report():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        df, sig = _sample()
        meta = {"生成时间": "2026-08-13 12:00:00", "预测天数": 10}
        out = Path(_WS_TMP) / "out.xlsx"
        report.write_report(df, df, sig, meta, out)
        wb = load_workbook(out)
        assert wb.sheetnames == ["总览", "在线-分类型明细", "热线-分类型明细", "回测误差", "运行说明"]
        ws = wb["总览"]
        assert ws.max_row > 10
        ws2 = wb["运行说明"]
        assert "预测天数" in str([c.value for c in ws2["A"]])
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_write_report")


def test_report_has_totals_in_detail():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        df, sig = _sample()
        out = Path(_WS_TMP) / "out.xlsx"
        report.write_report(df, df, sig, {}, out)
        wb = load_workbook(out)
        ws = wb["在线-分类型明细"]
        types = {ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)}
        assert "合计" in types
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_report_has_totals_in_detail")


def test_write_client_type_csv():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        df, sig = _sample()
        out = Path(_WS_TMP) / "client_types.csv"
        from peakflow.report import write_client_type_csv
        write_client_type_csv(df, df, out)
        assert out.exists()
        import csv
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2 * 8 * 10, f"预期 160 行，实际 {len(rows)}"
        assert rows[0].keys() == {"日期", "客户类型", "渠道", "预估进线量", "预估转人工量"}
        chs = {r["渠道"] for r in rows}
        assert chs == {"在线", "热线"}
        for r in rows:
            assert int(r["预估进线量"]) >= 0
            assert int(r["预估转人工量"]) >= 0
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_write_client_type_csv")


def main():
    test_write_report()
    test_report_has_totals_in_detail()
    test_write_client_type_csv()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
