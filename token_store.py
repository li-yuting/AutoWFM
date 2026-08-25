# -*- coding: utf-8 -*-
"""Token 读取/保存/脱敏/解析的纯函数模块，以及失效自动刷新辅助。

token.json 由 抓取Token.py 生成；.env 仍是主 token 来源（AUTOWFM_TOKEN）。
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOKEN_FILE = ROOT / "token.json"
ENV_FILE = ROOT / ".env"
FETCH_SCRIPT = ROOT / "抓取Token.py"

_ENV_TOKEN_LINE = "AUTOWFM_TOKEN"


def mask_token(token):
    if not token:
        return "(空)"
    if len(token) <= 10:
        return token[:2] + "***"
    return token[:6] + "***" + token[-4:]


def extract_token_from_post_data(data):
    if not data:
        return None
    try:
        obj = json.loads(data)
    except (ValueError, TypeError):
        return None
    tok = obj.get("token") if isinstance(obj, dict) else None
    if isinstance(tok, str) and tok.strip():
        return tok.strip()
    return None


def save_token(path, token):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"token": token, "captured_at": datetime.now().isoformat(timespec="seconds")},
            f,
            ensure_ascii=False,
            indent=2,
        )


def load_token(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    tok = data.get("token") if isinstance(data, dict) else None
    if isinstance(tok, str) and tok.strip():
        return tok.strip()
    return None


def update_env_token(token, env_path=None):
    """把新 token 写回 .env 的 AUTOWFM_TOKEN（新增或替换该行），并返回 True。

    保持 .env 其他行不变；不存在时补一行；全程不打印/记录 token 明文。
    """
    if env_path is None:
        env_path = ENV_FILE
    env_path = Path(env_path)
    lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    seen = False
    out = []
    for line in lines:
        key = (line.split("=", 1)[0] if "=" in line else line).strip()
        if key == _ENV_TOKEN_LINE:
            out.append(f"{_ENV_TOKEN_LINE}={token}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{_ENV_TOKEN_LINE}={token}")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return True


def refresh_token(timeout=180):
    """无头运行 抓取Token.py 刷新 token，成功后返回新 token，失败返回 None。"""
    cmd = [sys.executable, str(FETCH_SCRIPT), "--headless"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
        if proc.returncode != 0:
            print(f"[token] 刷新失败(exit={proc.returncode}): {proc.stdout[-300:]}")
            return None
    except Exception as exc:
        print(f"[token] 刷新失败: {exc}")
        return None
    token = load_token(TOKEN_FILE)
    return token


def selftest():
    assert mask_token("") == "(空)"
    assert mask_token("abcdefghij") == "ab***"
    assert mask_token("USER_TOKEN_KEYabcdefgh") == "USER_T***efgh"
    assert extract_token_from_post_data('{"token":"abc","x":1}') == "abc"
    assert extract_token_from_post_data('{"no":"here"}') is None
    assert extract_token_from_post_data("not-json") is None
    assert extract_token_from_post_data(None) is None
    assert load_token("no-such-file.json") is None
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        save_token(path, "tok123")
        assert load_token(path) == "tok123"
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        assert obj["token"] == "tok123"
        assert "captured_at" in obj
    finally:
        os.remove(path)
    # update_env_token 增/改测试（写临时 .env，不碰真实 .env）
    fd, env_path = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("A=1\nAUTOWFM_TOKEN=old\nB=2\n")
        update_env_token("NEWTOK123", env_path)
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "AUTOWFM_TOKEN=NEWTOK123" in content
        assert "A=1" in content and "B=2" in content
        assert "AUTOWFM_TOKEN=old" not in content
        # 缺失时新增
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("A=1\n")
        update_env_token("NEWTOK123", env_path)
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "AUTOWFM_TOKEN=NEWTOK123" in content
    finally:
        os.remove(env_path)
    print("token_store 自测通过")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
