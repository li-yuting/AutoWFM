# Task 1 Report — notify.py skeleton + data-reading helpers

## Status: DONE

## What was implemented

Rewrote `collector/notify.py` (was a 13-line stub with `send_alert`/`take_screenshot` logging stubs) into the real module skeleton with data-reading helpers, and created `tests/test_notify.py`.

### Interfaces produced (all 5)
- `latest_snapshot(data_dir, source, date_str) -> dict|None` — returns the latest row (by `时间`) for the given source/date, or `None` if the db/table/row is absent.
- `forecast_at(data_dir, line, now_str) -> int` — looks up `累计预估量` from `预估流入量.csv` for an exact `线路`+`时间` match; returns `0` on miss/missing file/parse error.
- `_find_sub(cfg, name) -> dict|None` — linear scan of `cfg["subs"]` by `name`.
- `_n(v) -> int` — `v or 0` (None/0/falsy -> 0).
- `_pct(num, den) -> str` — `f"{num/den*100:.2f}%"` when `den` is truthy, else `"0.00%"`.

### Files changed
- `collector/notify.py` — full rewrite (stub replaced).
- `tests/test_notify.py` — new file (test style matches project: sys.path bootstrap + plain `assert` + `main()` + `print("... OK")`).

## TDD evidence

### RED (test written first, run against the stub)
Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_latest_snapshot, test_forecast_at; test_latest_snapshot(); test_forecast_at()"
```
Output (exit 1):
```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import sys; sys.path.insert(0,'.'); from tests.test_notify import test_latest_snapshot, test_forecast_at; test_latest_snapshot(); test_forecast_at()
                                                                                                              ~~~~~~~~~~~~~~~~~~~~^^
  File "D:\PythonProject\AutoWFM\tests\test_notify.py", line 29, in test_latest_snapshot
    row = notify.latest_snapshot(d, "热线", "2026-07-28")
          ^^^^^^^^^^^^^^^^^^^^^^
AttributeError: module 'collector.notify' has no attribute 'latest_snapshot'
```
Expected failure confirmed.

### GREEN (after rewriting notify.py)
Command (same as RED):
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from tests.test_notify import test_latest_snapshot, test_forecast_at; test_latest_snapshot(); test_forecast_at()"
```
Output:
```
latest_snapshot OK
forecast_at OK
```

### Checkpoint (full file)
Command:
```powershell
$env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe tests\test_notify.py
```
Output:
```
latest_snapshot OK
forecast_at OK
ALL notify tests OK
```

### Regression (import-chain integrity)
`test_guard.py` imports `scheduler`, which top-level does `from collector import notify` — the brief flagged this as a circular-import risk. Both pass:
```
guard OK
storage OK
```

## Constraints satisfied
- Python 3.14 in `.venv` used throughout (`.\.venv\Scripts\python.exe`); `PYTHONIOENCODING=utf-8` set for Chinese output.
- `notify.py` does NOT import `dashboard` (layering: collector writes / dashboard reads). Verified via grep — top-level imports are only stdlib (`base64, csv, datetime, hashlib, logging, sqlite3`, `pathlib.Path`, `zoneinfo.ZoneInfo`) + `requests`.
- `notify.py` does NOT import `collector.scheduler` at module top level (circular-import guard for later `check_alerts`, which will lazily import `_in_window` inside a function body).
- No git operations performed (no git installed).
- Test follows project style: plain `assert`, no pytest, `main()` + `print("... OK")`, run via `python tests/test_notify.py`.
- Implementation is verbatim from the brief; nothing added beyond Task 1 scope.

## Self-review findings
- Unused imports `base64, datetime, hashlib, ZoneInfo, requests` are present but unused in Task 1. This is **intentional** per the brief: later tasks will APPEND renderers/webhook senders/screenshot/check_alerts/send_report to this same file, so these imports are pre-staged. Not a concern.
- `_pct` docstring says "den<=0 返回 '0.00%'", but the implementation (`if den`) only treats falsy `den` (0/None) as the zero case; a *negative* `den` would compute a negative percentage rather than returning "0.00%". This is the verbatim code from the brief; in practice denominators here are counts (转人工量/预测量) that are never negative, so it is moot. Noted for transparency, not "fixed" (out of Task 1 scope / implement-exactly-as-given).
- The three helpers `_n`/`_pct`/`_find_sub` are not exercised by the provided test file (only `latest_snapshot` and `forecast_at` are). Smoke-checked them via a one-off `-c` invocation: all behave per their docstrings (`_n(None/0/5)` -> `0/0/5`; `_pct(1,3)` -> `33.33%`, `_pct(*,0)` -> `0.00%`; `_find_sub` finds/misses correctly). No file changes made for this check.

## Concerns
None blocking. The `_pct` negative-denominator docstring discrepancy is cosmetic and out of scope.
