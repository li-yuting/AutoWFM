# -*- coding: utf-8 -*-
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from token_store import (
    mask_token,
    extract_token_from_post_data,
    save_token,
    load_token,
    update_env_token,
)


class _FakeResp:
    def __init__(self, content):
        self.content = content


def main():
    # ---- token_store 纯函数 ----
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
        # 保存空/坏 JSON 时 load 返回 None
        with open(path, "w", encoding="utf-8") as f:
            f.write("not-json")
        assert load_token(path) is None
    finally:
        os.remove(path)

    # ---- update_env_token：替换不丢其他键 / 缺失新增 ----
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

        with open(env_path, "w", encoding="utf-8") as f:
            f.write("A=1\n")
        update_env_token("NEWTOK123", env_path)
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "AUTOWFM_TOKEN=NEWTOK123" in content
        assert "A=1" in content
    finally:
        os.remove(env_path)

    # ---- collector.detail 登录态异常识别 ----
    from collector import detail
    assert detail._is_excel(b'PK\x03\x04rest') is True
    assert detail._is_excel(b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1xxx') is True
    assert detail._auth_error(_FakeResp('{"code":9003,"msg":"用户未登录"}'.encode("utf-8"))) is True
    assert detail._auth_error(_FakeResp(b'not a json')) is False
    assert detail._auth_error(_FakeResp(b'PK\x03\x04...')) is False

    print("test_token_store OK")


if __name__ == "__main__":
    main()
