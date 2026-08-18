# -*- coding: utf-8 -*-
"""member_limit 配置加载/校验测试：plain assert，直接运行。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shutil
from contextlib import contextmanager
from pathlib import Path
from member_limit.config import ConfigError, load

# Use workspace-local temp to avoid sandbox restrictions on system temp
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


@contextmanager
def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        yield Path(_WS_TMP)
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)


def _set_env(account="a@b.com", password="pwd"):
    os.environ["AUTOWFM_QCLOUD_ACCOUNT"] = account
    os.environ["AUTOWFM_QCLOUD_PASSWORD"] = password


def test_load_ok():
    with _tmp() as td:
        _set_env()
        p = td / "config.yaml"
        p.write_text(
            "member_limit:\n"
            "  url: 'https://desk.qcloud.com/'\n"
            "  limit: 3\n"
            "  headless: true\n"
            "  members:\n"
            "    - '甲'\n"
            "    - '乙'\n",
            encoding="utf-8")
        cfg = load(p)
        assert cfg["url"] == "https://desk.qcloud.com/"
        assert cfg["limit"] == 3
        assert cfg["members"] == ["甲", "乙"]
        assert cfg["headless"] is True
        assert cfg["account"] == "a@b.com"
        assert cfg["password"] == "pwd"


def test_load_missing_credentials():
    with _tmp() as td:
        os.environ.pop("AUTOWFM_QCLOUD_ACCOUNT", None)
        os.environ.pop("AUTOWFM_QCLOUD_PASSWORD", None)
        p = td / "config.yaml"
        p.write_text("member_limit:\n  members: ['甲']\n", encoding="utf-8")
        try:
            load(p)
            assert False, "缺少凭据应抛 ConfigError"
        except ConfigError as e:
            assert "AUTOWFM_QCLOUD_ACCOUNT" in str(e)


def test_load_empty_members():
    with _tmp() as td:
        _set_env()
        p = td / "config.yaml"
        p.write_text("member_limit:\n  members: []\n", encoding="utf-8")
        try:
            load(p)
            assert False, "空名单应抛 ConfigError"
        except ConfigError as e:
            assert "名单为空" in str(e)


def test_load_defaults():
    with _tmp() as td:
        _set_env()
        p = td / "config.yaml"
        p.write_text("member_limit:\n  members: ['甲']\n", encoding="utf-8")
        cfg = load(p)
        assert cfg["limit"] == 3
        assert cfg["headless"] is True
        assert cfg["url"] == "https://desk.qcloud.com/"


def main():
    test_load_ok()
    test_load_missing_credentials()
    test_load_empty_members()
    test_load_defaults()
    print("test_member_limit_config OK")


if __name__ == "__main__":
    main()
