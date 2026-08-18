# 接待上限批量修改接入 manager 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把腾讯云联络中心成员接待上限批量修改功能（原 AutoConjurer set_member_limit.py）移植为 `member_limit/` 模块，并集成进 `manager.py` 桌面监管器的「接待上限」页（手动执行 + 实时日志 + 逐人取消 + 两个独立单次预约入口）。

**Architecture:** `member_limit/` 包（config.py 加载配置/凭据 + core.py headless Playwright 自动化 + main.py CLI）由 manager.py 在进程内工作线程调用（仿 forecast/backfill 页）；预约调度挂在现有 5 秒 `_refresh()` 周期循环；浏览器无关逻辑抽成纯函数供 CI 单测。

**Tech Stack:** Python 3.14（.venv）、Playwright 1.61（已装，headless + persistent context）、Tkinter、PyYAML、python-dotenv。

## Global Constraints

- 一律用 `.venv\Scripts\python.exe` 运行，先 `$env:PYTHONIOENCODING="utf-8"`。
- 测试为纯 `assert`（无 pytest），文件 `tests/test_*.py`、函数 `test_*`，每个文件有 `main()` 逐调并打印 `... OK`，可直接 `python tests/test_xxx.py`。
- 测试临时目录用工作区本地 `tests/.test_tmp`（`_WS_TMP` + `shutil.rmtree`，不用系统临时目录）。
- 密钥（腾讯云账号/密码）只放 `.env`（git-ignored）；`config.yaml` / `config.example.yaml` 不放任何密码。
- Python 4 空格缩进、PEP 8；无 linter/formatter。
- Conventional Commits（`feat:` / `test:` / `chore:` 等，可带 scope，中文描述常见）。
- 不新增第三方依赖（playwright 已在 requirements.txt）。
- manager.py 现有导航页计数在 `tests/test_manager.py::test_ui_constructs` 断言（6 → 加页后为 7），需同步更新。
- `test_ui_constructs` 用 `_cfg()`（无 `member_limit` 段），新页面构建必须对缺失段安全（默认值兜底）。

---

### Task 1: member_limit/config.py — 配置加载与校验（TDD）

**Files:**
- Create: `member_limit/__init__.py`（本任务仅占位，Task 2 补导出）
- Create: `member_limit/config.py`
- Create: `tests/test_member_limit_config.py`
- Modify: `config.yaml`（末尾追加 `member_limit:` 段，含 41 人名单）
- Modify: `config.example.yaml`（末尾追加脱敏模板）
- Modify: `.env.example`（追加两个凭据占位）
- Modify: `.gitignore`（追加 `member_limit/chrome_profile/`）

**Interfaces:**
- Consumes: 无。
- Produces: `member_limit.config.load(config_path="config.yaml") -> dict`，返回 `{"url", "account", "password", "limit", "members", "headless"}`；缺失凭据/空名单抛 `member_limit.config.ConfigError`。后续 Task 2/3/4 依赖此函数。

- [ ] **Step 1: 创建空 `member_limit/__init__.py`**

```python
"""腾讯云联络中心成员接待上限批量修改模块。"""
```

- [ ] **Step 2: 写失败测试 `tests/test_member_limit_config.py`**

