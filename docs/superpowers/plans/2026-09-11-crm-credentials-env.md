# CRM 凭据迁移到 `.env` 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CRM 登录账号密码从 `login.json` 迁移到 `.env`，删除 `login.json`，并保持 token 自动刷新可用。

**Architecture:** `抓取Token.py` 通过 `python-dotenv` 读取环境变量，纯凭据校验函数接受映射以便测试；自动刷新子进程继续继承采集器环境。真实凭据只写入本地 `.env`。

**Tech Stack:** Python 3.14、python-dotenv、Playwright、PowerShell、纯 assert 测试脚本。

## Global Constraints

- CRM 凭据变量固定为 `AUTOWFM_CRM_USERNAME`、`AUTOWFM_CRM_PASSWORD`。
- 不保留 `login.json` 兼容回退。
- 不打印或提交账号、密码、完整 token。
- 测试使用 `.\.venv\Scripts\python.exe`，不使用 pytest。
- 现有未提交的 `member_limit/core.py` 和 `tests/test_member_limit_core.py` 修改不得触碰。

---

### Task 1: 从环境变量读取 CRM 凭据

**Files:**
- Create: `tests/test_fetch_token.py`
- Modify: `抓取Token.py`

**Interfaces:**
- Produces: `抓取Token.read_login(env=None) -> tuple[str, str]`
- Consumes: 环境变量 `AUTOWFM_CRM_USERNAME`、`AUTOWFM_CRM_PASSWORD`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fetch = importlib.import_module("抓取Token")


def test_read_login_from_env():
    creds = fetch.read_login({
        "AUTOWFM_CRM_USERNAME": "user",
        "AUTOWFM_CRM_PASSWORD": "password",
    })
    assert creds == ("user", "password")


def test_read_login_requires_both_values():
    for env in ({}, {"AUTOWFM_CRM_USERNAME": "user"}, {"AUTOWFM_CRM_PASSWORD": "password"}):
        try:
            fetch.read_login(env)
        except SystemExit as exc:
            assert "AUTOWFM_CRM_USERNAME" in str(exc)
            assert "AUTOWFM_CRM_PASSWORD" in str(exc)
        else:
            raise AssertionError("缺少凭据时应抛出 SystemExit")


if __name__ == "__main__":
    test_read_login_from_env()
    test_read_login_requires_both_values()
    print("test_fetch_token OK")
```

- [ ] **Step 2: 确认测试失败**

Run:

```powershell
.\.venv\Scripts\python.exe tests\test_fetch_token.py
```

Expected: FAIL，提示 `read_login()` 参数数量或凭据缺失行为不符合测试。

- [ ] **Step 3: 最小实现**

在 `抓取Token.py` 中删除 `json`、`LOGIN_FILE` 和旧版文件读取函数，改为：

```python
from dotenv import load_dotenv

from token_store import (
    TOKEN_FILE,
    ENV_FILE,
    mask_token,
    extract_token_from_post_data,
    save_token,
    update_env_token,
)


def read_login(env=None):
    """从环境变量读取 CRM 账号密码；env 参数用于测试注入。"""
    env = os.environ if env is None else env
    user = str(env.get("AUTOWFM_CRM_USERNAME") or "").strip()
    password = str(env.get("AUTOWFM_CRM_PASSWORD") or "").strip()
    if not user or not password:
        raise SystemExit(
            "请在 .env 配置 AUTOWFM_CRM_USERNAME 和 AUTOWFM_CRM_PASSWORD"
        )
    return user, password
```

`main()` 开头改为：

```python
    load_dotenv(ENV_FILE)
    user, password = read_login()
```

删除原来的 `if os.path.exists(LOGIN_FILE)` 读取逻辑。将“未找到 login.json”的提示改为：

```python
                print("未捕获到登录请求，请检查 .env 中的账号密码")
