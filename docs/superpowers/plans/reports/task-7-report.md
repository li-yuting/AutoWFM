# Task 7 Report — Wire notify into scheduler + config

**Date:** 2026-07-28
**Task:** Final task of the WeChat-notify feature. Wire `collector/notify.send_report` / `check_alerts` (built in Tasks 1–6) into `collector/scheduler.py`, add the `notify` config block to `config.yaml`, and add 2 TDD tests + a full regression run.

---

## 1. What was implemented

| Change | File | Detail |
|---|---|---|
| 2 new tests | `tests/test_notify.py` | `test_config_notify_block` (validates the real `config.yaml` notify block) + `test_push_job_window_gate` (validates `push_job` gates on the global window). Both added to `main()`. |
| notify config block | `config.yaml` | Inserted between the `logging:` section and the `seat_data: &seat_data` anchor. Contains `screenshot_url`, `push_minutes`, `webhook` (main/secondary keys), `alert` thresholds, and `recipients`. |
| CronTrigger import | `collector/scheduler.py` | `from apscheduler.triggers.cron import CronTrigger` after the `IntervalTrigger` import. |
| `check_alerts` call | `collector/scheduler.py` | In `ws_job`, after the `[WS] 周期完成` log line, wrapped in `try/except` so alert failures never block collection. |
| Stale comments removed | `collector/scheduler.py` | Deleted the two `# ponytail:` / `# notify.send_alert(...)` comment lines in `detail_job` (referenced the old stubs that no longer exist). |
| `push_job` function | `collector/scheduler.py` | New function between `detail_job` and `start`. Gates on `_in_window(cfg, None)` (global window) then calls `notify.send_report(cfg)`. |
| `push` APScheduler job | `collector/scheduler.py` | In `start(cfg)`, a `CronTrigger(minute="0,15,30,45")` job (`id="push"`, `max_instances=1`, `coalesce=True`, `misfire_grace_time=60`) registered after the `detail` job. |

No other code was refactored. `collector/notify.py` was not modified (it already had `send_report` / `check_alerts` from Tasks 1–6, and does not import `dashboard`).

---

## 2. TDD evidence

### RED (Step 2) — before any production change

Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_config_notify_block, test_push_job_window_gate; test_config_notify_block(); test_push_job_window_gate()"
```
Output:
```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    ...
  File "D:\PythonProject\AutoWFM\tests\test_notify.py", line 220, in test_config_notify_block
    n = cfg["notify"]
        ~~~^^^^^^^^^^
KeyError: 'notify'
```
Fails exactly as expected: `config.yaml` had no `notify` key (and `scheduler.push_job` did not yet exist). RED confirmed.

### GREEN (Step 5) — after config + scheduler edits

Same command, output:
```
config_notify_block OK
push_job_window_gate OK
```
GREEN confirmed.

---

## 3. Exact production changes

### `config.yaml` — inserted after `logging:` block, before `seat_data: &seat_data`

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

### `collector/scheduler.py` — five edits

**(a) Imports** (line 8, new):
```python
from apscheduler.triggers.cron import CronTrigger
```

**(b) `ws_job`** — after the `log.info(f"[WS] 周期完成 ...")` line:
```python
    try:
        notify.check_alerts(cfg)
    except Exception:
        log.exception("[alert] check_alerts 异常")
```

**(c) `detail_job`** — deleted:
```python
    # ponytail: 告警/截图入口已留,调用时机后续补充
    # notify.send_alert(...); notify.take_screenshot(cfg.get("screenshot_url"))
```

**(d) New `push_job`** — between `detail_job` and `def start(cfg):`:
```python
def push_job(cfg):
    if not _in_window(cfg, None):
        return
    notify.send_report(cfg)
```

**(e) `start(cfg)`** — after the `detail` `add_job` call:
```python
    push_trig = CronTrigger(minute="0,15,30,45", timezone=tz)
    sched.add_job(push_job, push_trig, args=[cfg], max_instances=1, coalesce=True,
                  misfire_grace_time=60, id="push")
```

---

## 4. Full regression output

Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }
```

