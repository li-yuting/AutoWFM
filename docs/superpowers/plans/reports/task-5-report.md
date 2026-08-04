# Task 5 Report — `check_alerts` 排队告警

## Status: DONE

## What was implemented

Appended `check_alerts(cfg, now=None) -> None` to `collector/notify.py`. The function reads the latest snapshot for 热线/在线/12378 (+ their seat dbs 热线明细/12378明细) and sends a text alert per source when 排队 crosses its threshold.

### Key design decisions (per brief)
- **Lazy import**: `from collector.scheduler import _in_window` lives INSIDE the function body. `collector.scheduler` imports `collector.notify` at top level, so a top-level reverse import would be circular. Lazy import breaks the cycle.
- **热线**: alert when `排队量 >= hotline_queue` AND `空闲 < 排队量` (threshold `>=`, idle strict `<`).
- **在线**: alert when `排队 >= online_queue` only — 在线 has no 空闲 metric, so no idle condition.
- **12378**: gated by its OWN `schedule` window via `_in_window(cfg, sub12378, now)` BEFORE reading data. This prevents stale false alerts on weekend evenings (12378 weekend window is `(09:00, 18:00]`, so 18:30 Saturday is out-of-window → no alert even if a stale row shows 排队=5). When in-window, same `排队量 >= queue_12378 AND 空闲 < 排队量` rule as 热线.
- **热线/在线 do NOT repeat the window gate** inside `check_alerts` — the caller `ws_job` already guarantees they are in-window.
- **Webhook keys**: 热线/在线 → `webhook.main_key` (MAIN); 12378 → `webhook.secondary_key` (SECOND). Recipients from `alert.recipients.{hotline|online|12378}`.
- **No top-level imports added** to notify.py (datetime/ZoneInfo already present from Tasks 1-4).

## TDD evidence

### RED (before implementation)
Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_check_alerts_hotline; test_check_alerts_hotline()"
```
Output:
```
AttributeError: module 'collector.notify' has no attribute 'check_alerts'
```
Exit code 1 — exactly the expected failure (function not yet defined).

### GREEN (after implementation)
Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_check_alerts_hotline, test_check_alerts_online, test_check_alerts_12378, test_check_alerts_12378_window; [f() for f in [test_check_alerts_hotline, test_check_alerts_online, test_check_alerts_12378, test_check_alerts_12378_window]]"
```
Output:
```
check_alerts_hotline OK
check_alerts_online OK
check_alerts_12378 OK
check_alerts_12378_window OK
```
All 4 new tests pass.

### Checkpoint (full suite)
Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Output:
```
webhook errcode=93000: bad
截图失败: no browser
latest_snapshot OK
forecast_at OK
render_firstline OK
render_secondline OK
take_screenshot_failure OK
check_alerts_hotline OK
check_alerts_online OK
check_alerts_12378 OK
check_alerts_12378_window OK
ALL notify tests OK
```
16 tests total (12 from Tasks 1-4 + 4 new), all pass.

## Files changed
1. **`collector/notify.py`** — appended `check_alerts(cfg, now=None)` (44 lines) after `take_screenshot`. No other lines touched.
2. **`tests/test_notify.py`** — added 2 imports (`import datetime`, `from zoneinfo import ZoneInfo`) to the import区; appended `_capture_alerts` helper + 4 tests after `test_take_screenshot_failure`; added 4 test calls to `main()`. No Tasks 1-4 content modified.

## Self-review
- Signature `check_alerts(cfg, now=None) -> None` matches the brief exactly.
- Threshold boundaries: `>=` for queue (equal triggers), strict `<` for `空闲 < 排队`. Verified by `test_check_alerts_hotline` (排队=10/阈值10 → alert; 排队=9 → no alert; 排队=10/空闲=10 → no alert).
- 在线 has no idle condition. Verified by `test_check_alerts_online` (排队=20 → alert; 排队=19 → no alert).
- 12378 window gate uses `_in_window` with 12378's own `schedule`. Verified by `test_check_alerts_12378_window` (Saturday 18:30, out of weekend `(09:00,18:00]` → no alert despite 排队=5).
- Webhook key routing: 热线/在线 → MAIN, 12378 → SECOND. Verified by `c[0] == "MAIN"` / `c[0] == "SECOND"` assertions.
- `_capture_alerts` patches `notify._send_text` at module level; `check_alerts` resolves `_send_text` via module globals, so the patch is effective.
- No circular import: lazy import confirmed working (all tests pass, no ImportError).
- `notify.py` does NOT import `dashboard`; does NOT import `collector.scheduler` at top level.
- No git operations performed.

## Concerns
- None blocking. The lazy import pattern is the documented solution for the scheduler↔notify cycle and works correctly.
- `check_alerts` is not yet wired into `ws_job`/`detail_job` (scheduler.py still has the `# notify.send_alert(...)` comment). That is out of scope for Task 5 — wiring is a later task.
- `log.info(_send_text(...))` logs the webhook return string; on failure `_send_text` returns a `"webhook 失败: ..."` string which `log.info` will record. This matches the pattern used elsewhere in notify.py and the brief's exact code.
