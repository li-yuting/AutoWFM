# -*- coding: utf-8 -*-
"""公钥常量(由 tools/gen_license.py 生成,仅含公钥,可随 exe 分发)。"""
from __future__ import annotations
import base64

PUB_KEY_B64 = b"MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAp9wmCnyL0qq6jRmpYG+g" \
    b"O3Wj2PaqwUkcDCHWpUPF4CeSh3WToQ1Yjk7VjWSnMLsz9MbFJop0By96VQ3mYuPK" \
    b"w5h2vkAvFgMhE5taMbs0yXzjElrTDCrOUSZkyVUMMr4f2F/tJZ7HpOLCsPMD1RZ0" \
    b"SM95MdHC3s+gHq/PkCSZajY/1EF1I7bFqXxADYcMhbTz0yKjb3RWHcZyke3fW6Id" \
    b"1fvCBbWsEp804pJqWiea8RJBvzU2Rfz6ODdpFcptWepG5Dlwdz17tFbUi9G4q/EX" \
    b"aqFZMRmnP1ZYHdhR4EFPXek7s6vzC5AaWjOe9gnnd4MvJzrHQRyKdmzmg8LvRotK" \
    b"ux6SSuOTQQ2qef3B/u2f9DPr6fyn/5dZxpGc3TY1/LKOWBX5sne+Ac2iZpqpTc63" \
    b"kMiDh+tBD055J8uPekoXPycALPHfszkWknPJqD3jaPK38QCJD0yLq47453xhECFW" \
    b"/CFkAed5/Av+ucpo2911qt6OUEZHpgNMc8IJeyBTAZb9AgMBAAE="

def public_key_der() -> bytes:
    return base64.b64decode(PUB_KEY_B64)
