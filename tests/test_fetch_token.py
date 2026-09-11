# -*- coding: utf-8 -*-
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fetch = importlib.import_module("抓取Token")


def test_read_login_from_env():
    creds = fetch.read_login({
        "AUTOWFM_CRM_USERNAME": "user",
        "AUTOWFM_CRM_PASSWORD": "password",
    })
    assert creds == ("user", "password")


def test_read_login_requires_both_values():
    for env in ({}, {"AUTOWFM_CRM_USERNAME": "user"}, {"AUTOWFM_CRM_PASSWORD": "password"}):
        try:
            fetch.read_login(env)
        except SystemExit as exc:
            assert "AUTOWFM_CRM_USERNAME" in str(exc)
            assert "AUTOWFM_CRM_PASSWORD" in str(exc)
        else:
            raise AssertionError("缺少凭据时应抛出 SystemExit")


if __name__ == "__main__":
    test_read_login_from_env()
    test_read_login_requires_both_values()
    print("test_fetch_token OK")
