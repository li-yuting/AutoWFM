# -*- coding: utf-8 -*-
"""接待上限批量修改核心：headless Playwright 自动化（重构自 AutoConjurer set_member_limit.py）。

浏览器无关的纯逻辑（classify_member / build_summary / format_summary）独立成函数可单测；
run_member_limit() 依赖 playwright + 联网，不进 CI。
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "chrome_profile"   # 持久登录态（git-ignored）
MAX_PAGES = 20                               # 最大翻页数（防死循环）


# ---- 纯逻辑（可单测，不依赖浏览器）----

def classify_member(cur, target: int) -> str:
    """按当前显示值与目标值判定动作：'already'（已达标跳过）| 'change'（需修改）。"""
    return "already" if str(cur).strip() == str(target) else "change"


def build_summary(changed, already, unverified, failed, not_found,
                  cancelled: bool, dry_run: bool) -> dict:
    return {
        "changed": list(changed),
        "already": list(already),
        "unverified": list(unverified),
        "failed": list(failed),
        "not_found": list(not_found),
        "cancelled": bool(cancelled),
        "dry_run": bool(dry_run),
    }


def format_summary(s: dict) -> str:
    lines = []
    if s["dry_run"]:
        lines.append("[DRY-RUN] 仅检查当前值，未实际修改")
    lines.append("===== 运行汇总 =====")
    total = (len(s["changed"]) + len(s["already"]) + len(s["unverified"])
             + len(s["failed"]) + len(s["not_found"]))
    lines.append(f"共处理 {total} 人")
    if s["cancelled"]:
        lines.append("[已取消] 执行被手动停止（以下为已处理部分）")
    if s["changed"]:
        lines.append(f"[修改成功] {len(s['changed'])} 人：" +
                     ", ".join(f"{n}({o}->{nw})" for n, o, nw in s["changed"]))
    else:
        lines.append("[修改成功] 0 人：无")
    if s["already"]:
        lines.append(f"[本来已达标] {len(s['already'])} 人：" + ", ".join(n for n, _ in s["already"]))
    else:
        lines.append("[本来已达标] 0 人：无")
    if s["unverified"]:
        lines.append(f"[提交未校验] {len(s['unverified'])} 人：" + ", ".join(s["unverified"]))
    else:
        lines.append("[提交未校验] 0 人：无")
    if s["failed"]:
        lines.append(f"[修改失败] {len(s['failed'])} 人：" + ", ".join(s["failed"]))
    else:
        lines.append("[修改失败] 0 人：无")
    if s["not_found"]:
        lines.append(f"[未找到] {len(s['not_found'])} 人：" + ", ".join(s["not_found"]))
    else:
        lines.append("[未找到] 0 人：无")
    return "\n".join(lines)


# ---- 浏览器自动化（依赖 playwright，联网）----

def _say(cb, text: str) -> None:
    if cb:
        cb(text)


def _login(page, account: str, password: str) -> None:
    page.get_by_role("textbox", name="请输入账号").click()
    page.get_by_role("textbox", name="请输入账号").fill(account)
    page.get_by_role("textbox", name="请输入密码").click()
    page.get_by_role("textbox", name="请输入密码").fill(password)
    page.get_by_role("button", name="登录").click()


def _ensure_login(page, config: dict, cb) -> None:
    base_url = config["url"].rstrip("/")
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if "/login" in page.url:
        _say(cb, ">> 登录态失效，重新登录...")
        page.goto(base_url + "/login")
        _login(page, config["account"], config["password"])
        page.wait_for_timeout(4000)
        _say(cb, ">> 登录成功（已持久化到 profile）")
    else:
        _say(cb, ">> 登录态有效，跳过登录")


def _data_rows(page):
    return page.locator("table").nth(1).locator("tr")


def _row_info(page, i):
    row = _data_rows(page).nth(i)
    name = row.locator("td").nth(0).inner_text().strip()
    cur = row.locator("td").nth(3).inner_text().strip()
    return name, cur


def _find_pending_row(page, pending):
    rows = _data_rows(page)
    for i in range(rows.count()):
        name, cur = _row_info(page, i)
        if name in pending:
            return i, name, cur
    return None, None, None


def _page_number(page) -> str:
    try:
        return page.locator(".trtc-tea-pagination__inputpagenum").input_value()
    except Exception:
        return "?"


def _click_next(page) -> bool:
    nxt = page.locator(".trtc-tea-pagination__nextbtn")
    if "is-disabled" in (nxt.get_attribute("class") or ""):
        return False
    cur = _page_number(page)
    nxt.click()
    try:
        page.wait_for_function(
            "document.querySelector('.trtc-tea-pagination__inputpagenum')?.value !== arguments[0]",
            arg=cur, timeout=15000,
        )
    except Exception:
        pass
    page.wait_for_timeout(1500)
    return True


def _set_limit(page, limit: int) -> bool:
    dlg = page.locator("[role=dialog]").last
    cb = dlg.locator("input[type=checkbox]").first
    if cb.count():
        if not cb.is_checked():
            dlg.locator("label.trtc-tea-form-check").first.click()
            page.wait_for_timeout(600)
    limit_input = dlg.locator('input[placeholder="请输入"]').first
    limit_input.fill(str(limit))
    page.wait_for_timeout(300)
    dlg.get_by_role("button", name="完成").click()
    for _ in range(30):
        page.wait_for_timeout(500)
        if page.locator("[role=dialog]").count() == 0:
            return True
    return False


def _edit_row(page, idx: int, limit: int) -> bool:
    _data_rows(page).nth(idx).locator("td").nth(6).locator("button").first.click()
    page.wait_for_timeout(1500)
    return _set_limit(page, limit)


def run_member_limit(config: dict, progress_cb=None, should_cancel=None,
                     dry_run: bool = False) -> dict:
    """批量修改成员接待上限。

    config: member_limit.config.load() 的返回值。
    progress_cb: callable(str) 逐行进度；None 则不输出。
    should_cancel: callable() -> bool，每处理完一人检查，True 则停止。
    dry_run: True 只读当前值不修改。
    返回 build_summary() 汇总。
    """
    from playwright.sync_api import sync_playwright

    limit = int(config["limit"])
    pending = set(config["members"])
    changed, already, unverified, failed, not_found = [], [], [], [], []
    cancelled = False

    _say(progress_cb, f"共用接待上限 = {limit}，待处理成员数 = {len(pending)}"
                      + ("（DRY-RUN，不实际修改）" if dry_run else ""))

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=bool(config.get("headless", True)),
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _ensure_login(page, config, progress_cb)
            page.locator("a").filter(has_text="成员").click()
            page.wait_for_timeout(3000)
            _say(progress_cb, f"已进入成员页：{page.url}")

            for page_num in range(1, MAX_PAGES + 1):
                if not pending:
                    break
                _say(progress_cb, f"----- 第 {page_num} 页 -----")
                while pending:
                    idx, name, cur = _find_pending_row(page, pending)
                    if idx is None:
                        break
                    pending.discard(name)
                    if classify_member(cur, limit) == "already":
                        _say(progress_cb, f"  [本来已达标] {name} 当前已是 {limit}，跳过")
                        already.append((name, limit))
                    elif dry_run:
                        _say(progress_cb, f"  [DRY-RUN] {name} 当前 {cur}，目标 {limit}（未修改）")
                        changed.append((name, cur, limit))
                    else:
                        _say(progress_cb, f"  [开始修改] {name} 当前 {cur} -> 目标 {limit}，点击编辑")
                        ok = _edit_row(page, idx, limit)
                        page.wait_for_timeout(3000)
                        _, _, new_cur = _find_pending_row(page, {name})
                        if ok and new_cur == str(limit):
                            _say(progress_cb, f"  [修改成功] {name}：{cur} -> {limit}（已校验）")
                            changed.append((name, cur, limit))
                        elif ok:
                            _say(progress_cb, f"  [提交未校验] {name} 提交成功但自动校验未通过，当前显示 {new_cur!r}")
                            unverified.append(name)
                        else:
                            _say(progress_cb, f"  [修改失败] {name} 弹窗未正常关闭，可能未生效")
                            failed.append(name)
                    if should_cancel and should_cancel():
                        cancelled = True
                        _say(progress_cb, ">> 收到停止请求，终止后续处理")
                        break
                if not pending or cancelled:
                    break
                if not _click_next(page):
                    _say(progress_cb, ">> 已到最后一页")
                    break

            if not cancelled:
                for name in sorted(pending):
                    not_found.append(name)
                    _say(progress_cb, f"  [未找到] {name} 遍历全部页面未找到")
        finally:
            context.close()

    summary = build_summary(changed, already, unverified, failed, not_found,
                            cancelled, dry_run)
    _say(progress_cb, format_summary(summary))
    return summary
