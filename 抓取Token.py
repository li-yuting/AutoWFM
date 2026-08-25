# -*- coding: utf-8 -*-
r"""用 Playwright 登录 CRM 抓取最新 token，写入 token.json 并回填 .env 的 AUTOWFM_TOKEN。

运行: \.venv\Scripts\python.exe 抓取Token.py
可选: --headless 无头模式（默认有头，便于人机配合验证码/手动登录）
"""
import argparse
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

from token_store import (
    TOKEN_FILE,
    ENV_FILE,
    mask_token,
    extract_token_from_post_data,
    save_token,
    update_env_token,
)

BASE_URL = "https://callcenter-crm.weicai.com.cn"
LOGIN_FILE = "login.json"
STATE_FILE = "storage_state.json"
LOGIN_TIMEOUT = 90


def read_login(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    user = data.get("username", "")
    pw = data.get("password", "")
    if not user or not pw:
        raise SystemExit("login.json 缺少 username 或 password")
    return user, pw


def _fill_first(page, selector_candidates, text):
    for sel in selector_candidates:
        locator = page.locator(sel).first
        if locator.count() and locator.is_visible():
            locator.fill(text)
            return True
    return False


def click_sso_if_present(page):
    """若登录页提供 SSO 统一登录入口，则点击进入 SSO 登录。"""
    for sel in [
        'button:has-text("SSO")',
        'a:has-text("SSO")',
        '[role="tab"]:has-text("SSO")',
        'text=SSO统一登录',
    ]:
        locator = page.locator(sel).first
        if locator.count() and locator.is_visible():
            locator.click()
            return True
    return False


def try_auto_login(page, username, password):
    """进入（若有的）SSO 登录，再用账密尝试自动登录；失败返回 False。"""
    click_sso_if_present(page)
    page.wait_for_timeout(2000)
    user_sels = [
        'input[name="username"]',
        'input[placeholder*="账号"]',
        'input[placeholder*="用户"]',
        'input[type="text"]',
    ]
    pw_sels = [
        'input[name="password"]',
        'input[type="password"]',
    ]
    ok_u = _fill_first(page, user_sels, username)
    ok_p = _fill_first(page, pw_sels, password)
    if not (ok_u and ok_p):
        return False
    for sel in [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("登 录")',
        'button:has-text("登录")',
        'button:has-text("登　录")',
    ]:
        ctl = page.locator(sel).first
        if ctl.count() and ctl.is_visible():
            ctl.click()
            return True
    page.keyboard.press("Enter")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    user, password = None, None
    if os.path.exists(LOGIN_FILE):
        try:
            user, password = read_login(LOGIN_FILE)
        except Exception as exc:
            print(f"[提示] 读取 {LOGIN_FILE} 失败: {exc}；将进入手动登录模式。")

    captured = {"token": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context_kwargs = {}
        if os.path.exists(STATE_FILE):
            context_kwargs["storage_state"] = STATE_FILE
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        def on_request(request):
            if "callcenter-crm.weicai.com.cn" not in request.url:
                return
            body = None
            try:
                body = request.post_data
            except Exception:
                pass
            tok = extract_token_from_post_data(body)
            if tok:
                captured["token"] = tok

        page.on("request", on_request)

        print(f"打开 {BASE_URL} ...")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        if os.path.exists(STATE_FILE):
            page.wait_for_timeout(4000)  # 给已登录会话产生请求的时间

        if captured["token"]:
            print(f"会话仍有效，抓取到 token: {mask_token(captured['token'])}")
        else:
            if user:
                tried = try_auto_login(page, user, password)
                if tried:
                    print("已尝试自动登录，等待跳转与请求...")
                else:
                    print("未定位到登录框，进入手动登录")
            else:
                print("未找到 login.json，进入手动登录")
            print("若页面需要验证码/手动登录，请在打开的浏览器中完成（超时 90 秒）。")
            deadline = time.time() + LOGIN_TIMEOUT
            while time.time() < deadline and not captured["token"]:
                page.wait_for_timeout(1000)
            if not captured["token"]:
                print("未在时限内捕获到 token，请检查登录是否完成。")
                sys.exit(1)

        context.storage_state(path=STATE_FILE)
        save_token(TOKEN_FILE, captured["token"])
        update_env_token(captured["token"], ENV_FILE)
        print(f"已写入 {TOKEN_FILE} 并回填 .env: {mask_token(captured['token'])}")
        browser.close()


if __name__ == "__main__":
    main()
