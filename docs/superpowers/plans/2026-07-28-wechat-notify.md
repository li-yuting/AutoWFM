# 企微消息推送与告警 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `collector/notify.py` 的企微 webhook 推送(常规定时 markdown 报表 + 排队告警 text)+ Playwright 截图,接入 `collector/scheduler.py` 的新 `push_job` 与 `ws_job` 内告警检查,新增 `config.yaml` 的 `notify` 块。

**Architecture:** 新增第 3 个 APScheduler 作业 `push_job`(`CronTrigger(minute="0,15,30,45")`,全局窗口挡)调 `notify.send_report(cfg)`(两条 markdown -> 各自 webhook -> 一张截图发两路);`ws_job` 采集循环后调 `notify.check_alerts(cfg)`(逐源判排队阈值,超阈值发 text 告警 @ 手机号)。所有外部 IO + 文案集中在 `collector/notify.py`,**不 import dashboard**。

**Tech Stack:** Python 3.14(.venv)、APScheduler(CronTrigger)、requests、Playwright(sync_api)、SQLite、PyYAML。

## Global Constraints

- Python 3.14 在 `.venv`,一律用 `.\.venv\Scripts\python.exe`,**绝不**用系统 Python。
- 任何中文输出前先 `$env:PYTHONIOENCODING="utf-8"`。
- **测试:plain assert,无 pytest。** 单测用 `-c` 导入调用;全量用 `python tests/test_notify.py`。
- **无 git(未安装):禁止 `git commit`。** 每个"Commit"步骤改为跑测试的 Checkpoint。
- 无 build 步骤。
- 列名为中文(SQLite 存 UTF-8,带引号);`storage.SCHEMAS` 的键即列名。
- **`collector/notify.py` 不得 import `dashboard`**(分层:collector 写 / dashboard 读)。
- **避免循环 import:** `scheduler.py` 顶层 `from collector import notify`;故 `notify.py` 不得在顶层 import scheduler。`check_alerts` 需 `_in_window` 时,在**函数体内**惰性 `from collector.scheduler import _in_window`。
- 阈值边界用 `>=`(排队达到阈值即告警);空闲<排队 仍为严格 `<`。
- 在线告警仅看 `排队>=20`(不加空闲条件);12378 告警需先过 12378 自己的 `schedule` 窗口(防周末 18:00 后陈旧误报)。

---

## File Structure

- **`collector/notify.py`**(由 stub 重写):数据读取(`latest_snapshot`/`forecast_at`/`_find_sub`)、渲染(`_render_firstline`/`_render_secondline`/`build_firstline_msg`/`build_secondline_msg`)、webhook(`_webhook`/`_send_md`/`_send_img`/`_send_text`)、截图(`take_screenshot`)、入口(`check_alerts`/`send_report`)。
- **`collector/scheduler.py`**(改):加 `push_job`、`ws_job` 末尾调 `notify.check_alerts`、`start()` 注册 `push_job`、加 `CronTrigger` import、清理 `detail_job` 末尾 stub 注释。
- **`config.yaml`**(改):新增 `notify:` 块。
- **`tests/test_notify.py`**(新建):sys.path bootstrap + plain assert + `main()`。

---

## Task 1: notify.py 骨架 + 数据读取

**Files:**
- Create: `tests/test_notify.py`
- Rewrite: `collector/notify.py`

**Interfaces:**
- Produces: `latest_snapshot(data_dir, source, date_str) -> dict|None`; `forecast_at(data_dir, line, now_str) -> int`; `_find_sub(cfg, name) -> dict|None`; `_n(v) -> int`; `_pct(num, den) -> str`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_notify.py`:

```python
# -*- coding: utf-8 -*-
import sys, os, tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector import storage, notify

def _cfg(data_dir):
    return {
        "schedule": {"timezone": "Asia/Shanghai", "window_start": "09:00", "window_end": "21:00"},
        "storage": {"dir": data_dir},
        "subs": [
            {"name": "热线"},
            {"name": "12378", "schedule": {"weekday": {"start": "08:30", "end": "21:00"},
                                            "weekend": {"start": "09:00", "end": "18:00"}}},
        ],
        "notify": {
            "screenshot_url": "http://localhost:5001/",
            "webhook": {"main_key": "MAIN", "secondary_key": "SECOND"},
            "alert": {"hotline_queue": 10, "online_queue": 20, "queue_12378": 1,
                      "recipients": {"hotline": ["111"], "online": ["222"], "12378": ["333"]}},
        },
    }

def test_latest_snapshot():
    d = tempfile.mkdtemp()
    storage.insert("热线", {"时间":"2026-07-28 09:05","转人工量":1,"接通量":1,"排队量":0,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1108,"接通量":1106,"排队量":0,"累计呼入量":1187,"外呼量":0,"外呼接通量":0}, d)
    row = notify.latest_snapshot(d, "热线", "2026-07-28")
    assert row["转人工量"] == 1108, row
    assert notify.latest_snapshot(d, "热线", "2026-07-29") is None
    assert notify.latest_snapshot(d, "在线", "2026-07-28") is None  # 无库
    print("latest_snapshot OK")

