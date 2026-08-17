import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import textwrap

import pandas as pd

from peakflow import loader

# Use workspace-local temp to avoid sandbox restrictions on system temp
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


def _write_csv(tmp_dir: str, content: str):
    p = os.path.join(tmp_dir, "输入.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return p


HEADER = ("日期\t客户类型\t客户量-实际\t客户量-预测\t进线次数-实际\t"
          "进线次数-预测\t转人工次数-实际\t转人工次数-预测")

OK_CSV = f"""\
{HEADER}
2026-06-04\tM1\t88,906\t\t790\t\t287\t
2026-06-04\tM2-M3\t120,577\t\t293\t\t124\t
2026-06-04\tM3+\t2,401,155\t\t680\t\t294\t
2026-06-04\t购买过权益卡且未逾期\t3,866,488\t\t10,111\t\t2,982\t
2026-06-04\tbehind_30\t168,738\t\t497\t\t151\t
2026-06-04\tover_30\t5,256,692\t\t266\t\t77\t
2026-06-04\trepay_3in\t146,373\t\t797\t\t260\t
2026-06-04\trepay_3out\t659,707\t\t605\t\t208\t
2026-06-04\t合计\t12,708,636\t\t14,039\t\t4,383\t
2026-06-05\tM1\t88,818\t\t910\t\t351\t
2026-06-05\tM2-M3\t120,034\t\t289\t\t128\t
2026-06-05\tM3+\t2,402,710\t\t771\t\t340\t
2026-06-05\t购买过权益卡且未逾期\t3,870,401\t\t9,949\t\t2,940\t
2026-06-05\tbehind_30\t169,908\t\t497\t\t123\t
2026-06-05\tover_30\t5,261,584\t\t266\t\t76\t
2026-06-05\trepay_3in\t146,482\t\t789\t\t260\t
2026-06-05\trepay_3out\t659,886\t\t599\t\t205\t
2026-06-05\t合计\t12,719,823\t\t14,070\t\t4,423\t
"""


def test_load_parses_and_drops_total():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        df = loader.load_channel_data(_write_csv(_WS_TMP, OK_CSV))
        assert list(df.columns) == ["date", "client_type", "client_count", "inbound", "transfer"]
        assert len(df) == 16
        assert df["client_type"].tolist()[:2] == ["M1", "M2-M3"]
        assert df.iloc[0]["client_count"] == 88906.0
        assert df.iloc[0]["inbound"] == 790.0
        assert df.iloc[0]["transfer"] == 287.0
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert "合计" not in df["client_type"].tolist()
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_load_parses_and_drops_total")


def test_load_detects_utf16():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        p = _write_csv(_WS_TMP, OK_CSV)
        data = open(p, "rb").read()
        bom = b"\xff\xfe"
        utf16 = bom + data.decode("utf-8").encode("utf-16-le")
        with open(p, "wb") as f:
            f.write(utf16)
        assert loader.detect_encoding(p) == "utf-16"
        df = loader.load_channel_data(p)
        assert len(df) == 16
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_load_detects_utf16")


def test_load_missing_type_raises():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        bad = OK_CSV.replace("2026-06-05\tM2-M3", "2026-06-05\tM1")
        try:
            loader.load_channel_data(_write_csv(_WS_TMP, bad))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "缺少客户类型" in str(e)
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_load_missing_type_raises")


def test_load_total_mismatch_raises():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        bad = OK_CSV.replace("12,708,636", "12,000,000")
        try:
            loader.load_channel_data(_write_csv(_WS_TMP, bad))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "合计" in str(e)
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_load_total_mismatch_raises")


if __name__ == "__main__":
    test_load_parses_and_drops_total()
    test_load_detects_utf16()
    test_load_missing_type_raises()
    test_load_total_mismatch_raises()
    print("\nAll tests passed!")
