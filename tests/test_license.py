# -*- coding: utf-8 -*-
"""collector.license 秘钥校验测试:plain assert,直接 `python tests/test_license.py`。

自包含:不依赖开发机私钥。用临时 RSA 密钥对签发 key,并 patch license 的内嵌公钥
与日期提供器,验证 is_activated 与 check_license 的日期边界分支。
"""
import base64
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding

from collector import license as L


def _make_keypair():
    """生成临时 RSA-3072 密钥对,返回 (sign_fn, pub_pem)。"""
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pub = private.public_key()
    def _sign(key: str) -> str:
        payload = base64.b64decode("QVVUT1dGTS1MSUNFTlNFLXYx")  # AUTOWFM-LICENSE-v1
        sig = private.sign(
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return "AUTOWFM-" + base64.urlsafe_b64encode(sig).decode().rstrip("=")
    pub_der = pub.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return _sign, pub_der


def _patch_public_key(pub_der: bytes):
    """把 license 的内嵌公钥替换为临时公钥。"""
    pub = serialization.load_der_public_key(pub_der)
    L._license_public_key = lambda: pub


def _test_is_activated():
    sign, pub_der = _make_keypair()
    _patch_public_key(pub_der)
    good = sign("x")
    assert L.is_activated(good) is True, "有效秘钥应通过"
    assert L.is_activated("AUTOWFM-" + "A" * len(good.split("-", 1)[1])) is False, "错误签名应拒绝"
    assert L.is_activated(None) is False, "None 应拒绝"
    assert L.is_activated("") is False, "空串应拒绝"
    assert L.is_activated("BADKEY") is False, "无前缀应拒绝"
    assert L.is_activated("AUTOWFM-###notbase64###") is False, "坏 base64 应拒绝"
    print("test_is_activated OK")


def _test_date_boundary():
    # 2026-09-30 (cutoff 前一天) -> 放行
    L._date_provider = lambda: datetime.date(2026, 9, 30)
    assert L.check_license() is True, "cutoff 前应放行"
    # 2026-10-01 (cutoff 当天) -> 拦截
    L._date_provider = lambda: datetime.date(2026, 10, 1)
    assert L.check_license() is False, "cutoff 当天应拦截"
    # 2026-10-02 -> 拦截
    L._date_provider = lambda: datetime.date(2026, 10, 2)
    assert L.check_license() is False, "cutoff 后应拦截"
    # 恢复默认
    L._date_provider = datetime.date.today
    print("test_date_boundary OK")


def _test_ui_expiry():
    assert L.ui_expiry_date() == "2026-10-01", "ui_expiry_date 应返回 2026-10-01"
    print("test_ui_expiry OK")


def main():
    _test_ui_expiry()
    _test_is_activated()
    _test_date_boundary()
    print("ALL license tests OK")


if __name__ == "__main__":
    main()