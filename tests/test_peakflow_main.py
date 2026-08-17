import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as dt
import shutil
from pathlib import Path

from peakflow import config, main as main_mod

# Use workspace-local temp to avoid sandbox restrictions on system temp
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _make_test_data(data_dir: Path):
    """Write 45 days × 8 client types of tab-separated CSV data."""
    for name in ["在线各类用户.csv", "热线各类用户.csv"]:
        p = data_dir / name
        p.write_text(
            "日期\t客户类型\t客户量-实际\t进线次数-实际\t转人工次数-实际\n",
            encoding="utf-8")
        with open(p, "a", encoding="utf-8") as fh:
            for d in range(45):
                for t in ["M1", "M2-M3", "M3+", "购买过权益卡且未逾期",
                          "behind_30", "over_30", "repay_3in", "repay_3out"]:
                    day = (dt.date(2026, 6, 1) + dt.timedelta(days=d)).isoformat()
                    fh.write(f"{day}\t{t}\t{100000 + d}\t{100 + d}\t{30 + d}\n")


def test_run_forecast_produces_excel():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        data_dir = Path(_WS_TMP) / "data"
        data_dir.mkdir()
        _make_test_data(data_dir)

        # Patch config.DATA_DIR to point to test data
        old_data_dir = config.DATA_DIR
        config.DATA_DIR = data_dir
        try:
            out = main_mod.run_forecast(fetch=False, out_dir=Path(_WS_TMP) / "out")
            assert Path(out).exists()
            assert Path(out).suffix == ".xlsx"
            assert Path(out).parent.name == dt.date.today().isoformat()
            html = Path(out).with_suffix(".html")
            assert html.exists()
            assert "var FD =" in html.read_text(encoding="utf-8")
        finally:
            config.DATA_DIR = old_data_dir
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_run_forecast_produces_excel")


def main():
    test_run_forecast_produces_excel()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()