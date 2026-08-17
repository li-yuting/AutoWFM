# Repository Guidelines

## Project Structure & Module Organization

- `collector/` — data collection: WebSocket scraping (`ws.py`), CRM detail export (`detail.py`: 会话记录/工单明细), forecasting, backfill, notifications, scheduler. All writes go through the Repository abstraction in `repository.py` (SQLite today; `config.yaml storage.backend` selects the backend).
- `dashboard/` — read-only Flask viewer (:8080) over `data/*.db` and `data/预估流入量.csv`; data via `api_client.py` (FastAPI).
- `api/` — FastAPI read layer (:8081); dashboard falls back to `dashboard/queries.py` when the API is down.
- `shift/` — scheduling subproject (Flask app `shift/app.py`), supervised by `manager.py`.
- `writeforecast/` — standalone weekly-forecast-to-CSV converter.
- `manager.py` — optional Tkinter supervisor for the collector, API, dashboard, and shift processes.
- `tests/` — plain-`assert` test scripts (`test_*.py`) plus a live `smoke.py`.
- Root config: `config.yaml`, `.env` (secrets, git-ignored), `holidays.txt`.

## Build, Test, and Development Commands

Always use the `.venv` interpreter and set UTF-8 output. There is no build step.

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m collector.main       # 采集器 (per-source windows)
.\.venv\Scripts\python.exe -m peakflow.main --fetch # 一次性全量预测 (Excel + HTML)
.\.venv\Scripts\python.exe -m dashboard.app        # 看板 http://127.0.0.1:8080
.\.venv\Scripts\python.exe -m api.app              # API 服务 http://127.0.0.1:8081
.\.venv\Scripts\python.exe manager.py              # 桌面管理器 (not -m)
.\.venv\Scripts\python.exe tests\test_storage.py   # 单个测试
```

Run all tests (no pytest): `Get-ChildItem tests\test_*.py | ForEach-Object { .\.venv\Scripts\python.exe $_.FullName }`.

Use `-m` for `collector`/`dashboard`/`api` so the project root stays on `sys.path`.

## Coding Style & Naming Conventions

- Python, 4-space indentation, PEP 8. No linter or formatter is configured.
- DB column names are Chinese; extractor keys must match `SCHEMAS` (single source of truth: `collector/repository.py`, re-exported by `collector/storage.py`) exactly, or inserts fail with `KeyError`. When adding a metric, update both the extractor and the schema entry.
- Collector writes and dashboard reads are separate processes; `collector/notify.py` must not import `dashboard` or `collector.scheduler` at top level.

## Testing Guidelines

- Plain `assert`, no pytest; run each file directly with the `.venv` interpreter.
- Name files `tests/test_<module>.py` and functions `test_*`.
- `tests/smoke.py` hits live WS/requests endpoints and is **not** part of CI.
- CI (`.github/workflows/ci.yml`) runs `tests/test_*.py` serially on Ubuntu + Python 3.14.

## Commit & Pull Request Guidelines

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, with an optional scope (e.g. `feat(detail): …`). Chinese descriptions are common; keep each commit focused on a single change.
- PRs: describe the change and rationale, link related issues, note any config/template updates, and include screenshots for UI changes.

## Security & Configuration

- Never commit secrets into `config.yaml` or source. Put tokens/keys in `.env` (git-ignored), loaded via `load_dotenv()`; mirror placeholders in `.env.example` and `config.example.yaml`.
- Env vars consumed: `AUTOWFM_TOKEN` / `AUTOWFM_TENEMENT_ID` (CRM export), `AUTOWFM_WEBHOOK_MAIN` / `AUTOWFM_WEBHOOK_SECONDARY` (企微 webhook), `AUTOWFM_DASH_TOKEN` (dashboard/API Bearer), optional `AUTOWFM_API_URL` / `AUTOWFM_DATA_DIR`.
- The dashboard requires `Authorization: Bearer <AUTOWFM_DASH_TOKEN>`; leave the token empty for local development.