def test_forecast_at():
    d = tempfile.mkdtemp()
    with open(Path(d)/"预估流入量.csv", "w", encoding="utf-8", newline="") as f:
        f.write("时间,线路,时段预估量,累计预估量\n")
        f.write("2026-07-28 11:00,热线,100,1187\n")
        f.write("2026-07-28 11:00,在线,50,811\n")
    assert notify.forecast_at(d, "热线", "2026-07-28 11:00") == 1187
    assert notify.forecast_at(d, "在线", "2026-07-28 11:00") == 811
    assert notify.forecast_at(d, "热线", "2026-07-28 12:00") == 0
    print("forecast_at OK")

def main():
    test_latest_snapshot()
    test_forecast_at()
    print("ALL notify tests OK")

if __name__ == "__main__": main()
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_latest_snapshot, test_forecast_at; test_latest_snapshot(); test_forecast_at()"
```
Expected: FAIL — `AttributeError: module 'collector.notify' has no attribute 'latest_snapshot'`

- [ ] **Step 3: 重写 `collector/notify.py` 最小实现**

```python
# -*- coding: utf-8 -*-
"""企微 webhook 推送(定时 markdown 报表 + 排队告警)+ Playwright 截图。"""
import base64, csv, datetime, hashlib, logging, sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

log = logging.getLogger("autowfm")


def _n(v):
    """None/缺值 -> 0,否则原值。"""
    return v or 0


def _pct(num, den):
    """num/den 百分比,2 位小数;den<=0 返回 '0.00%'。"""
    return f"{num / den * 100:.2f}%" if den else "0.00%"


def _find_sub(cfg, name):
    for s in cfg["subs"]:
        if s["name"] == name:
            return s
    return None


def latest_snapshot(data_dir, source, date_str):
    """当天该源 时间 最大的一行(dict);无表/无数据返回 None。"""
    path = Path(data_dir) / f"{source}.db"
    if not path.exists():
        return None
    con = sqlite3.connect(str(path))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(t)").fetchall()]
        if not cols:
            return None
        row = con.execute(
            f'SELECT {",".join(chr(34) + c + chr(34) for c in cols)} FROM t '
            f'WHERE "时间" LIKE ? ORDER BY "时间" DESC LIMIT 1',
            (f"{date_str}%",),
        ).fetchone()
    finally:
        con.close()
    return dict(zip(cols, row)) if row else None


def forecast_at(data_dir, line, now_str):
    """预估流入量.csv 中 线路==line 且 时间==now_str 的 累计预估量;未命中返回 0。"""
    path = Path(data_dir) / "预估流入量.csv"
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("线路") == line and row.get("时间") == now_str:
                try:
                    return int(row["累计预估量"])
                except (ValueError, KeyError):
                    return 0
    return 0
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_latest_snapshot, test_forecast_at; test_latest_snapshot(); test_forecast_at()"
```
Expected: 打印 `latest_snapshot OK` / `forecast_at OK`

- [ ] **Step 5: Checkpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: `ALL notify tests OK`

---

## Task 2: 消息渲染 + 组装

**Files:**
- Modify: `collector/notify.py`(追加渲染/组装函数)
- Modify: `tests/test_notify.py`(追加渲染测试)

**Interfaces:**
- Consumes: Task 1 的 `latest_snapshot`/`forecast_at`/`_n`/`_pct`
- Produces: `_render_firstline(now_str, hot, hot_seat, ol, f_hot, f_ol) -> str`; `_render_secondline(now_str, groups, z12378, z12378_seat) -> str`; `build_firstline_msg(data_dir, now_str, date_str) -> str`; `build_secondline_msg(data_dir, now_str, date_str) -> str`。`groups` = `[(label, transfer:int, ticket:int, seat:dict|None), ...]`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 中 `test_forecast_at` 之后追加:

```python
def test_render_firstline():
    hot = {"转人工量":1108,"接通量":1106,"排队量":0,"累计呼入量":1187,"外呼量":0,"外呼接通量":0}
    hot_seat = {"签入":87,"通话":38,"空闲":42,"离席":0,"话后":5,"振铃":0,"置忙":0}
    ol = {"转人工量":826,"转人工失败":2,"排队":0,"咨询":75,"在线":32,"小休":5,"示忙":1,"话后":0,"就餐":0,"培训":0,"回访":0}
    s = notify._render_firstline("2026-07-28 11:00", hot, hot_seat, ol, 1187, 811)
    assert "统计监控`热线`" in s, s
    assert ">预测量: 1187, 转人工量：1108" in s, s
    assert ">流入率：93.34%" in s, s          # 1108/1187
    assert ">接通量：1106, 接通率：99.82%" in s, s  # 1106/1108
    assert "统计监控`在线`" in s, s
    assert ">接通量：824, 接通率：99.76%" in s, s  # 826-2=824, 824/826
    assert ">流入率：101.85%" in s, s          # 826/811
    assert ">示忙人数：1, 就餐人数：0" in s, s
    print("render_firstline OK")

def test_render_secondline():
    groups = [
        ("常规转接组", 100, 150, {"签入":17,"通话":8,"空闲":6,"离席":0,"话后":3,"振铃":0,"置忙":0}),
        ("贷后转接组", 100, 150, {"签入":18,"通话":5,"空闲":9,"离席":0,"话后":4,"振铃":0,"置忙":0}),
    ]
    z = {"转人工量":44,"接通量":44,"排队量":0,"累计呼入量":44}
    z_seat = {"签入":6,"通话":1,"空闲":5,"离席":0,"话后":0,"振铃":0,"置忙":0}
    s = notify._render_secondline("2026-07-28 11:15", groups, z, z_seat)
    assert "签入情况`常规转接组`" in s
    assert "签入情况`贷后转接组`" in s
    assert ">转接量：100, 工单量：150" in s
    assert "统计监控`12378`" in s
    assert ">转人工量：44" in s
    assert ">接通率：100.00%" in s  # 44/44
    print("render_secondline OK")
