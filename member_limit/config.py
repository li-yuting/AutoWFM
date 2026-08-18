# -*- coding: utf-8 -*-
"""member_limit 配置加载与校验。

读取 config.yaml 的 member_limit 段（url/limit/members/headless）与 .env 的
AUTOWFM_QCLOUD_ACCOUNT / AUTOWFM_QCLOUD_PASSWORD。凭据缺失或名单为空抛 ConfigError。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml


class ConfigError(Exception):
    """member_limit 配置缺失/非法。"""


DEFAULT_URL = "https://desk.qcloud.com/"
DEFAULT_LIMIT = 3
DEFAULT_HEADLESS = True


def _load_env() -> None:
    """加载 .env 到 os.environ（load_dotenv 默认不覆盖已设变量）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def load(config_path: str | Path = "config.yaml") -> dict:
    """读取并校验 member_limit 配置，返回：
    {"url": str, "account": str, "password": str,
     "limit": int, "members": list[str], "headless": bool}
    """
    _load_env()
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    section = cfg.get("member_limit") or {}
    account = (os.environ.get("AUTOWFM_QCLOUD_ACCOUNT") or "").strip()
    password = os.environ.get("AUTOWFM_QCLOUD_PASSWORD") or ""
    members = [str(m).strip() for m in (section.get("members") or []) if str(m).strip()]
    if not account or not password:
        raise ConfigError("缺少腾讯云凭据：请在 .env 设置 AUTOWFM_QCLOUD_ACCOUNT / AUTOWFM_QCLOUD_PASSWORD")
    if not members:
        raise ConfigError("member_limit.members 名单为空，请检查 config.yaml")
    return {
        "url": section.get("url", DEFAULT_URL),
        "account": account,
        "password": password,
        "limit": int(section.get("limit", DEFAULT_LIMIT)),
        "members": members,
        "headless": bool(section.get("headless", DEFAULT_HEADLESS)),
    }
