# -*- coding: utf-8 -*-
"""存储层测试:验证 insert/ensure_index 写入与 SQLiteReadOnlyRepository 读取一致。"""
import sys, os, shutil, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector.repository import SCHEMAS, ensure_index, insert, SQLiteReadOnlyRepository

# 工作区内临时目录：避免沙箱对系统 temp / mkdtemp 的写入限制（同 test_peakflow_main.py）
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")

def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    d = os.path.join(_WS_TMP, f"t{os.getpid()}_{time.time_ns()}")
    os.makedirs(d)
    return d


def test_write_then_read():
    """insert 写入后,rows_in 能读到。"""
    d = _tmp()
    r = SQLiteReadOnlyRepository(d)
    vals = {"时间": "2026-07-27 09:05", "转人工量": 10, "接通量": 9,
            "排队量": 0, "累计呼入量": 80, "外呼量": 0, "外呼接通量": 0}
    insert("热线", vals, d)
    rows, cols = r.rows_in("热线", "2026-07-27")
    assert len(rows) == 1, f"应读到 1 行, 实际 {len(rows)}"
    assert cols == SCHEMAS["热线"], cols
    row = dict(zip(cols, rows[0]))
    assert row["转人工量"] == 10
    assert row["时间"] == "2026-07-27 09:05"


def test_read_empty():
    """无表/无数据返回 ([], [])。"""
    d = _tmp()
    r = SQLiteReadOnlyRepository(d)
    rows, cols = r.rows_in("热线", "2026-07-27")
    assert rows == [] and cols == [], (rows, cols)


def test_latest_date():
    """latest_date 取热线/在线最新日期;无数据回落今天。"""
    import datetime
    d = _tmp()
    r = SQLiteReadOnlyRepository(d)
    # 无数据 -> 今天
    assert r.latest_date() == datetime.date.today().strftime("%Y-%m-%d")
    # 写入两条不同日期
    insert("热线", {"时间": "2026-07-27 09:05", "转人工量": 1, "接通量": 1,
                    "排队量": 0, "累计呼入量": 1, "外呼量": 0, "外呼接通量": 0}, d)
    insert("热线", {"时间": "2026-07-28 09:05", "转人工量": 2, "接通量": 2,
                    "排队量": 0, "累计呼入量": 2, "外呼量": 0, "外呼接通量": 0}, d)
    assert r.latest_date() == "2026-07-28", r.latest_date()


def test_ensure_index_idempotent():
    """ensure_index 幂等,多次调用不报错;建索引前无库也能建。"""
    d = _tmp()
    ensure_index("热线", d)
    ensure_index("热线", d)  # 重复调用不报错
    insert("热线", {"时间": "2026-07-27 09:05", "转人工量": 1, "接通量": 1,
                    "排队量": 0, "累计呼入量": 1, "外呼量": 0, "外呼接通量": 0}, d)
    r = SQLiteReadOnlyRepository(d)
    rows, _ = r.rows_in("热线", "2026-07-27")
    assert len(rows) == 1


def test_month_prefix():
    """rows_in 用月前缀(YYYY-MM)能取整月。"""
    d = _tmp()
    insert("热线", {"时间": "2026-07-01 09:05", "转人工量": 1, "接通量": 1,
                    "排队量": 0, "累计呼入量": 1, "外呼量": 0, "外呼接通量": 0}, d)
    insert("热线", {"时间": "2026-07-15 09:05", "转人工量": 2, "接通量": 2,
                    "排队量": 0, "累计呼入量": 2, "外呼量": 0, "外呼接通量": 0}, d)
    insert("热线", {"时间": "2026-08-01 09:05", "转人工量": 3, "接通量": 3,
                    "排队量": 0, "累计呼入量": 3, "外呼量": 0, "外呼接通量": 0}, d)
    r = SQLiteReadOnlyRepository(d)
    rows, _ = r.rows_in("热线", "2026-07")
    assert len(rows) == 2, f"7月应有2行, 实际 {len(rows)}"


def test_other_source_table_created():
    """insert 会按 SCHEMAS 建表,非热线源同样可写可读。"""
    d = _tmp()
    insert("在线", {"时间": "2026-07-27 09:05", "转人工量": 1, "转人工失败": 0, "排队": 0,
                    "咨询": 0, "在线": 2, "小休": 1, "示忙": 1, "话后": 0,
                    "就餐": 0, "培训": 0, "回访": 0}, d)
    assert os.path.exists(os.path.join(d, "在线.db"))
    r = SQLiteReadOnlyRepository(d)
    rows, cols = r.rows_in("在线", "2026-07-27")
    assert cols == SCHEMAS["在线"], cols
    assert len(rows) == 1


def main():
    test_write_then_read()
    test_read_empty()
    test_latest_date()
    test_ensure_index_idempotent()
    test_month_prefix()
    test_other_source_table_created()
    shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("repository OK")


if __name__ == "__main__":
    main()