```

并在 `main()` 里 `test_forecast_at()` 之后加 `test_render_firstline()` / `test_render_secondline()`。

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_render_firstline, test_render_secondline; test_render_firstline(); test_render_secondline()"
```
Expected: FAIL — `AttributeError: module 'collector.notify' has no attribute '_render_firstline'`

- [ ] **Step 3: 实现 `_render_firstline` / `_render_secondline` / `build_*`**

在 `collector/notify.py` 末尾追加:

```python
def _render_firstline(now_str, hot, hot_seat, ol, f_hot, f_ol):
    if not hot:
        return ""
    hs = hot_seat or {}
    zrg = _n(hot.get("转人工量")); jtl = _n(hot.get("接通量")); pd = _n(hot.get("排队量"))
    s = (f"# 当前时间: {now_str}    \n"
         f"统计监控`热线`:     \n"
         f">预测量: {f_hot:.0f}, 转人工量：{zrg:.0f}    \n"
         f">流入率：{_pct(zrg, f_hot)}    \n"
         f">接通量：{jtl:.0f}, 接通率：{_pct(jtl, zrg)}    \n"
         f">排队量：{pd:.0f}    \n"
         f">签入人数：{_n(hs.get('签入'))}     \n"
         f">通话人数：{_n(hs.get('通话'))}, 话后人数：{_n(hs.get('话后'))}    \n"
         f">空闲人数：{_n(hs.get('空闲'))}, 置忙人数：{_n(hs.get('置忙'))}    \n\n")
    if ol:
        ozrg = _n(ol.get("转人工量")); osb = _n(ol.get("转人工失败")); ocg = ozrg - osb
        s += (f"统计监控`在线`:     \n"
              f">预测量: {f_ol:.0f}, 转人工量：{ozrg:.0f}    \n"
              f">流入率：{_pct(ozrg, f_ol)}    \n"
              f">接通量：{ocg:.0f}, 接通率：{_pct(ocg, ozrg)}    \n"
              f">排队量：{_n(ol.get('排队')):.0f}    \n"
              f">正在咨询人数：{_n(ol.get('咨询')):.0f}    \n"
              f">在线人数：{_n(ol.get('在线')):.0f}, 回访人数：{_n(ol.get('回访')):.0f}    \n"
              f">话后人数：{_n(ol.get('话后')):.0f}, 小休人数：{_n(ol.get('小休')):.0f}    \n"
              f">示忙人数：{_n(ol.get('示忙')):.0f}, 就餐人数：{_n(ol.get('就餐')):.0f}    \n")
    return s


def _render_secondline(now_str, groups, z12378, z12378_seat):
    s = f"# 当前时间：{now_str}    \n"
    for label, transfer, ticket, seat in groups:
        st = seat or {}
        s += (f"签入情况`{label}`:     \n"
              f">转接量：{transfer}, 工单量：{ticket}    \n"
              f">签入人数：{_n(st.get('签入'))}     \n"
              f">通话人数：{_n(st.get('通话'))}, 话后人数：{_n(st.get('话后'))}    \n"
              f">空闲人数：{_n(st.get('空闲'))}, 置忙人数：{_n(st.get('置忙'))}    \n"
              f">离席人数：{_n(st.get('离席'))}, 振铃人数：{_n(st.get('振铃'))}    \n\n")
    if z12378:
        zs = z12378_seat or {}
        zzrg = _n(z12378.get("转人工量")); zjtl = _n(z12378.get("接通量"))
        s += (f"统计监控`12378`:     \n"
              f">转人工量：{zzrg:.0f}    \n"
              f">接通量：{zjtl:.0f}, 接通率：{_pct(zjtl, zzrg)}    \n"
              f">排队量：{_n(z12378.get('排队量')):.0f}    \n"
              f">签入人数：{_n(zs.get('签入'))}     \n"
              f">通话人数：{_n(zs.get('通话'))}, 话后人数：{_n(zs.get('话后'))}    \n"
              f">空闲人数：{_n(zs.get('空闲'))}, 置忙人数：{_n(zs.get('置忙'))}    \n")
    return s


def build_firstline_msg(data_dir, now_str, date_str):
    hot = latest_snapshot(data_dir, "热线", date_str)
    if not hot:
        return ""
    hot_seat = latest_snapshot(data_dir, "热线明细", date_str)
    ol = latest_snapshot(data_dir, "在线", date_str)
    f_hot = forecast_at(data_dir, "热线", now_str)
    f_ol = forecast_at(data_dir, "在线", now_str)
    return _render_firstline(now_str, hot, hot_seat, ol, f_hot, f_ol)


def build_secondline_msg(data_dir, now_str, date_str):
    groups = []
    for label, seat_src, sess_cols, ticket_col in [
        ("常规转接组", "常规", ("转接一组", "转接二组"), "回访组一组"),
        ("贷后转接组", "贷后", ("贷后转接组",), "贷后回访组"),
    ]:
        seat = latest_snapshot(data_dir, seat_src, date_str)
        if not seat:
            continue
        sess = latest_snapshot(data_dir, "会话记录", date_str)
        transfer = sum(_n(sess.get(c)) for c in sess_cols) if sess else 0
        tickets = latest_snapshot(data_dir, "工单明细", date_str)
        ticket = _n(tickets.get(ticket_col)) if tickets else 0
        groups.append((label, transfer, ticket, seat))
    z12378 = latest_snapshot(data_dir, "12378", date_str)
    z12378_seat = latest_snapshot(data_dir, "12378明细", date_str)
    return _render_secondline(now_str, groups, z12378, z12378_seat)
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_render_firstline, test_render_secondline; test_render_firstline(); test_render_secondline()"
```
Expected: `render_firstline OK` / `render_secondline OK`