```python
# -*- coding: utf-8 -*-
"""member_limit 配置加载/校验测试：plain assert，直接运行。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shutil
from contextlib import contextmanager
from pathlib import Path
from member_limit.config import ConfigError, load

# Use workspace-local temp to avoid sandbox restrictions on system temp
_WS_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_tmp")


@contextmanager
def _tmp():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        yield Path(_WS_TMP)
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)


def _set_env(account="a@b.com", password="pwd"):
    os.environ["AUTOWFM_QCLOUD_ACCOUNT"] = account
    os.environ["AUTOWFM_QCLOUD_PASSWORD"] = password


def test_load_ok():
    with _tmp() as td:
        _set_env()
        p = td / "config.yaml"
        p.write_text(
            "member_limit:\n"
            "  url: 'https://desk.qcloud.com/'\n"
            "  limit: 3\n"
            "  headless: true\n"
            "  members:\n"
            "    - '甲'\n"
            "    - '乙'\n",
            encoding="utf-8")
        cfg = load(p)
        assert cfg["url"] == "https://desk.qcloud.com/"
        assert cfg["limit"] == 3
        assert cfg["members"] == ["甲", "乙"]
        assert cfg["headless"] is True
        assert cfg["account"] == "a@b.com"
        assert cfg["password"] == "pwd"


def test_load_missing_credentials():
    with _tmp() as td:
        os.environ.pop("AUTOWFM_QCLOUD_ACCOUNT", None)
        os.environ.pop("AUTOWFM_QCLOUD_PASSWORD", None)
        p = td / "config.yaml"
        p.write_text("member_limit:\n  members: ['甲']\n", encoding="utf-8")
        try:
            load(p)
            assert False, "缺少凭据应抛 ConfigError"
        except ConfigError as e:
            assert "AUTOWFM_QCLOUD_ACCOUNT" in str(e)


def test_load_empty_members():
    with _tmp() as td:
        _set_env()
        p = td / "config.yaml"
        p.write_text("member_limit:\n  members: []\n", encoding="utf-8")
        try:
            load(p)
            assert False, "空名单应抛 ConfigError"
        except ConfigError as e:
            assert "名单为空" in str(e)


def test_load_defaults():
    with _tmp() as td:
        _set_env()
        p = td / "config.yaml"
        p.write_text("member_limit:\n  members: ['甲']\n", encoding="utf-8")
        cfg = load(p)
        assert cfg["limit"] == 3
        assert cfg["headless"] is True
        assert cfg["url"] == "https://desk.qcloud.com/"


def main():
    test_load_ok()
    test_load_missing_credentials()
    test_load_empty_members()
    test_load_defaults()
    print("test_member_limit_config OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 运行确认失败**

Run（PowerShell，工作目录为项目根）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_member_limit_config.py
```
Expected: FAIL with `ModuleNotFoundError: No module named 'member_limit'`（包尚未创建）。

- [ ] **Step 4: 实现 `member_limit/config.py`**

```python
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
```

- [ ] **Step 5: 运行确认通过**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_member_limit_config.py
```
Expected: PASS，输出 `test_member_limit_config OK`。

- [ ] **Step 6: 更新 `config.yaml`（末尾追加，含 41 人真实名单）**

在文件末尾追加：

```yaml

# ============ 接待上限批量修改(member_limit) ============
# 腾讯云联络中心成员接待上限批量修改工具。
# 凭据(账号/密码)放 .env 的 AUTOWFM_QCLOUD_ACCOUNT / AUTOWFM_QCLOUD_PASSWORD，不入库。
member_limit:
  url: "https://desk.qcloud.com/"
  limit: 3              # 手动执行输入框的默认值(实际以 UI 输入框为准)
  headless: true        # 无头运行;排障可临时改 false(可见窗口)
  members:
    - "雷博"
    - "蒙静"
    - "米欣"
    - "段雪楠"
    - "赵敏"
    - "丁花娟"
    - "石凯"
    - "贾世涛"
    - "杨晖"
    - "史立"
    - "王玲霞"
    - "魏倩倩"
    - "孟坤"
    - "孟令坤"
    - "曾小玲"
    - "杨国蓉"
    - "杨少晨"
    - "魏卓怡"
    - "张慧颖"
    - "方珍"
    - "王园"
    - "王璇"
    - "田科科"
    - "段旭"
    - "魏锋"
    - "贺周"
    - "赵佩瑶"
    - "荀飞扬"
    - "候磊"
    - "黎家齐"
    - "杨诺菲"
    - "朱启佳"
    - "王廷仪"
    - "蔡勋"
    - "张圣莹"
    - "王静逸"
    - "何泽名"
    - "陈逸飞"
    - "吴嘉瑜"
    - "林耀俊"
    - "房吴颉"
```

校验名单条数：
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe -c "import yaml,io; c=yaml.safe_load(io.open('config.yaml',encoding='utf-8')); print(len(c['member_limit']['members']))"
```
Expected: `41`。

- [ ] **Step 7: 更新 `config.example.yaml`（末尾追加脱敏模板）**

