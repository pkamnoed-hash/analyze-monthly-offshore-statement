# Deployment

## Current reality: local only

This app runs as a **single local instance on the developer's own
machine**. There is no cloud hosting, no CI/CD pipeline, and no separate
Dev/UAT/Production environment split today -- all of that would need to be
designed from scratch if it's ever pursued. This document describes what
actually exists, not an aspirational setup.

## Prerequisites

- Python 3.12, with a dedicated virtualenv at `.venv_dashboard/` (already
  created; not committed to git).
- Dependencies from `requirements.txt` (`pip install -r requirements.txt`
  inside that venv).
- `.streamlit/secrets.toml` (gitignored, must be created manually on any
  new machine) with three keys:
  ```toml
  APP_PASSWORD_SALT = "..."
  APP_PASSWORD_HASH = "..."   # see core/auth.py -- sha256(salt + password)
  ANTHROPIC_API_KEY = "..."   # for slip parsing (core/slip_parser.py)
  ```
- `data/portfolio.db` and `data/Offshore_Statements_*.xlsx` already present
  locally (not seeded fresh on every run -- `scripts/seed_from_xlsx.py` is
  a one-time import, safe to skip if `data/portfolio.db` already exists).

## Running it

```
run_dashboard.bat
```

Honors a `PORT` environment variable (defaults to `8502` if unset) --
useful if something else is already bound to 8502. Equivalent manual
command:

```
.venv_dashboard\Scripts\python.exe -m streamlit run dashboard_app.py --server.headless true --server.port 8502
```

Then open `http://localhost:8502` and log in with the password whose hash
is in `secrets.toml`.

## Restarting after a code change

Streamlit's file-watcher auto-reloads most edits, but a `core/` module
change sometimes needs a full restart to pick up cleanly. On Windows, kill
any stale process first (Streamlit can leave zombie processes bound to the
port across restarts):

```powershell
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
  Where-Object { $_.CommandLine -like '*streamlit run dashboard_app.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Then re-run `run_dashboard.bat`.

## If cloud deployment is ever pursued

Not designed yet -- revisit this document first. Things that would need
deciding: where `data/portfolio.db` and the xlsx actually live (this app
assumes local disk access, not object storage), how `secrets.toml` maps to
whatever secrets manager the host provides, and whether a single shared
password is still an acceptable auth model outside a fully local,
single-user context.
