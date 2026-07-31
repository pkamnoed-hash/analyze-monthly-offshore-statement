# Unified Portfolio Web App

A personal offshore brokerage portfolio tracker. Blends an audited official
statement (xlsx) with live-logged trades and dividends, in one password-gated
Streamlit app -- so activity no longer needs re-typing into a separate Excel
workbook between official broker statements.

## Quick start

1. Python 3.12 virtualenv at `.venv_dashboard/`, dependencies from
   `requirements.txt`.
2. Create `.streamlit/secrets.toml` with `APP_PASSWORD_SALT`,
   `APP_PASSWORD_HASH`, and `ANTHROPIC_API_KEY` (see `docs/DEPLOYMENT.md`
   for the exact format).
3. Run `run_dashboard.bat`, open `http://localhost:8502`.

Full details: `docs/DEPLOYMENT.md`.

## What it does

- **Dashboard** -- blended KPIs (Portfolio Value, ROI, Realized/Unrealized
  P/L, Dividends, Interest, Fees): audited xlsx figures up to the last
  official statement, live FIFO recompute for anything logged since.
- **Record Trade** -- log a buy/sell manually, or upload a broker slip
  screenshot and let Claude's vision API pre-fill the form.
- **Record Dividend** -- log a dividend/interest/capital distribution as
  Gross Amount + Withholding Tax (matching how the broker's own app shows
  it); Net is computed automatically.
- **Reconciliation** (Tools) -- once a new official statement arrives,
  verifies everything logged live against it and flags anything that
  doesn't match.

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Tech stack, system architecture diagram, data flow/pipeline diagram |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | SQLite schema, xlsx-to-SQLite column mapping, ER diagram |
| [`docs/USER_FLOW.md`](docs/USER_FLOW.md) | End-to-end user journey diagram |
| [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) | How every KPI/calculation actually works, and why |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Build history and design record (V1, V2, deferred/future) |
| [`docs/VERSION_CONTROL.md`](docs/VERSION_CONTROL.md) | Branch strategy, merge/commit conventions |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | How to run this app (local only, today) |
| [`CHANGELOG.md`](CHANGELOG.md) | User-facing summary of what shipped, per version |
| [`CLAUDE.md`](CLAUDE.md) | Orientation notes for a Claude Code session working in this repo |