- [ ] **Step 5: Checkpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: `ALL notify tests OK`

---

## Task 3: webhook 发送

**Files:**
- Modify: `collector/notify.py`(追加 `_webhook`/`_send_md`/`_send_img`/`_send_text`)
- Modify: `tests/test_notify.py`

**Interfaces:**
- Produces: `_webhook(key:str, payload:dict) -> str`(永不抛异常);`_send_md(text, key) -> str`;`_send_img(path, key) -> str`;`_send_text(key, mobiles:list, msg) -> str`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 顶部 import 区加 `import base64, hashlib`,并在 `test_render_secondline` 之后追加:

```python
def test_webhook_success():
    fake = MagicMock(); fake.status_code = 200; fake.json.return_value = {"errcode": 0}; fake.text = "ok"
    with patch.object(notify.requests, "post", return_value=fake):
        assert notify._webhook("K", {"msgtype": "text", "text": {"content": "x"}}) == "ok"

def test_webhook_errcode():
    fake = MagicMock(); fake.status_code = 200; fake.json.return_value = {"errcode": 93000, "errmsg": "bad"}
    with patch.object(notify.requests, "post", return_value=fake):
        r = notify._webhook("K", {})
        assert isinstance(r, str) and "失败" in r

def test_webhook_exception():
    with patch.object(notify.requests, "post", side_effect=Exception("boom")):
        assert "失败" in notify._webhook("K", {})

def test_send_md_payload():
    with patch.object(notify, "_webhook", return_value="ok") as m:
        notify._send_md("hello", "KEY")
        m.assert_called_once_with("KEY", {"msgtype": "markdown", "markdown": {"content": "hello"}})

def test_send_text_payload():
    with patch.object(notify, "_webhook", return_value="ok") as m:
        notify._send_text("KEY", ["111", "222"], "hi")
        m.assert_called_once_with("KEY", {"msgtype": "text",
                                          "text": {"content": "hi", "mentioned_mobile_list": ["111", "222"]}})

def test_send_img_payload():
    d = tempfile.mkdtemp(); p = Path(d) / "t.png"; p.write_bytes(b"\x89PNG fake")
    with patch.object(notify, "_webhook", return_value="ok") as m:
        notify._send_img(str(p), "KEY")
        payload = m.call_args[0][1]
        assert payload["msgtype"] == "image"
        assert payload["image"]["base64"] == base64.b64encode(b"\x89PNG fake").decode()
        assert payload["image"]["md5"] == hashlib.md5(b"\x89PNG fake").hexdigest()
```

在 `main()` 里追加这 6 个测试调用。

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_webhook_success; test_webhook_success()"
```
Expected: FAIL — `AttributeError: ... has no attribute '_webhook'`

- [ ] **Step 3: 实现发送函数**

在 `collector/notify.py` 末尾追加:

```python
def _webhook(key, payload):
    try:
        resp = requests.post(f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}",
                             json=payload, timeout=30)
        if resp.status_code != 200:
            log.warning(f"webhook HTTP {resp.status_code}: {resp.text}")
            return f"webhook 失败: HTTP {resp.status_code}"
        r = resp.json()
        if r.get("errcode") != 0:
            log.warning(f"webhook errcode={r['errcode']}: {r.get('errmsg', '')}")
            return f"webhook 失败: errcode={r['errcode']}"
        return resp.text
    except Exception as e:
        return f"webhook 失败: {e}"


def _send_md(text, key):
    return _webhook(key, {"msgtype": "markdown", "markdown": {"content": text}})


def _send_img(path, key):
    with open(path, "rb") as f:
        data = f.read()
    return _webhook(key, {"msgtype": "image",
                          "image": {"base64": base64.b64encode(data).decode(),
                                    "md5": hashlib.md5(data).hexdigest()}})


def _send_text(key, mobiles, msg):
    return _webhook(key, {"msgtype": "text",
                          "text": {"content": msg, "mentioned_mobile_list": mobiles}})
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_webhook_success, test_webhook_errcode, test_webhook_exception, test_send_md_payload, test_send_text_payload, test_send_img_payload; [f() for f in [test_webhook_success, test_webhook_errcode, test_webhook_exception, test_send_md_payload, test_send_text_payload, test_send_img_payload]]"
```
Expected: 无输出(全过,无 assert 触发)

- [ ] **Step 5: Checkpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: `ALL notify tests OK`

---

## Task 4: Playwright 截图

**Files:**
- Modify: `collector/notify.py`(追加 `take_screenshot`)
- Modify: `tests/test_notify.py`

**Interfaces:**
- Produces: `take_screenshot(url:str) -> str|None`(成功返回 png 路径,失败返回 None,永不抛)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 追加(只测异常路径,真实截图留手动冒烟):

```python
def test_take_screenshot_failure():
    with patch("playwright.sync_api.sync_playwright") as sp:
        sp.side_effect = Exception("no browser")
        assert notify.take_screenshot("http://localhost:5001/") is None
    print("take_screenshot_failure OK")