| Test file | Result | Exit |
|---|---|---|
| `tests/test_dashboard_app.py` | `dashboard_app OK` | 0 |
| `tests/test_dashboard_queries.py` | `dashboard_queries OK` | 0 |
| `tests/test_detail.py` | `detail OK` | 0 |
| `tests/test_guard.py` | `guard OK` | 0 |
| `tests/test_notify.py` | 20 OK lines → `ALL notify tests OK` | 0 |
| `tests/test_storage.py` | `storage OK` | 0 |
| `tests/test_ws.py` | `ws OK` | 0 |

**No regressions.** All 7 test files pass. The two log lines emitted by `test_notify.py` (`webhook errcode=93000: bad` and `截图失败: no browser`) are expected output from the `test_webhook_errcode` and `test_take_screenshot_failure` failure-path tests — they are `log.warning`/`log.error` calls inside the SUT, not test failures, and the file exits 0.

`test_notify.py` now runs **20 tests** (18 from Tasks 1–6 + the 2 added here).

---

## 5. Self-review

- **Scope discipline:** Only the 5 listed scheduler edits + the config block + the 2 tests were made. No other code touched. `notify.py` unchanged.
- **`check_alerts` isolation:** Called from `ws_job` inside `try/except Exception` with `log.exception`. An alert failure (e.g. webhook timeout, missing data) cannot break or delay WS collection — verified by reading the surrounding code; the `try` wraps only the alert call, after storage inserts are already complete.
- **`push_job` window gate:** Uses `_in_window(cfg, None)` → the global `window_start`/`window_end`. Verified by `test_push_job_window_gate`: empty window `(9,9]` → no `send_report` call; full-day window `(0,23:59]` → exactly one call.
- **`push` job cadence:** `CronTrigger(minute="0,15,30,45")` fires at :00/:15/:30/:45 every hour, 24/7, but `push_job` itself returns early outside the global window (09:00–21:00). `max_instances=1` + `coalesce=True` prevent overlap/queueing. Consistent with the WS/detail job config style.
- **No circular import:** `notify.check_alerts` imports `collector.scheduler._in_window` lazily (inside the function, line 208 of `notify.py`), and `scheduler.py` imports `notify` at module top. Since `check_alerts` is only called at runtime (not at import), this is safe — and the full test suite (which exercises `check_alerts`) passes, confirming no import cycle.
- **`push_minutes` config key:** Present in `config.yaml` but **not read by code** — the `CronTrigger` hardcodes `minute="0,15,30,45"`. This matches the brief verbatim (Step 4e specifies the literal string, not a config read). The key is informational/documentation of intent. Flagging as a minor concern below.
- **Recipients key type:** `"12378"` is a quoted YAML string key, matching `rcpt["12378"]` access in `notify.py`. Verified by `test_check_alerts_12378` passing.
- **`notify` does not import `dashboard`:** confirmed by reading `notify.py` imports (lines 1–6: `base64, csv, datetime, hashlib, logging, sqlite3, pathlib, zoneinfo, requests`). Constraint satisfied.

---

## 6. Concerns

1. **`push_minutes` is dead config.** `config.yaml` declares `push_minutes: [0, 15, 30, 45]` but `start()` hardcodes the same value into `CronTrigger` and never reads `cfg["notify"]["push_minutes"]`. If the cadence ever needs to change, both the config and the code must be updated, or the config will silently disagree. This is per the task brief (Step 4e gives the literal), so left as-is — but a future task could wire `push_trig = CronTrigger(minute=",".join(str(m) for m in cfg["notify"]["push_minutes"]), timezone=tz)` to make the config authoritative.
2. **`screenshot_url` points at `localhost:5001`.** The comment `# 改8080后换这里` notes the dashboard port may move to 8080; the current dashboard runs on 5001 (`python -m dashboard.app` → 127.0.0.1:5001). `take_screenshot` will fail silently (returns `None`, logged) if the dashboard is not running at push time — `send_report` still sends the two markdown messages, just without the screenshot. Acceptable graceful degradation.
3. **`push_job` runs in the scheduler thread.** `send_report` does synchronous HTTP (webhook POST, 30s timeout each) + Playwright screenshot (30s goto + 4s wait). If both are slow, the `push` job could take ~1–2 min, but `max_instances=1` + `coalesce=True` means the next :15 tick is skipped rather than queued — no backpressure buildup. Playwright launches headless Chromium, which must be installed in the deployment env (`playwright install chromium`).

---

## 7. Commit

**No commit made** — per task constraints (no git in this environment).

**Task 7 complete.** Feature wired, TDD red→green verified, full regression green.
