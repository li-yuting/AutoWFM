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
    df, _ = three_band_forecast(hist, fd)
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


def test_overview_notes_statutory_holidays():
    os.makedirs(_WS_TMP, exist_ok=True)
    import tempfile
    from peakflow import models
    try:
        df, sig = _sample()
        # 预测日期约 2026-07-06..07-15；标记首日为首个法定休假日
        fd_dates = sorted(set(df["date"]))
        target = fd_dates[0].date()
        tmp_dir = os.path.join(_WS_TMP, "holiday")
        os.makedirs(tmp_dir, exist_ok=True)
        path = os.path.join(tmp_dir, "节假日.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"日期,类型\n{target},法定三薪\n")
        orig = config.HOLIDAY_FILE
        config.HOLIDAY_FILE = path
        models._holiday_map.cache_clear()
        try:
            out = Path(_WS_TMP) / "out_note.xlsx"
            report.write_report(df, df, sig, {}, out)
            wb = load_workbook(out)
            ws = wb["总览"]
            headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
            assert headers[-1] == "备注", f"备注应是最右列, got {headers}"
            col_date = headers.index("日期") + 1
            col_note = len(headers)
            found = False
            for r in range(2, ws.max_row + 1):
                cv = ws.cell(row=r, column=col_date).value
                if cv is None or "周汇总" in str(cv):
                    continue
                d = cv.date() if hasattr(cv, "date") else cv
                note = ws.cell(row=r, column=col_note).value
                if d == target:
                    assert note == "法定三薪"
                    found = True
                else:
                    assert note in ("", None), f"{cv} 不应有备注"
            assert found, "未找到标记日期行"
        finally:
            config.HOLIDAY_FILE = orig
            models._holiday_map.cache_clear()
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_overview_notes_statutory_holidays")


def main():
    test_write_report()
    test_report_has_totals_in_detail()
    test_overview_notes_statutory_holidays()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
