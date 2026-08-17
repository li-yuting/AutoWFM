import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as dt
import html as html_mod
import json
import re
import shutil
from pathlib import Path

from peakflow import dashboard
from peakflow.forecast import three_band_forecast
from tests.helpers import make_history

_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def test_render_dashboard_embeds_forecast_data():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        history = make_history(35)
        future_dates = [dt.datetime(2026, 7, 6) + dt.timedelta(days=i)
                        for i in range(3)]
        forecast = three_band_forecast(history, future_dates)
        data = dashboard.build_dashboard_data(
            {"在线": history, "热线": history},
            {"在线": forecast, "热线": forecast},
            {"生成时间": "2026-08-13 12:00:00", "预测天数": 3},
        )
        out = Path(_WS_TMP) / "forecast.html"
        dashboard.write_dashboard(data, out)

        html = out.read_text(encoding="utf-8")
        assert "var FD =" in html
        assert "2026-07-06" in html
        assert '"forecast"' not in html
        assert "&quot;forecast&quot;" in html

        # Regression guard: the FD payload must decode to a single valid JSON
        # object. A too-greedy/early-terminated placeholder regex previously
        # spliced leftover template text after the object, breaking the script.
        m = re.search(r"var FD = ([^\n]*);\n", html)
        assert m, "FD payload line not found"
        payload = json.loads(html_mod.unescape(m.group(1)))
        assert payload["meta"]["horizon"] == 3
        assert len(payload["channels"]["在线"]["forecast"]) == 3
        assert payload["channels"]["在线"]["forecast"][0]["d"] == "2026-07-06"
        # 转人工三档数据 + 历史转人工字段
        assert len(payload["channels"]["在线"]["transfer"]) == 3
        assert payload["channels"]["在线"]["transfer"][0]["d"] == "2026-07-06"
        assert payload["channels"]["在线"]["history"][0]["tr"] >= 0
        assert "fd-transfer" in html
        assert "每日转人工" in html
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_render_dashboard_embeds_forecast_data")


def main():
    test_render_dashboard_embeds_forecast_data()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