```yaml

# ============ 接待上限批量修改(member_limit) ============
# 腾讯云联络中心成员接待上限批量修改工具。
# 凭据(账号/密码)由 .env 的 AUTOWFM_QCLOUD_ACCOUNT / AUTOWFM_QCLOUD_PASSWORD 提供。
member_limit:
  url: "https://desk.qcloud.com/"
  limit: 3              # 手动执行输入框的默认值(实际以 UI 输入框为准)
  headless: true        # 无头运行;排障可临时改 false(可见窗口)
  members: []           # 待调整的成员昵称列表(成员页第一列，需完全一致)
```

- [ ] **Step 8: 更新 `.env.example`（末尾追加）**

```ini
# 接待上限批量修改(member_limit 模块用)
AUTOWFM_QCLOUD_ACCOUNT=
AUTOWFM_QCLOUD_PASSWORD=
```

- [ ] **Step 9: 更新 `.gitignore`（末尾追加）**

```gitignore
# ---- 接待上限批量修改 ----
member_limit/chrome_profile/
```

- [ ] **Step 10: 提交**

```bash
git add member_limit/__init__.py member_limit/config.py tests/test_member_limit_config.py config.yaml config.example.yaml .env.example .gitignore
git commit -m "feat(member_limit): add config loader with validation and member list"
```

---

### Task 2: member_limit/core.py — 自动化核心 + 纯逻辑（TDD）

**Files:**
- Create: `member_limit/core.py`
- Create: `tests/test_member_limit_core.py`
- Modify: `member_limit/__init__.py`（补导出）

**Interfaces:**
- Consumes: `member_limit.config.load()` 返回的 dict（Task 1）。
- Produces:
  - `member_limit.core.classify_member(cur, target) -> "already"|"change"`
  - `member_limit.core.build_summary(changed, already, unverified, failed, not_found, cancelled, dry_run) -> dict`
  - `member_limit.core.format_summary(summary) -> str`
  - `member_limit.core.run_member_limit(config, progress_cb=None, should_cancel=None, dry_run=False) -> dict`
  - `member_limit.core.PROFILE_DIR`（`member_limit/chrome_profile`）
  Task 3（CLI）与 Task 4（manager）依赖上述签名。

- [ ] **Step 1: 写失败测试 `tests/test_member_limit_core.py`**

```python
# -*- coding: utf-8 -*-
"""member_limit 纯逻辑测试（不依赖浏览器）：plain assert，直接运行。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from member_limit.core import classify_member, build_summary, format_summary


def test_classify_member():
    assert classify_member("3", 3) == "already"
    assert classify_member(" 3 ", 3) == "already"
    assert classify_member("4", 3) == "change"
    assert classify_member("", 3) == "change"


def test_build_summary():
    s = build_summary([("甲", "4", "3")], [("乙", 3)], [], ["丙"], ["丁"], False, False)
    assert s["changed"] == [("甲", "4", "3")]
    assert s["already"] == [("乙", 3)]
    assert s["failed"] == ["丙"]
    assert s["not_found"] == ["丁"]
    assert s["cancelled"] is False
    assert s["dry_run"] is False


def test_format_summary_cancelled():
    s = build_summary([("甲", "4", "3")], [], [], [], [], True, False)
    text = format_summary(s)
    assert "共处理 1 人" in text
    assert "[已取消]" in text
    assert "甲(4->3)" in text


def test_format_summary_dry_run():
    s = build_summary([], [], [], [], [], False, True)
    text = format_summary(s)
    assert "[DRY-RUN]" in text
    assert "[修改成功] 0 人：无" in text


def main():
    test_classify_member()
    test_build_summary()
    test_format_summary_cancelled()
    test_format_summary_dry_run()
    print("test_member_limit_core OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_member_limit_core.py
```
Expected: FAIL with `ModuleNotFoundError: No module named 'member_limit.core'`。

- [ ] **Step 3: 实现 `member_limit/core.py`**

```python
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
    return "
".join(lines)


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
```

- [ ] **Step 4: 更新 `member_limit/__init__.py`（补导出）**

