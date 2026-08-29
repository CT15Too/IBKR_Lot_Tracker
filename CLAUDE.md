# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Starting the project

The backend serves both the API and the frontend from a single process. You do **not** need a separate frontend server.

```bash
# Install dependencies (first time only)
pip3 install -r requirements.txt

# Start the app (backend + frontend)
python3 run.py
# → open http://127.0.0.1:8000

# Desktop app (native pywebview window, same backend + frontend)
python3 run_desktop.py
```

If port 8000 is already in use, kill it first:
```bash
lsof -ti:8000 | xargs kill -9
```

Do **not** use `python3 -m http.server` to serve the frontend — API calls will fail (POST not supported by that server). Always use `http://127.0.0.1:8000`.

The three launch modes share the same backend and frontend: browser/source (`run.py`, reads `.env`), desktop/source (`run_desktop.py`), and desktop/installed (PyInstaller-bundled, reads settings from the OS data directory and the Flex token from the OS keyring). See `README.md` and `docs/desktop-release.md` for the packaging/release mechanics.

## Other commands

```bash
# Run all tests (install dev deps first)
pip3 install -r requirements-dev.txt
python3 -m pytest

# Run a single test file
python3 -m pytest tests/test_flex_parser.py

# Run a single test
python3 -m pytest tests/test_api.py::test_full_refresh_and_read_flow
```

Note: tests use FastAPI's `TestClient`, which requires `httpx` — pin it in `requirements-dev.txt` (already present).

## Configuration

Copy `.env.example` to `.env` and fill in:
- `IBKR_FLEX_TOKEN` — from IBKR Account Management → Flex Queries → Flex Web Service Configuration
- `IBKR_FLEX_QUERY_ID` — from the Activity Flex Query list after creating a query with Open Positions (lot detail) + Cash Report sections
- `FLEX_MIN_REFRESH_MINUTES` — cooldown between IBKR calls (default: 15)
- `DATABASE_PATH` — SQLite file path (default: `./data/lots.db`)

The app returns 400 on IBKR endpoints until credentials are set.

## Architecture

**Purpose:** Visualize individual IBKR position lots with cost basis, current price, unrealized P&L in both native currency and USD. IBKR natively shows only blended averages; this app parses raw Flex XML to show per-lot breakdown.

**Key design split:** Lot data (slow, 15–30 min IBKR refresh) and price data (fast, ~60s Yahoo Finance cache) are fetched independently.

### Data flow

```
POST /api/refresh-lots
  → ibkr_flex.py: two-step async poll (SendRequest → GetStatement)
  → db.py: upsert raw XML into SQLite (single-row flex_snapshot table)

GET /api/lots
  → db.py: load raw XML snapshot
  → flex_parser.py: parse XML → list[Lot] + list[CashBalance]
  → prices.py: yfinance lookup with 60s in-memory cache + manual price overrides
  → prices.py: get_fx_rate() for non-USD currencies (e.g. SGDUSD=X)
  → lots.py: group by symbol, compute P&L in native + USD, sort lots cheapest-first
  → JSON response

GET /api/cash
  → flex_parser.py: parse CashReportCurrency rows from stored XML
  → prices.py: get_fx_rate() for conversion
  → JSON response

POST /api/prices/override
  → db.py: save manual price to price_overrides table (persisted in SQLite)
  → used as fallback when yfinance returns None (e.g. non-US tickers like CSPX)
```

### Backend modules (`backend/`)

| Module | Role |
|---|---|
| `main.py` | FastAPI app, endpoints, enforces refresh cooldown (429) |
| `config.py` | Settings loaded from `.env` via `python-dotenv` |
| `ibkr_flex.py` | Async HTTP client for IBKR Flex Web Service; polls every 5s up to 60s; raises `FlexServiceError` |
| `flex_parser.py` | XML → `Lot` + `CashBalance` dataclasses; uses `FIELD_CANDIDATES` dict to handle IBKR's varying XML attribute names |
| `prices.py` | yfinance wrapper with 60s TTL in-memory cache; `get_fx_rate()` for currency conversion; manual override fallback |
| `lots.py` | Groups lots by symbol, computes unrealized P&L in native + USD, identifies `sellable_quantity`, sorts cheapest-first |
| `db.py` | SQLite with one-row upsert for flex snapshot + `price_overrides` table for manual prices |
| `runtime.py` | `LaunchMode` (browser/desktop/smoke) + `build_runtime()` — resolves per-platform writable data dir and server bind |
| `settings_store.py` | `SettingsStore` — atomic settings writes to the OS data dir (Flex query ID, preferences) |
| `credentials.py` | `CredentialStore` — Flex token in the OS keyring, never written to disk/logs |
| `desktop.py` | `run_desktop()` / `run_smoke()` — pywebview window, single-instance lock, loopback-only server, `SMOKE_OK` smoke mode |
| `version.py` | `APP_VERSION`, `UPDATER_VERSION`, `GITHUB_REPOSITORY` |
| `update_key.py` | `UPDATE_PUBLIC_KEY_B64` — embedded Ed25519 public key that verifies the signed update manifest |
| `updater/` | Desktop update state machine: `http.py` (GitHub release client), `manifest.py` (signature verify + platform select), `download.py` (staged download), `install.py` (apply/relaunch per platform), `service.py` (orchestration) |

### Frontend (`frontend/index.html`)

Single-file vanilla JS/CSS app, no build step. Served as a static file by FastAPI at `/`. Makes `fetch()` calls to `/api/*`. Theme (light/dark) persists in `localStorage`. Auto-polls prices every 60s and IBKR every 15 min (silently, 429 swallowed).

### Tests (`tests/`)

- `test_flex_parser.py` — unit tests for XML parsing
- `test_lots.py` — P&L math and sort order
- `test_api.py` — FastAPI integration tests with mocked IBKR and prices (uses `fastapi.testclient.TestClient`)
- `test_desktop.py` — desktop lifecycle, single-instance, loopback enforcement
- `test_runtime.py` / `test_settings_store.py` — runtime paths and atomic settings persistence
- `test_settings_update_api.py` / `test_update_*.py` — settings+update endpoints and the updater state machine
- `test_frontend_contract.py` / `test_packaging_contract.py` / `test_release_scripts.py` / `test_workflow_contract.py` / `test_docs_contract.py` — contract tests asserting frontend update UI, the PyInstaller spec, release scripts, CI workflow, and release docs stay in sync
- `fixtures/sample_flex.xml` — 4-lot sample (2 AAPL, 2 TSLA)

### Important implementation details

- `flex_parser.py` tries multiple XML attribute name candidates per field (e.g., `costBasisPrice`, `openPrice`, `costPrice`, `avgCost`) because IBKR varies attribute names across report types. Add new candidates to `FIELD_CANDIDATES` if a field fails to parse. Use `/api/debug/raw-flex` to inspect the raw XML.
- The SQLite database stores only the most recent snapshot (id=1 always). There is no history.
- Manual price overrides (for tickers yfinance can't find) are stored in the `price_overrides` SQLite table and persist across restarts.
- P&L for non-USD tickers is computed in native currency and converted to USD via `get_fx_rate()` (yfinance `SGDUSD=X` etc.).
- `apscheduler` is in `requirements.txt` but not currently used.
