# -*- coding: utf-8 -*-
"""member_limit 纯逻辑测试（不依赖浏览器）：plain assert，直接运行。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from member_limit.core import (classify_member, classify_edit_result,
                                   build_summary, format_summary)


def test_classify_member():
    assert classify_member("3", 3) == "already"
    assert classify_member(" 3 ", 3) == "already"
    assert classify_member("4", 3) == "change"
    assert classify_member("", 3) == "change"


def test_classify_edit_result():
    # 核心回归:headless 下 dialog 计数可能永不归零(ok=False),但回读值已更新 -> 必须判成功
    assert classify_edit_result(False, "3", 3) == "success"
    assert classify_edit_result(False, " 3 ", 3) == "success"
    assert classify_edit_result(True, "3", 3) == "success"
    assert classify_edit_result(True, "2", 3) == "unverified"
    assert classify_edit_result(False, "2", 3) == "failed"
    assert classify_edit_result(False, None, 3) == "failed"   # 回读找不到 -> 非成功
    assert classify_edit_result(True, None, 3) == "unverified"
    print("classify_edit_result OK")


def test_build_summary():
    s = build_summary([("甲", "4", "3")], [("乙", 3)], [], ["丙"], ["丁"], False, False)
    assert s["changed"] == [("甲", "4", "3")]
    assert s["already"] == [("乙", 3)]
    assert s["failed"] == ["丙"]
    assert s["not_found"] == ["丁"]
    assert s["cancelled"] is False
    assert s["dry_run"] is False


def test_format_summary_cancelled():
    s = build_summary([("甲", "4", "3")], [], [], [], [], True, False)
    text = format_summary(s)
    assert "共处理 1 人" in text
    assert "[已取消]" in text
    assert "甲(4->3)" in text


def test_format_summary_dry_run():
    s = build_summary([], [], [], [], [], False, True)
    text = format_summary(s)
    assert "[DRY-RUN]" in text
    assert "[修改成功] 0 人：无" in text


def main():
    test_classify_member()
    test_classify_edit_result()
    test_build_summary()
    test_format_summary_cancelled()
    test_format_summary_dry_run()
    print("test_member_limit_core OK")


if __name__ == "__main__":
    main()
