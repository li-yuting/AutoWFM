# -*- coding: utf-8 -*-
"""秘钥校验模块(打进 exe)。

职责:
- 判断系统日期是否 >= 授权起始日(2026-10-01),是则要求秘钥激活。
- 用内嵌公钥验签秘钥(私钥只在开发机,exe 内仅公钥)。

校验结果由调用方(manager.py)决定是否放行;本模块不直接启动/拦截任何功能,
只返回 "是否已激活"。混淆加固说明见模块内注释。
"""
from __future__ import annotations

import base64
import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# 授权起始日,拆散存储避免明文特征被静态扫描一眼识别。
# 2026-10-01 -> 2026 / 10 / 01
_YEAR = 20 * 100 + 26      # 2026
_MONTH = 3 + 7             # 10
_DAY = 0 + 1               # 01

# 兼容测试注入:允许覆盖 "当前日期" 以便测试日期分支(生产不用)。
_date_provider = datetime.date.today


def _license_public_key() -> object:
    """惰性加载内嵌公钥(避免 import 时解析开销)。"""
    from collector import license_public_key as _lpk
    return serialization.load_der_public_key(_lpk.public_key_der())


def ui_expiry_date() -> str:
    """返回授权起始日字符串,供 UI 提示用。"""
    return f"{_YEAR}-{_MONTH:02d}-{_DAY:02d}"


def _is_after_cutoff(now: datetime.date) -> bool:
    """系统日期 >= 授权起始日则需激活。"""
    cutoff = datetime.date(_YEAR, _MONTH, _DAY)
    return now >= cutoff


def is_activated(key: str | None) -> bool:
    """RSA-PSS 验签秘钥。key 为 None 或格式错误返回 False。"""
    if not key:
        return False
    key = key.strip()
    # 秘钥前缀(拆散对比,避免明文特征)。
    prefix = "AUTOW" + "F" + "M"
    if not key.startswith(prefix + "-"):
        return False
    try:
        b64 = key.split(prefix + "-", 1)[1]
        sig = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        pub = _license_public_key()
        pub.verify(
            sig,
            base64.b64decode("QVVUT1dGTS1MSUNFTlNFLXYx"),  # "AUTOWFM-LICENSE-v1"
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def check_license() -> bool:
    """聚合入口:判定当前是否已激活(放行)。

    - 系统日期 < 授权起始日:直接放行(不需秘钥)。
    - 系统日期 >= 授权起始日:必须秘钥且验签通过才放行。
    """
    now = _date_provider()
    if not _is_after_cutoff(now):
        return True
    return False  # 未注入秘钥,由调用方弹窗收集后再 is_activated