```

在 `main()` 里追加 `test_take_screenshot_failure()`。

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_take_screenshot_failure; test_take_screenshot_failure()"
```
Expected: FAIL — `AttributeError: ... has no attribute 'take_screenshot'`

- [ ] **Step 3: 实现 `take_screenshot`**

在 `collector/notify.py` 末尾追加:

```python
def take_screenshot(url):
    """Playwright 截图 -> data/screenshot.png;失败返回 None。"""
    from playwright.sync_api import sync_playwright
    Path("data").mkdir(exist_ok=True)
    path = str(Path("data") / "screenshot.png")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page(viewport={"width": 1920, "height": 1080})
            try:
                pg.goto(url, wait_until="networkidle", timeout=30000)
                pg.wait_for_timeout(4000)   # 等 Chart.js 渲染(本看板无 updateTime 标记)
                pg.screenshot(path=path, full_page=True)
                log.info(f"截图已保存: {path}")
                return path
            finally:
                b.close()
    except Exception as e:
        log.error(f"截图失败: {e}")
        return None
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_take_screenshot_failure; test_take_screenshot_failure()"
```
Expected: `take_screenshot_failure OK`

- [ ] **Step 5: Checkpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: `ALL notify tests OK`

- [ ] **Step 6: 一次性准备(仅首次)**

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```
(playwright 包已在 venv;此步装浏览器二进制。失败则真实截图会返回 None,markdown 照发。)

---

## Task 5: check_alerts(排队告警)

**Files:**
- Modify: `collector/notify.py`(追加 `check_alerts`)
- Modify: `tests/test_notify.py`

**Interfaces:**
- Consumes: Task 1 的 `latest_snapshot`/`_find_sub`/`_n`;Task 3 的 `_send_text`;**惰性** `from collector.scheduler import _in_window`。
- Produces: `check_alerts(cfg:dict, now:datetime|None=None) -> None`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 顶部 import 区加 `import datetime`,追加 helper 与测试:

```python
def _capture_alerts(cfg, now):
    calls = []
    with patch.object(notify, "_send_text", lambda key, mob, msg: calls.append((key, mob, msg)) or "ok"):
        notify.check_alerts(cfg, now=now)
    return calls

def test_check_alerts_hotline():
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周二
    # 排队=10(>=阈值) 空闲=3(<10) -> 告警
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":10,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":5,"通话":1,"空闲":3,"离席":0,"话后":1,"振铃":0,"置忙":0}, d)
    calls = _capture_alerts(cfg, now)
    assert any("热线排队" in c[2] and c[0] == "MAIN" for c in calls), calls
    # 排队=9(<阈值) -> 不告警
    d2 = tempfile.mkdtemp(); cfg2 = _cfg(d2)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":9,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d2)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":5,"通话":1,"空闲":0,"离席":0,"话后":1,"振铃":0,"置忙":0}, d2)
    assert _capture_alerts(cfg2, now) == [], "排队9不应告警"
    # 排队=10 空闲=10(>=排队) -> 不告警
    d3 = tempfile.mkdtemp(); cfg3 = _cfg(d3)
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":10,"累计呼入量":1,"外呼量":0,"外呼接通量":0}, d3)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":5,"通话":1,"空闲":10,"离席":0,"话后":1,"振铃":0,"置忙":0}, d3)
    assert _capture_alerts(cfg3, now) == [], "空闲>=排队不应告警"
    print("check_alerts_hotline OK")

def test_check_alerts_online():
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("在线", {"时间":"2026-07-28 11:00","转人工量":1,"转人工失败":0,"排队":20,"咨询":0,"在线":5,"小休":0,"示忙":0,"话后":0,"就餐":0,"培训":0,"回访":0}, d)
    assert any("在线排队" in c[2] for c in _capture_alerts(cfg, now))   # =20 告警
    d2 = tempfile.mkdtemp(); cfg2 = _cfg(d2)
    storage.insert("在线", {"时间":"2026-07-28 11:00","转人工量":1,"转人工失败":0,"排队":19,"咨询":0,"在线":5,"小休":0,"示忙":0,"话后":0,"就餐":0,"培训":0,"回访":0}, d2)
    assert _capture_alerts(cfg2, now) == [], "排队19不应告警"
    print("check_alerts_online OK")