```python
"""腾讯云联络中心成员接待上限批量修改模块。"""
from member_limit.core import (build_summary, classify_member, format_summary,
                               run_member_limit)

__all__ = ["run_member_limit", "classify_member", "build_summary", "format_summary"]
```

- [ ] **Step 5: 运行确认通过**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_member_limit_core.py
```
Expected: PASS，输出 `test_member_limit_core OK`。

- [ ] **Step 6: 提交**

```bash
git add member_limit/core.py member_limit/__init__.py tests/test_member_limit_core.py
git commit -m "feat(member_limit): add headless core automation with cancel and dry-run"
```
### Task 3: member_limit/main.py — CLI（--limit / --dry-run，TDD）

**Files:**
- Create: `member_limit/main.py`
- Create: `tests/test_member_limit_main.py`

**Interfaces:**
- Consumes: `member_limit.config.load()`（Task 1）、`member_limit.core.run_member_limit(config, progress_cb=None, should_cancel=None, dry_run=False)`（Task 2）。
- Produces: `member_limit.main.main(argv=None) -> int`（0=成功 / 1=执行失败 / 2=配置错误）。Task 4 不依赖本任务（manager 直接调 core），但 CLI 独立可用。

- [ ] **Step 1: 写失败测试 `tests/test_member_limit_main.py`**

```python
# -*- coding: utf-8 -*-
"""member_limit CLI 测试（monkeypatch core.run_member_limit，不启动浏览器）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch
from member_limit import main as cli
from member_limit.core import build_summary
from member_limit.config import ConfigError

_FAKE_CONFIG = {
    "url": "https://desk.qcloud.com/", "account": "a@b.com", "password": "pwd",
    "limit": 3, "members": ["甲"], "headless": True,
}


def test_main_dry_run_flag():
    captured = {}

    def fake_run(config, progress_cb=None, should_cancel=None, dry_run=False):
        captured["dry_run"] = dry_run
        captured["limit"] = config["limit"]
        return build_summary([], [], [], [], [], False, dry_run)

    with patch("member_limit.main.load_config", return_value=dict(_FAKE_CONFIG)), \
         patch("member_limit.core.run_member_limit", side_effect=fake_run) as m:
        rc = cli.main(["--limit", "5", "--dry-run"])
    assert rc == 0
    assert captured["dry_run"] is True
    assert captured["limit"] == 5
    assert m.call_count == 1


def test_main_default_limit():
    captured = {}

    def fake_run(config, progress_cb=None, should_cancel=None, dry_run=False):
        captured["limit"] = config["limit"]
        return build_summary([], [], [], [], [], False, False)

    with patch("member_limit.main.load_config", return_value=dict(_FAKE_CONFIG)), \
         patch("member_limit.core.run_member_limit", side_effect=fake_run):
        rc = cli.main([])
    assert rc == 0
    assert captured["limit"] == 3


def test_main_config_error():
    with patch("member_limit.main.load_config", side_effect=ConfigError("缺少凭据")):
        rc = cli.main([])
    assert rc == 2


def main():
    test_main_dry_run_flag()
    test_main_default_limit()
    test_main_config_error()
    print("test_member_limit_main OK")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_member_limit_main.py
```
Expected: FAIL with `ModuleNotFoundError: No module named 'member_limit.main'`。

- [ ] **Step 3: 实现 `member_limit/main.py`**

```python
# -*- coding: utf-8 -*-
"""CLI 入口：python -m member_limit.main [--limit N] [--dry-run]。

