# Deployment

## Current reality: local dev + Streamlit Community Cloud (v3)

This app runs two ways today: a **local instance** on the developer's own
machine, and a **deployed instance** on Streamlit Community Cloud. Both
talk to the same **Turso** database (see `docs/ARCHITECTURE.md`'s
"Hosting" section for why) -- there's no local/deployed data divergence,
no CI/CD pipeline, and no separate Dev/UAT/Production split. This document
describes what actually exists, not an aspirational setup.

## Prerequisites

- Python 3.12, with a dedicated virtualenv at `.venv_dashboard/` (already
  created; not committed to git).
- Dependencies from `requirements.txt` (`pip install -r requirements.txt`
  inside that venv) -- includes `libsql`, the Turso client.
- `.streamlit/secrets.toml` (gitignored, must be created manually on any
  new machine) with five keys:
  ```toml
  APP_PASSWORD_SALT = "..."
  APP_PASSWORD_HASH = "..."      # see core/auth.py -- sha256(salt + password)
  ANTHROPIC_API_KEY = "..."      # for slip parsing (core/slip_parser.py)
  TURSO_DATABASE_URL = "libsql://..."
  TURSO_AUTH_TOKEN = "..."
  ```
  The last two come from the Turso dashboard (turso.tech) -- see "Cloud
  deployment" below for where they're generated.
- `data/portfolio.db` and `data/Offshore_Statements_*.xlsx` present
  locally, but `portfolio.db` is now a **frozen pre-migration snapshot**,
  not what the app actually reads -- `core/db.py`'s `get_connection()`
  always targets Turso, local and deployed alike. The xlsx is unaffected
  (still read directly from disk). `scripts/seed_from_xlsx.py` was the
  one-time import that originally populated the database, before the
  Turso migration; not relevant to a fresh machine setup anymore.

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

## Cloud deployment (Streamlit Community Cloud + Turso)

**Chosen over Hugging Face Spaces (Docker SDK went paid-only mid-build),
Render, and Google Cloud Run** -- see `docs/ARCHITECTURE.md`'s "Hosting"
section for the comparison and rationale. Both pieces are free.

### One-time Turso setup

1. Sign up at turso.tech (free plan: 5GB storage, 10M writes/month).
2. Create a group (name it `default` -- Turso's own tooling assumes this
   name when none is given, and the free plan only allows one group
   anyway) in whichever region is closest to you.
3. Create a database under that group. If migrating existing data, use
   the "Upload SQLite File" option directly -- **the source file must
   already be in `journal_mode=WAL`**, or the upload fails with a
   "Protocol error" (fix locally first: `PRAGMA journal_mode=WAL;` via a
   throwaway `sqlite3` connection to the file, then re-upload).
4. From the database's Overview page, copy the **Database URL**
   (`libsql://...`) and generate an **auth token** (Read & Write,
   Never expires -- this token lives in host secrets long-term with no
   human present to rotate it; if it's ever compromised, invalidate it
   from the same page rather than relying on an expiry).

### One-time Streamlit Community Cloud setup

1. share.streamlit.io -> sign in with GitHub.
2. **Private repos need an explicit GitHub App grant, separate from
   signing in** -- the plain "Continue with GitHub" flow only grants an
   OAuth identity authorization (visible under
   `github.com/settings/connections/applications`, not
   `github.com/settings/installations`), which isn't enough to see a
   private repo. If deploying from a private repo silently shows "This
   repository does not exist," use Streamlit's interactive repository
   picker and select the repo directly (not paste-URL) -- that's what
   actually triggers GitHub's "Grant access to this repository" prompt.
3. "New app" (or "Deploy an app") -> pick this repo, the branch being
   deployed, and `dashboard_app.py` as the main file.
4. Advanced settings -> Python version **3.12** (matches the local venv).
5. Advanced settings -> Secrets -> paste the entire local
   `.streamlit/secrets.toml` file's contents in as-is (all five keys,
   same TOML format).
6. Deploy. First build installs `libsql` from source-adjacent wheels and
   can take longer than a typical redeploy.

### Verifying it actually worked

Loading is not enough proof -- confirm two separate things:
1. **Reads work**: log in, visit a data-heavy page (e.g. Rebalance &
   Reallocate) and confirm it shows the same figures as local.
2. **Writes persist across a restart**: make one small edit on the live
   app, force a restart (push a trivial commit, or wait for the app to
   sleep from inactivity and reload), and confirm the edit is still
   there. This is the actual point of the Turso migration -- Streamlit
   Community Cloud's own disk is ephemeral, so this step is what proves
   data survives independently of the app's own lifecycle.

### Auth model

Still a single shared password (`core/auth.py`), same as local -- not
revisited as part of this migration. Worth reconsidering if this ever
stops being a single-user app.