def test_check_alerts_12378():
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周二,在 12378 窗口
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("12378", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":1,"累计呼入量":1}, d)
    storage.insert("12378明细", {"时间":"2026-07-28 11:00","签入":3,"通话":1,"空闲":0,"离席":0,"话后":0,"振铃":0,"置忙":0}, d)
    assert any("12378排队" in c[2] and c[0] == "SECOND" for c in _capture_alerts(cfg, now))  # =1 告警
    d2 = tempfile.mkdtemp(); cfg2 = _cfg(d2)
    storage.insert("12378", {"时间":"2026-07-28 11:00","转人工量":1,"接通量":1,"排队量":0,"累计呼入量":1}, d2)
    storage.insert("12378明细", {"时间":"2026-07-28 11:00","签入":3,"通话":1,"空闲":0,"离席":0,"话后":0,"振铃":0,"置忙":0}, d2)
    assert _capture_alerts(cfg2, now) == [], "排队0不应告警"
    print("check_alerts_12378 OK")

def test_check_alerts_12378_window():
    now = datetime.datetime(2026, 8, 1, 18, 30, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周六
    assert now.weekday() == 5, now.weekday()
    d = tempfile.mkdtemp(); cfg = _cfg(d)
    storage.insert("12378", {"时间":"2026-08-01 18:00","转人工量":1,"接通量":1,"排队量":5,"累计呼入量":1}, d)
    storage.insert("12378明细", {"时间":"2026-08-01 18:00","签入":3,"通话":1,"空闲":0,"离席":0,"话后":0,"振铃":0,"置忙":0}, d)
    assert all("12378排队" not in c[2] for c in _capture_alerts(cfg, now)), "出窗口不应告警"
    print("check_alerts_12378_window OK")
```

> 注:`tests/test_notify.py` 顶部需 `from zoneinfo import ZoneInfo`。把 `import datetime` 与 `from zoneinfo import ZoneInfo` 一并加到 import 区。

在 `main()` 里追加这 4 个测试调用。

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_check_alerts_hotline; test_check_alerts_hotline()"
```
Expected: FAIL — `AttributeError: ... has no attribute 'check_alerts'`

- [ ] **Step 3: 实现 `check_alerts`**

在 `collector/notify.py` 末尾追加:

```python
def check_alerts(cfg, now=None):
    """逐源判排队阈值,超阈值发 text 告警。热线/在线 走全局窗口(由调用方 ws_job 保证);
    12378 走自己的 schedule(防周末 18:00 后陈旧误报)。"""
    from collector.scheduler import _in_window  # 惰性,避免循环 import
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.datetime.now(tz)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    date_str = now.strftime("%Y-%m-%d")
    data_dir = cfg["storage"]["dir"]
    alert = cfg["notify"]["alert"]
    wh = cfg["notify"]["webhook"]
    rcpt = alert["recipients"]

    hot = latest_snapshot(data_dir, "热线", date_str)
    if hot:
        q = _n(hot.get("排队量")); idle = _n((latest_snapshot(data_dir, "热线明细", date_str) or {}).get("空闲"))
        if q >= alert["hotline_queue"] and idle < q:
            msg = f"⚠️ 排队告警 {now_str}\n热线排队：{q} 人（阈值 {alert['hotline_queue']}，空闲 {idle}）"
            log.info(_send_text(wh["main_key"], rcpt["hotline"], msg))

    ol = latest_snapshot(data_dir, "在线", date_str)
    if ol:
        q = _n(ol.get("排队"))
        if q >= alert["online_queue"]:
            msg = f"⚠️ 排队告警 {now_str}\n在线排队：{q} 人（阈值 {alert['online_queue']}）"
            log.info(_send_text(wh["main_key"], rcpt["online"], msg))

    sub12378 = _find_sub(cfg, "12378")
    if sub12378 and _in_window(cfg, sub12378, now):
        z = latest_snapshot(data_dir, "12378", date_str)
        if z:
            q = _n(z.get("排队量")); idle = _n((latest_snapshot(data_dir, "12378明细", date_str) or {}).get("空闲"))
            if q >= alert["queue_12378"] and idle < q:
                msg = f"⚠️ 12378排队告警 {now_str}\n12378排队：{q} 人（阈值 {alert['queue_12378']}，空闲 {idle}）"
                log.info(_send_text(wh["secondary_key"], rcpt["12378"], msg))
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_check_alerts_hotline, test_check_alerts_online, test_check_alerts_12378, test_check_alerts_12378_window; [f() for f in [test_check_alerts_hotline, test_check_alerts_online, test_check_alerts_12378, test_check_alerts_12378_window]]"
```
Expected: 4 行 `... OK`

- [ ] **Step 5: Checkpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: `ALL notify tests OK`

---

## Task 6: send_report(定时推送入口)

**Files:**
- Modify: `collector/notify.py`(追加 `send_report`)
- Modify: `tests/test_notify.py`

**Interfaces:**
- Consumes: Task 2 的 `build_firstline_msg`/`build_secondline_msg`;Task 3 的 `_send_md`/`_send_img`;Task 4 的 `take_screenshot`。
- Produces: `send_report(cfg:dict, now:datetime|None=None) -> None`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 追加:

```python
def _seed_full(d):
    storage.insert("热线", {"时间":"2026-07-28 11:00","转人工量":1108,"接通量":1106,"排队量":0,"累计呼入量":1187,"外呼量":0,"外呼接通量":0}, d)
    storage.insert("热线明细", {"时间":"2026-07-28 11:00","签入":87,"通话":38,"空闲":42,"离席":0,"话后":5,"振铃":0,"置忙":0}, d)
    storage.insert("在线", {"时间":"2026-07-28 11:00","转人工量":826,"转人工失败":2,"排队":0,"咨询":75,"在线":32,"小休":5,"示忙":1,"话后":0,"就餐":0,"培训":0,"回访":0}, d)
    storage.insert("常规", {"时间":"2026-07-28 11:00","签入":17,"通话":8,"空闲":6,"离席":0,"话后":3,"振铃":0,"置忙":0}, d)
    storage.insert("贷后", {"时间":"2026-07-28 11:00","签入":18,"通话":5,"空闲":9,"离席":0,"话后":4,"振铃":0,"置忙":0}, d)
    storage.insert("12378", {"时间":"2026-07-28 11:00","转人工量":44,"接通量":44,"排队量":0,"累计呼入量":44}, d)
    storage.insert("12378明细", {"时间":"2026-07-28 11:00","签入":6,"通话":1,"空闲":5,"离席":0,"话后":0,"振铃":0,"置忙":0}, d)
    storage.insert("会话记录", {"时间":"2026-07-28 11:00","转接一组":60,"转接二组":40,"贷后转接组":100}, d)
    storage.insert("工单明细", {"时间":"2026-07-28 11:00","二线客诉处理组":0,"常规工单处理组":0,"回访组一组":150,"贷后回访组":150,"12378回访组":0}, d)
    with open(Path(d)/"预估流入量.csv", "w", encoding="utf-8", newline="") as f:
        f.write("时间,线路,时段预估量,累计预估量\n2026-07-28 11:00,热线,100,1187\n2026-07-28 11:00,在线,50,811\n")

def test_send_report():
    d = tempfile.mkdtemp(); cfg = _cfg(d); _seed_full(d)
    now = datetime.datetime(2026, 7, 28, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    md_calls = []; img_calls = []
    with patch.object(notify, "_send_md", lambda text, key: md_calls.append((text, key)) or "ok"), \
         patch.object(notify, "_send_img", lambda path, key: img_calls.append((path, key)) or "ok"), \
         patch.object(notify, "take_screenshot", return_value="data/s.png"):
        notify.send_report(cfg, now=now)
    keys = [c[1] for c in md_calls]
    assert "MAIN" in keys and "SECOND" in keys, md_calls
    assert [c[1] for c in img_calls] == ["MAIN", "SECOND"], img_calls
    assert "统计监控`热线`" in md_calls[0][0]
    assert ">预测量: 1187, 转人工量：1108" in md_calls[0][0]   # forecast 命中
    assert "统计监控`12378`" in md_calls[1][0]
    print("send_report OK")
```

在 `main()` 里追加 `test_send_report()`。

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_send_report; test_send_report()"
```
Expected: FAIL — `AttributeError: ... has no attribute 'send_report'`

- [ ] **Step 3: 实现 `send_report`**

在 `collector/notify.py` 末尾追加:

```python
def send_report(cfg, now=None):
    """定时推送入口:两条 markdown -> 各自 webhook -> 一张截图发两路。窗口由 push_job 挡。"""
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.datetime.now(tz)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    date_str = now.strftime("%Y-%m-%d")
    data_dir = cfg["storage"]["dir"]
    wh = cfg["notify"]["webhook"]
    try:
        msg1 = build_firstline_msg(data_dir, now_str, date_str)
        if msg1:
            log.info(_send_md(msg1, wh["main_key"]))
        msg2 = build_secondline_msg(data_dir, now_str, date_str)
        if msg2:
            log.info(_send_md(msg2, wh["secondary_key"]))
        ss = take_screenshot(cfg["notify"]["screenshot_url"])
        if ss:
            log.info(_send_img(ss, wh["main_key"]))
            log.info(_send_img(ss, wh["secondary_key"]))
    except Exception:
        log.exception("send_report 异常")
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_send_report; test_send_report()"
```
Expected: `send_report OK`

- [ ] **Step 5: Checkpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Expected: `ALL notify tests OK`

---

## Task 7: 接入 scheduler.py + config.yaml

**Files:**
- Modify: `collector/scheduler.py`
- Modify: `config.yaml`
- Modify: `tests/test_notify.py`(config + push_job 测试)

**Interfaces:**
- Consumes: Task 5/6 的 `notify.check_alerts(cfg)` / `notify.send_report(cfg)`;现有 `_in_window`/`_now`。
- Produces: `scheduler.push_job(cfg)`;`config.yaml` 的 `notify` 块。

- [ ] **Step 1: 写失败测试**

在 `tests/test_notify.py` 追加:

```python
def test_config_notify_block():
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    n = cfg["notify"]
    assert n["webhook"]["main_key"]
    assert n["webhook"]["secondary_key"]
    assert n["alert"]["hotline_queue"] == 10
    assert n["alert"]["online_queue"] == 20
    assert n["alert"]["queue_12378"] == 1
    assert n["screenshot_url"].startswith("http://")
    print("config_notify_block OK")

def test_push_job_window_gate():
    from collector import scheduler
    # 出窗口(空窗口 (9,9] 永不成立)
    cfg_out = _cfg(tempfile.mkdtemp())
    cfg_out["schedule"]["window_start"] = "09:00"; cfg_out["schedule"]["window_end"] = "09:00"
    called = []
    with patch.object(notify, "send_report", lambda c: called.append(c)):
        scheduler.push_job(cfg_out)
    assert called == [], "出窗口不应调 send_report"
    # 在窗口(全天)
    cfg_in = _cfg(tempfile.mkdtemp())
    cfg_in["schedule"]["window_start"] = "00:00"; cfg_in["schedule"]["window_end"] = "23:59"
    called2 = []
    with patch.object(notify, "send_report", lambda c: called2.append(c)):
        scheduler.push_job(cfg_in)
    assert len(called2) == 1, "在窗口应调 send_report"
    print("push_job_window_gate OK")
```

在 `main()` 里追加这两个测试调用。

- [ ] **Step 2: 跑测试确认失败**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_config_notify_block, test_push_job_window_gate; test_config_notify_block(); test_push_job_window_gate()"
```
Expected: FAIL — config.yaml 无 `notify` 键 / scheduler 无 `push_job`

- [ ] **Step 3: 改 `config.yaml`**

在 `logging:` 块之后、`seat_data: &seat_data` 之前插入:

```yaml
notify:
  screenshot_url: "http://localhost:5001/"   # 改8080后换这里
  push_minutes: [0, 15, 30, 45]
  webhook:
    main_key: "6702efeb-5787-4285-948d-93ebb6f29c7d"      # 一线 + 截图
    secondary_key: "c816bbf5-c34c-4b7e-93b3-578a891e68dd"  # 二线 + 截图
  alert:
    hotline_queue: 10
    online_queue: 20
    queue_12378: 1
    recipients:
      hotline: ["17629050914", "18829270926"]
      online: ["17629050914", "18821657478"]
      "12378": ["17629050914", "18629552489"]
```

- [ ] **Step 4: 改 `collector/scheduler.py`**

(a) 第 7 行 import 改为同时引入 `CronTrigger`:

```python
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
```

(b) `ws_job` 末尾(现有 `log.info(f"[WS] 周期完成 ...")` 之后)追加告警调用:

```python
    try:
        notify.check_alerts(cfg)
    except Exception:
        log.exception("[alert] check_alerts 异常")
```

(c) `detail_job` 末尾删除 stub 注释两行:

```python
    # ponytail: 告警/截图入口已留,调用时机后续补充
    # notify.send_alert(...); notify.take_screenshot(cfg.get("screenshot_url"))
```

(d) 在 `detail_job` 之后、`start` 之前新增 `push_job`:

```python
def push_job(cfg):
    if not _in_window(cfg, None):
        return
    notify.send_report(cfg)
```

(e) `start()` 中,在 `sched.add_job(... id="detail")` 之后注册 push_job:

```python
    push_trig = CronTrigger(minute="0,15,30,45", timezone=tz)
    sched.add_job(push_job, push_trig, args=[cfg], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="push")
```

- [ ] **Step 5: 跑新测试确认通过**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_config_notify_block, test_push_job_window_gate; test_config_notify_block(); test_push_job_window_gate()"
```
Expected: `config_notify_block OK` / `push_job_window_gate OK`

- [ ] **Step 6: 全量回归**

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```
Expected: `test_notify.py` 打 `ALL notify tests OK`;其余既有测试全过,无回归。

- [ ] **Step 7: 手动冒烟(真实 webhook,可选但建议)**

确认看板在跑(`python -m dashboard.app`),然后:

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import yaml; from collector import notify; notify.send_report(yaml.safe_load(open('config.yaml',encoding='utf-8')))"
```

检查企微群:一线群收到 热线+在线 markdown + 截图;二线群收到 常规+贷后+12378 markdown + 截图。

告警冒烟(临时把 `config.yaml` 里某阈值改成 0 触发,验证后改回):

```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import yaml; from collector import notify; notify.check_alerts(yaml.safe_load(open('config.yaml',encoding='utf-8')))"
```

---

## Self-Review 结果

**1. Spec 覆盖:**
- §2 架构(独立 push_job + ws_job 内 check_alerts + notify.py 自包含)-> Task 6/7/5 ✓
- §3 字段映射(9 db latest + CSV forecast + 派生指标)-> Task 1/2 ✓
- §3 缺数据跳过 section(`if not hot: return ""`、`if not seat: continue`、`if z12378:`)-> Task 2 ✓
- §3 告警窗口(12378 自己 schedule 挡)-> Task 5 ✓
- §4 消息/告警格式 -> Task 2/5 ✓
- §5 截图(networkidle+4s,一张发两路)-> Task 4/6 ✓
- §6 config notify 块 -> Task 7 ✓
- §7 调度接入(CronTrigger minute="0,15,30,45" + _in_window 挡 + ws_job 调 check_alerts)-> Task 7 ✓
- §8 错误处理(_webhook 不抛、send_report 外层 try/except、截图失败跳过)-> Task 3/4/6 ✓
- §9 测试(latest_snapshot/forecast_at/渲染/check_alerts 阈值+窗口)-> Task 1/2/5 ✓

**2. 占位符扫描:** 无 TBD/TODO;每步含完整代码与命令。

**3. 类型一致:** `build_*_msg(data_dir, now_str, date_str)`、`check_alerts(cfg, now=None)`、`send_report(cfg, now=None)`、`take_screenshot(url)`、`_send_text(key, mobiles, msg)` 在定义与调用处签名一致;`groups` 元组形状 `(label, transfer:int, ticket:int, seat:dict|None)` 在 `_render_secondline` 与 `build_secondline_msg` 一致。

**4. 已知边界(非缺陷):** `forecast_at` 精确匹配 `时间==now_str`(CSV 15-min 对齐 :00/:15/:30/:45,推送同节拍);超 CSV 范围(2026-06-01…2026-08-15)或 12378 无 CSV 预测量 -> 0/无该行,符合 spec §3。
