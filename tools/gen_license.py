# -*- coding: utf-8 -*-
"""秘钥签发工具(仅开发机使用,不随包分发)。

用法:
    python tools/gen_license.py init      # 首次:生成 RSA-3072 密钥对,私钥加密存 tools/license_private.pem,
                                          #       公钥写入 collector/license_public_key.py
    python tools/gen_license.py issue     # 签发一个静态通用秘钥,打印 AUTOWFM-XXXX-XXXX-XXXX-XXXX
    python tools/gen_license.py verify <key>   # 用私钥侧校验一个秘钥(自测用)

设计要点:
- RSA 签名: 私钥签,公钥验。私钥只存在于开发机,跨平台可移植(exe 内仅含公钥)。
- 静态通用秘钥: 所有机器同一把,签名消息固定为 LICENSE_PAYLOAD。
- 私钥用口令加密存储,避免明文泄露。
"""
from __future__ import annotations

import base64
import getpass
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding

TOOLS_DIR = Path(__file__).resolve().parent
PRIVATE_PEM = TOOLS_DIR / "license_private.pem"
PUBLIC_PY = Path(__file__).resolve().parent.parent / "collector" / "license_public_key.py"

# 被签名的固定消息(静态通用秘钥的载荷)。
LICENSE_PAYLOAD = b"AUTOWFM-LICENSE-v1"
# 秘钥显示前缀 + 分段间的分隔符。
KEY_PREFIX = "AUTOWFM"
KEY_GROUPS = 4          # 4 段
KEY_PER_GROUP = 4       # 每段 4 字符
# 用 base64 拆散常量字符串,避免明文特征被静态扫描一眼看出。
_B64 = "QVVUT1dGTS1MSUNFTlNFLXYx"  # "AUTOWFM-LICENSE-v1"


def _payload() -> bytes:
    return base64.b64decode(_B64)


def _load_or_create_private() -> rsa.RSAPrivateKey:
    """加载私钥;不存在则生成并加密保存。"""
    if PRIVATE_PEM.exists():
        pw = _ask_passphrase("请输入私钥口令")
        try:
            with open(PRIVATE_PEM, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=pw.encode())
        except Exception as exc:
            sys.exit(f"私钥口令错误或文件损坏: {exc}")
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pw = _ask_passphrase("设置私钥口令(请牢记,用于签发)")
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(pw.encode()),
    )
    PRIVATE_PEM.write_bytes(pem)
    print(f"私钥已保存: {PRIVATE_PEM}")
    return private


def _ask_passphrase(prompt: str) -> str:
    # 非交互测试/CI 用环境变量注入口令。
    env = os.environ.get("AUTOWFM_LICENSE_PASSPHRASE")
    if env:
        return env
    try:
        return getpass.getpass(prompt + ": ")
    except Exception:
        return input(prompt + ": ")


def _write_public_py(private: rsa.RSAPrivateKey) -> None:
    """把公钥 DER base64 写进 collector/license_public_key.py(打进 exe 的常量)。"""
    pub_der = private.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_b64 = base64.b64encode(pub_der).decode()
    # 把 base64 串拆成多行 b"..." 片段,用 + 连接(避免单行长字符串特征)。
    chunks = [pub_b64[i:i+64] for i in range(0, len(pub_b64), 64)]
    joined = " \\\n    ".join(f'b"{c}"' for c in chunks)
    content = (
        "# -*- coding: utf-8 -*-\n"
        '"""公钥常量(由 tools/gen_license.py 生成,仅含公钥,可随 exe 分发)。"""\n'
        "from __future__ import annotations\n"
        "import base64\n\n"
        f"PUB_KEY_B64 = {joined}\n\n"
        "def public_key_der() -> bytes:\n"
        "    return base64.b64decode(PUB_KEY_B64)\n"
    )
    PUBLIC_PY.write_text(content, encoding="utf-8")
    print(f"公钥已写入: {PUBLIC_PY}")


def _sign_key(private: rsa.RSAPrivateKey) -> str:
    """对固定载荷签名，编码为秘钥字符串(完整签名 base64url)。"""
    sig = private.sign(
        _payload(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{KEY_PREFIX}-{b64}"


def _verify_with_private(private: rsa.RSAPrivateKey, key: str) -> bool:
    """用公钥验签(开发机自测)。"""
    from cryptography.exceptions import InvalidSignature
    try:
        b64 = key.split(KEY_PREFIX + "-", 1)[1]
        sig = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        private.public_key().verify(
            sig,
            _payload(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "init":
        private = _load_or_create_private()
        _write_public_py(private)
        print("密钥对已就绪。用 'issue' 签发秘钥。")
    elif cmd == "issue":
        private = _load_or_create_private()
        print("签发秘钥: " + _sign_key(private))
    elif cmd == "verify":
        if len(sys.argv) < 3:
            sys.exit("用法: python tools/gen_license.py verify <key>")
        private = _load_or_create_private()
        print("有效" if _verify_with_private(private, sys.argv[2]) else "无效")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()