脱离 GUI 运行同一逻辑；--dry-run 只读当前值不修改。
"""
from __future__ import annotations

import argparse
import sys

from member_limit import core
from member_limit.config import ConfigError, load as load_config


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="腾讯云联络中心成员接待上限批量修改")
    ap.add_argument("--limit", type=int, default=None,
                    help="目标接待上限（缺省用 config.yaml 的 member_limit.limit）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只检查当前值，不实际修改")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2
    if args.limit is not None:
        config["limit"] = args.limit
    try:
        core.run_member_limit(config, progress_cb=print, dry_run=args.dry_run)
    except Exception as exc:
        print(f"执行失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

注意：`core.run_member_limit` 内部已用 `progress_cb=print` 逐行输出并在结尾输出汇总，CLI 无需再重复打印汇总。

- [ ] **Step 4: 运行确认通过**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_member_limit_main.py
```
Expected: PASS，输出 `test_member_limit_main OK`。

- [ ] **Step 5: 提交**

```bash
git add member_limit/main.py tests/test_member_limit_main.py
git commit -m "feat(member_limit): add CLI entry with --limit and --dry-run"
```

---

### Task 4: manager.py「接待上限」页 + 两行预约 + 调度与取消（TDD）

**Files:**
- Modify: `manager.py`（多处，见各步骤）
- Modify: `tests/test_manager.py`（新增纯函数测试 + 更新 `test_ui_constructs` 计数 6→7）
- Modify: `AGENTS.md`、`README.md`（各补一段说明）

**Interfaces:**
- Consumes: `member_limit.config.load()` 与 `member_limit.core.run_member_limit(config, progress_cb, should_cancel, dry_run)`（Task 1/2）。
- Produces（manager.py 模块级纯函数，供本任务测试）:
  - `parse_schedule(s: str) -> int | None`（HH:MM → 当日分钟；非法/越界返回 None）
  - `schedule_action(enabled: bool, now_mins: int, sched_mins, fired: bool) -> "wait"|"run"|"expired"|"idle"`
  以及 ManagerUI 新方法（供人工验收，不单测）：
  `_build_member_limit_page` / `_set_member_limit_text` / `_append_member_limit_text` / `_set_member_limit_status` / `_manual_member_limit` / `_run_member_limit(limit, label) -> bool` / `_stop_member_limit` / `_on_member_limit_progress` / `_on_member_limit_done` / `_check_member_limit_schedules`。

- [ ] **Step 1: 写失败测试（追加到 `tests/test_manager.py`）**

在文件顶部 import 行（第 10 行）把 `schedule_text` 之后补上 `parse_schedule, schedule_action`：

```python
from manager import (compute_auto_start, auto_stop_minutes, in_run_window, schedule_text,
                     ManagerUI, ManagedTask, GRACE_SECONDS, parse_schedule, schedule_action)
```

在 `test_schedule_text` 之后追加两个测试函数：

```python
def test_parse_schedule():
    assert parse_schedule("18:00") == 18 * 60
    assert parse_schedule("09:30") == 9 * 60 + 30
    assert parse_schedule("00:00") == 0
    assert parse_schedule("") is None
    assert parse_schedule("abc") is None
    assert parse_schedule("25:00") is None
    assert parse_schedule("12:60") is None
    print("parse_schedule OK")


def test_schedule_action():
    t = 18 * 60
    assert schedule_action(False, t, t, False) == "idle"          # 未启用
    assert schedule_action(True, t - 30, t, False) == "wait"      # 未到点
    assert schedule_action(True, t, t, False) == "run"            # 到点触发
    assert schedule_action(True, t + 30, t, False) == "expired"   # 时间已过
    assert schedule_action(True, t, t, True) == "idle"            # 已触发过
    assert schedule_action(True, t, None, False) == "idle"        # 时间非法
    print("schedule_action OK")
```

在 `test_ui_constructs` 中把导航/页面计数断言 6 改为 7：

```python
            assert len(ui._nav_buttons) == 7, f"7 个导航按钮, 实际 {len(ui._nav_buttons)}"
            assert len(ui._nav_pages) == 7, f"7 个内容页, 实际 {len(ui._nav_pages)}"
            assert len(ui._log_boxes) == 4, f"4 个日志框, 实际 {len(ui._log_boxes)}"
```

在 `main()` 的调用列表（第 285-305 行附近）加入两行新测试，并把最后的打印改为 `test_schedule_action` 等：

```python
    test_parse_schedule()
    test_schedule_action()
```

- [ ] **Step 2: 运行确认失败**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_manager.py
```
Expected: FAIL with `ImportError: cannot import name 'parse_schedule'`（函数尚未实现）。

- [ ] **Step 3: manager.py 加模块级常量与纯函数**

在 `SHIFT_LOG = LOG_DIR / "shift.log"`（约第 41 行）之后加一行：

```python
MEMBER_LIMIT_LOG = LOG_DIR / "member_limit.log"
```

在 `class ManagedTask:`（第 157 行）之前插入两个纯函数：

```python
def parse_schedule(s: str):
    """HH:MM → 当日分钟(0..1439)；非法/越界返回 None。"""
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except (ValueError, AttributeError):
        pass
    return None


def schedule_action(enabled: bool, now_mins: int, sched_mins, fired: bool) -> str:
    """单次预约状态机：'wait'（等待到点）| 'run'（到点触发）|
    'expired'（时间已过，跳过）| 'idle'（未启用/已触发/时间非法）。"""
    if not enabled or fired or sched_mins is None:
        return "idle"
    if now_mins > sched_mins:
        return "expired"
    if now_mins == sched_mins:
        return "run"
    return "wait"
```

- [ ] **Step 4: 运行确认通过**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
..venvScriptspython.exe tests	est_manager.py
```
Expected: 纯函数测试 PASS（UI 部分因页面尚未加仍会失败？——不会：`test_ui_constructs` 断言仍是 6，页面未加时仍通过）。实际期望：除新增两个测试通过外，其余照旧。

- [ ] **Step 5: manager.py 加 UI 状态与导航项**

在 `ManagerUI.__init__` 的 `self._backfill_running = False`（约第 452 行）之后加：

```python
        self._ml_running = False   # 接待上限执行去重锁
        self._ml_cancel = False    # 逐人中断标记
        self._ml_sched = []        # 两个预约行状态(在 _build_member_limit_page 填充)
```

在 `_build_ui` 的「数据补全」导航项（`nav_items.append(("数据补全", bf_page))`，约第 521 行）之后加：

```python
        ml_page = tk.Frame(content)
        ml_page.grid(row=0, column=0, sticky="nsew")
        self._build_member_limit_page(ml_page)
        nav_items.append(("接待上限", ml_page))
```

- [ ] **Step 6: manager.py 加「接待上限」页全部方法**

在 `_on_backfill_done` 方法之后（`_restart_manager` 之前）插入整块方法：

```python
    # ---- 接待上限批量修改 ----
    def _build_member_limit_page(self, page: tk.Frame) -> None:
        top = tk.Frame(page, padx=10, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="上限值:").pack(side=tk.LEFT)
        ml_cfg = self.cfg.get("member_limit") or {}
        self.ml_limit_var = tk.StringVar(value=str(ml_cfg.get("limit", 3)))
        tk.Entry(top, width=6, textvariable=self.ml_limit_var).pack(side=tk.LEFT, padx=4)
        members = ml_cfg.get("members") or []
        tk.Label(top, text=f"成员: {len(members)} 人").pack(side=tk.LEFT, padx=8)
        self.btn_ml_start = tk.Button(top, text="开始执行", width=10, command=self._manual_member_limit)
        self.btn_ml_start.pack(side=tk.LEFT, padx=6)
        self.btn_ml_stop = tk.Button(top, text="停止", width=8,
                                     command=self._stop_member_limit, state=tk.DISABLED)
        self.btn_ml_stop.pack(side=tk.LEFT, padx=6)
        self.ml_status_var = tk.StringVar(value="就绪")
        tk.Label(top, textvariable=self.ml_status_var, fg="#555555").pack(side=tk.LEFT, padx=10)

        sched = tk.Frame(page, padx=10, pady=4)
        sched.pack(fill=tk.X)
        tk.Label(sched, text="预约运行:", font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        self._ml_sched = []
        for k in (1, 2):
            st = {
                "enabled": tk.BooleanVar(value=False),
                "time": tk.StringVar(value=""),
                "limit": tk.StringVar(value=""),
                "status": tk.StringVar(value="等待预约"),
                "fired": False,
            }
            self._ml_sched.append(st)
            row = tk.Frame(sched)
            row.pack(side=tk.LEFT, padx=(0, 18))
            tk.Checkbutton(row, text=f"预约{k}", variable=st["enabled"]).pack(side=tk.LEFT)
            tk.Entry(row, width=7, textvariable=st["time"]).pack(side=tk.LEFT, padx=3)
            tk.Label(row, text="上限").pack(side=tk.LEFT)
            tk.Entry(row, width=4, textvariable=st["limit"]).pack(side=tk.LEFT, padx=3)
            tk.Label(row, textvariable=st["status"], fg="#555555").pack(side=tk.LEFT, padx=4)

        self.ml_box = scrolledtext.ScrolledText(page, wrap=tk.WORD, font=("Consolas", 10))
        self.ml_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self.ml_box.configure(state=tk.DISABLED)
        self._set_member_limit_text(
            "手动执行：填上限值后点「开始执行」；运行中可点「停止」逐人中断。\n"
            "预约执行：勾选启用 + 填 HH:MM 时间与上限值，到点自动执行一次后清除。\n"
            "日志同时写入 logs/member_limit.log。")

    def _set_member_limit_text(self, text: str) -> None:
        self.ml_box.configure(state=tk.NORMAL)
        self.ml_box.delete("1.0", tk.END)
        self.ml_box.insert(tk.END, text)
        self.ml_box.configure(state=tk.DISABLED)

    def _append_member_limit_text(self, text: str) -> None:
        self.ml_box.configure(state=tk.NORMAL)
        self.ml_box.insert(tk.END, text + "\n")
        self.ml_box.see(tk.END)
        self.ml_box.configure(state=tk.DISABLED)
        try:
            with open(MEMBER_LIMIT_LOG, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _set_member_limit_status(self, s: str) -> None:
        self.ml_status_var.set(s)

    def _manual_member_limit(self) -> None:
        try:
            limit = int(self.ml_limit_var.get().strip())
        except ValueError:
            self._set_member_limit_status("上限需为数字")
            return
        self._run_member_limit(limit, "手动执行")

    def _run_member_limit(self, limit: int, label: str) -> bool:
        """启动一次执行；返回是否真正启动（False=已有执行/参数无效，供调度层标记跳过）。"""
        if self._ml_running:
            self._append_member_limit_text(f"[{label}] 已有执行在跑，本次触发跳过")
            return False
        if limit <= 0:
            self._set_member_limit_status("上限无效")
            self._append_member_limit_text(f"[{label}] 上限需为正整数")
            return False
        members = (self.cfg.get("member_limit") or {}).get("members") or []
        if not members:
            self._set_member_limit_status("名单为空")
            self._append_member_limit_text("config.yaml 的 member_limit.members 为空，请先配置")
            return False
        self._ml_running = True
        self._ml_cancel = False
        self.btn_ml_start.configure(state=tk.DISABLED)
        self.btn_ml_stop.configure(state=tk.NORMAL)
        self._set_member_limit_status("运行中...")
        self._set_member_limit_text(f"[{label}] 开始，目标上限 = {limit}，成员 {len(members)} 人")

        def worker():
            try:
                from member_limit.config import ConfigError, load as ml_load
                from member_limit.core import run_member_limit
                cfg = ml_load()
                cfg["limit"] = limit
                summary = run_member_limit(
                    cfg,
                    progress_cb=self._on_member_limit_progress,
                    should_cancel=lambda: self._ml_cancel,
                )
                self.root.after(0, self._on_member_limit_done, summary, None, label)
            except ConfigError as exc:
                self.root.after(0, self._on_member_limit_done, "", exc, label)
            except Exception as exc:
                log.exception("接待上限执行失败")
                self.root.after(0, self._on_member_limit_done, "", exc, label)

        threading.Thread(target=worker, daemon=True, name="member_limit").start()
        return True

    def _stop_member_limit(self) -> None:
        self._ml_cancel = True
        self._set_member_limit_status("停止中...")
        self._append_member_limit_text(">> 已请求停止（当前成员完成后退出）")
        self.btn_ml_stop.configure(state=tk.DISABLED)

    def _on_member_limit_progress(self, text: str) -> None:
        self.root.after(0, self._append_member_limit_text, text)

    def _on_member_limit_done(self, summary, err, label: str) -> None:
        self._ml_running = False
        self.btn_ml_start.configure(state=tk.NORMAL)
        self.btn_ml_stop.configure(state=tk.DISABLED)
        if err is not None:
            self._set_member_limit_status("失败")
            self._append_member_limit_text(f"\n[{label}] 执行失败: {err}")
            for st in self._ml_sched:
                if st["status"].get() == "执行中":
                    st["status"].set("已失败")
            return
        from member_limit.core import format_summary
        self._set_member_limit_status("完成")
        self._append_member_limit_text("\n" + format_summary(summary))
        for st in self._ml_sched:
            if st["status"].get() == "执行中":
                st["status"].set("已执行")

    def _check_member_limit_schedules(self, now: dt.datetime) -> None:
        """每次 _refresh 调用：按状态机处理两个预约行（wait/run/expired）。"""
        now_mins = now.hour * 60 + now.minute
        for st in self._ml_sched:
            sched_mins = parse_schedule(st["time"].get().strip())
            action = schedule_action(st["enabled"].get(), now_mins, sched_mins, st["fired"])
            if action == "wait":
                continue
            st["enabled"].set(False)
            if action == "expired":
                st["status"].set("已过期")
                self._append_member_limit_text(f"[预约 {st['time'].get()}] 时间已过，跳过本次")
            elif action == "run":
                st["fired"] = True
                st["status"].set("执行中")
                try:
                    limit = int(st["limit"].get().strip() or 0)
                except ValueError:
                    limit = 0
                if limit <= 0:
                    st["status"].set("已取消(上限无效)")
                    self._append_member_limit_text(f"[预约 {st['time'].get()}] 上限无效，已取消本次")
                    continue
                self._append_member_limit_text(f"[预约 {st['time'].get()}] 到点自动执行，上限={limit}")
                started = self._run_member_limit(limit, f"预约 {st['time'].get()}")
                if not started:
                    st["status"].set("已跳过")
```

- [ ] **Step 7: manager.py 在 `_refresh` 挂调度钩子**

在 `_refresh` 方法中 `self.schedule_var.set(schedule_text(self.cfg, now))`（约第 567 行）之后加一行：

```python
            self._check_member_limit_schedules(now)
```

- [ ] **Step 8: 更新文档**

在 `AGENTS.md` 的 Project Structure 列表补一行：

```markdown
- `member_limit/` — 腾讯云联络中心成员接待上限批量修改（headless Playwright），manager.py「接待上限」页调用；凭据在 `.env`（AUTOWFM_QCLOUD_ACCOUNT / AUTOWFM_QCLOUD_PASSWORD），名单在 config.yaml。
```

在 `README.md` 的功能总览表补一行：

```markdown
| 接待上限 | `member_limit/` | headless 批量改腾讯云联络中心成员接待上限，manager.py「接待上限」页手动/预约执行 |
```

- [ ] **Step 9: 运行全量测试确认通过**

Run（PowerShell）:
```powershell
$env:PYTHONIOENCODING="utf-8"
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```
Expected: 全部测试文件输出 OK（包括 `test_manager.py` 输出 `ALL manager tests OK`）。注意 `test_manager.py` 的 `test_ui_constructs` 现在应断言 7 个导航按钮/页。

- [ ] **Step 10: 提交**

```bash
git add manager.py tests/test_manager.py AGENTS.md README.md
git commit -m "feat(manager): add member-limit page with manual run, cancel and two scheduled slots"
```

---

## 人工验收清单（实现完成后，Windows 本地）

1. 运行 `python -m member_limit.main --dry-run --limit 3`（先确保 `.env` 有凭据）——应无头打开 Chrome、逐人打印当前值、结尾输出汇总、不实际修改。
2. 首次真实执行 `python -m member_limit.main --limit 3`——应自动登录并持久化会话到 `member_limit/chrome_profile/`。
3. 启动 `python manager.py` → 左侧出现「接待上限」页；手动执行一次，日志框实时滚动、可点「停止」逐人中断、完成输出五类汇总，`logs/member_limit.log` 有对应记录。
4. 勾选「预约1」填未来 HH:MM 时间 + 上限 → 到点自动执行一次、勾选复位、状态「已执行」；填过去时间 → 状态「已过期」。
5. 两行预约可填不同时间/不同上限，互不干扰；执行中再触发另一行 → 日志提示「已有执行在跑，本次触发跳过」。