```

- [ ] **Step 4: 确认测试通过**

Run:

```powershell
.\.venv\Scripts\python.exe tests\test_fetch_token.py
```

Expected: `test_fetch_token OK`

- [ ] **Step 5: 提交**

```powershell
& $git add -- tests/test_fetch_token.py 抓取Token.py
& $git commit -m "feat(token): 从环境变量读取 CRM 凭据"
```

---

### Task 2: 更新配置模板与项目说明

**Files:**
- Modify: `.env.example`
- Modify: `config.example.yaml`
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: 新环境变量说明，无代码接口。

- [ ] **Step 1: 更新 `.env.example`**

在 CRM 区域加入：

```dotenv
# CRM 登录账密(抓取Token.py 用；仅保存在本地 .env，不入库)
AUTOWFM_CRM_USERNAME=
AUTOWFM_CRM_PASSWORD=
```

将原说明中的 `登录账密放根目录 login.json(不入库)` 改为：

```text
登录账密读取本文件的 AUTOWFM_CRM_USERNAME / AUTOWFM_CRM_PASSWORD。
```

- [ ] **Step 2: 更新 `config.example.yaml` 注释**

将该段末尾改为：

```yaml
# token 失效时会自动运行 抓取Token.py 刷新，写入 token.json 并回填 .env；
# 登录账密读取 .env 的 AUTOWFM_CRM_USERNAME / AUTOWFM_CRM_PASSWORD。
```

- [ ] **Step 3: 更新 `AGENTS.md`**

把两处 `login.json` 凭据说明改为：

```text
CRM 登录账密放 `.env` 的 `AUTOWFM_CRM_USERNAME` / `AUTOWFM_CRM_PASSWORD`；敏感文件 `token.json`、`storage_state.json` 不入 git。
```

保留 `.gitignore` 中的 `login.json` 规则，防止历史文件误提交。

- [ ] **Step 4: 语法与文本检查**

Run:

```powershell
rg -n "login\.json|AUTOWFM_CRM_(USERNAME|PASSWORD)" .env.example config.example.yaml AGENTS.md
```

Expected: `login.json` 只出现在保留忽略规则的说明中；两个新变量在两个模板/说明中出现。

- [ ] **Step 5: 提交**

```powershell
& $git add -- .env.example config.example.yaml AGENTS.md
& $git commit -m "docs: 更新 CRM 凭据配置说明"
```

---

### Task 3: 迁移真实凭据并删除 `login.json`

**Files:**
- Modify: `.env`
- Delete: `login.json`

**Interfaces:**
- Consumes: `D:\PythonProject\data\login.json`
- Produces: AutoWFM `.env` 中的可用 CRM 账号密码。

- [ ] **Step 1: 迁移凭据且不输出明文**

Run:

```powershell
@'
import json
from dotenv import set_key

with open(r"D:\PythonProject\data\login.json", encoding="utf-8") as f:
    login = json.load(f)

set_key(".env", "AUTOWFM_CRM_USERNAME", login["username"])
set_key(".env", "AUTOWFM_CRM_PASSWORD", login["password"])
'@ | .\.venv\Scripts\python.exe -
```

Expected: exit 0，无账号密码输出。

- [ ] **Step 2: 确认变量存在但不打印值**

Run:

```powershell
@'
from dotenv import dotenv_values
v = dotenv_values(".env")
assert v.get("AUTOWFM_CRM_USERNAME")
assert v.get("AUTOWFM_CRM_PASSWORD")
print("credentials configured")
'@ | .\.venv\Scripts\python.exe -
```

Expected: `credentials configured`

- [ ] **Step 3: 删除 `login.json`**

Run:

```powershell
$workspace = (Resolve-Path '.').Path
$target = (Resolve-Path '.\login.json').Path
if ((Split-Path -Parent $target) -ne $workspace) { throw "拒绝删除工作区外文件: $target" }
Remove-Item -LiteralPath $target
```

Expected: `login.json` 不存在，其他文件不变。

- [ ] **Step 4: 运行无头 token 刷新**

Run:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe 抓取Token.py --headless
```

Expected: 输出“会话仍有效”或“已尝试自动登录”，随后输出脱敏 token 并写入 `token.json`。

- [ ] **Step 5: 验证两个明细下载源**

Run:

```powershell
@'
from collector._utils import load_cfg
from collector import detail

cfg = load_cfg()
for name, mode in cfg["detail_modes"].items():
    counts = detail.download_and_count(
        name, mode, cfg["secrets"], "2026-09-11", cfg["detail"]["timeout"]
    )
    print(name, "ok", sum(counts.values()))
'@ | .\.venv\Scripts\python.exe -
```

Expected: `会话记录 ok ...` 和 `工单明细 ok ...`，无 `code=9003`。

- [ ] **Step 6: 全量测试**

Run:

```powershell
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

Expected: 所有测试脚本退出码为 0。

- [ ] **Step 7: 提交代码与文档**

```powershell
& $git add -- 抓取Token.py tests/test_fetch_token.py .env.example config.example.yaml AGENTS.md
& $git commit -m "feat(token): CRM 凭据迁移到 .env"
```
