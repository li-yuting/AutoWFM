# -*- coding: utf-8 -*-
"""member_limit CLI 测试（monkeypatch core.run_member_limit，不启动浏览器）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch
from member_limit import main as cli
from member_limit.core import build_summary
from member_limit.config import ConfigError

_FAKE_CONFIG = {
    "url": "https://desk.qcloud.com/", "account": "a@b.com", "password": "pwd",
    "limit": 3, "members": ["甲"], "headless": True,
}


def test_main_dry_run_flag():
    captured = {}

    def fake_run(config, progress_cb=None, should_cancel=None, dry_run=False):
        captured["dry_run"] = dry_run
        captured["limit"] = config["limit"]
        return build_summary([], [], [], [], [], False, dry_run)

    with patch("member_limit.main.load_config", return_value=dict(_FAKE_CONFIG)), \
         patch("member_limit.core.run_member_limit", side_effect=fake_run) as m:
        rc = cli.main(["--limit", "5", "--dry-run"])
    assert rc == 0
    assert captured["dry_run"] is True
    assert captured["limit"] == 5
    assert m.call_count == 1


def test_main_default_limit():
    captured = {}

    def fake_run(config, progress_cb=None, should_cancel=None, dry_run=False):
        captured["limit"] = config["limit"]
        return build_summary([], [], [], [], [], False, False)

    with patch("member_limit.main.load_config", return_value=dict(_FAKE_CONFIG)), \
         patch("member_limit.core.run_member_limit", side_effect=fake_run):
        rc = cli.main([])
    assert rc == 0
    assert captured["limit"] == 3


def test_main_config_error():
    with patch("member_limit.main.load_config", side_effect=ConfigError("缺少凭据")):
        rc = cli.main([])
    assert rc == 2


def main():
    test_main_dry_run_flag()
    test_main_default_limit()
    test_main_config_error()
    print("test_member_limit_main OK")


if __name__ == "__main__":
    main()
