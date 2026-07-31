# Architecture

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| UI framework | [Streamlit](https://streamlit.io) 1.58 | Multi-page app via `st.navigation`/`st.Page` |
| Language | Python 3.12 | Single `.venv_dashboard` virtualenv |
| Data wrangling | pandas 3.0 | Every `core/` function is DataFrame-in/DataFrame-out |
| Persistence | Turso (`libsql` client) | Hosted, SQLite-compatible; `get_connection()` always targets Turso now, local and deployed alike -- `data/portfolio.db` is a frozen pre-migration snapshot, no longer read by the running app (see "Hosting" below) |
| Official source file | openpyxl 3.1 (via `pandas.read_excel`) | Reads the audited `Offshore_Statements_*.xlsx` |
| Charts | Plotly 6.8 | Dashboard only |
| Slip parsing | Anthropic SDK 0.120 (`claude-opus-5`, vision) | Structured-output JSON schema, see `core/slip_parser.py` |
| PDF extraction | pdfplumber 0.11 | Used by `scripts/extract_statement.py` (the pre-existing, unchanged monthly-statement pipeline) |
| Testing | pytest 9.1 | 140 tests as of V2; every `core/` module is pure logic, no Streamlit import, so it's testable without a running app |

Full pinned versions: `requirements.txt`.

## System architecture

```mermaid
flowchart TD
    Browser["Browser<br/>(localhost:8502)"] <--> App["dashboard_app.py<br/>st.navigation router + login gate"]

    App --> Pages["app_pages/<br/>dashboard.py / record_trade.py /<br/>record_dividend.py / reconciliation.py"]

    Pages --> Core["core/<br/>auth, calculations, db,<br/>slip_parser, reconciliation"]

    Core --> DB[("Turso (libSQL)<br/>hosted, SQLite-compatible")]
    Core --> XLSX[("Offshore_Statements_*.xlsx<br/>audited official statement")]
    Core --> Claude["Claude Vision API<br/>(slip_parser.py only)"]

    Secrets[".streamlit/secrets.toml<br/>(gitignored, local) /<br/>host secrets (deployed)"] -.-> App
    App -.->|"os.environ bridge"| Core
```

`core/` holds every function that touches data or does a calculation --
none of it imports Streamlit, so it's unit-testable in isolation
(`tests/`). `app_pages/` is Streamlit-only glue: layout, widgets, session
state. `dashboard_app.py` is the thin entry point (login gate +
`st.navigation` page routing) and stays at the project root, everything
else moved into `core/` during V1's post-Step-7 restructure.

## Hosting (v3)

Deployed on **Streamlit Community Cloud** (free, deploys straight from
this GitHub repo), backed by **Turso** (free, SQLite-compatible hosted
database) for persistence -- chosen after Streamlit Community Cloud,
Render, and Google Cloud Run's free tiers all turned out to lack durable
storage (ephemeral disks that reset on redeploy), and Hugging Face Spaces'
free tier turned out to no longer include the Docker SDK Streamlit needs.
Compute and storage are deliberately decoupled: Streamlit Community Cloud
hosts the running app (which can restart, sleep, or redeploy freely
without losing data), while Turso is the single source of truth for
`trades`/`dividends`/`symbol_types`/`rebalance_plans`, shared identically
by the local dev instance and the deployed one via the same
`TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` secrets.

`core/db.py` deliberately has no Streamlit import (see `CLAUDE.md`), so it
can't read `st.secrets` directly -- `dashboard_app.py` bridges the two
Turso values from `st.secrets` into `os.environ` at startup, before any
`core.db` call happens, and `get_connection()` reads them from there.

See `docs/DEPLOYMENT.md` for the actual setup steps and
`docs/VERSION_CONTROL.md` for why this shipped as v3.

## Data flow / pipelines

Three distinct flows through this system:

```mermaid
flowchart TD
    subgraph Seed["1. One-time seed (already done, re-run only with --force)"]
        X1[("Offshore_Statements_*.xlsx")] --> S1["scripts/seed_from_xlsx.py"]
        S1 --> D1[("trades / dividends<br/>source='seed'")]
    end

    subgraph Entry["2. Ongoing live entry"]
        Slip["Slip screenshot"] --> Vision["core/slip_parser.py<br/>Claude Vision API"]
        Vision --> RT["Record Trade page<br/>(pre-filled, editable)"]
        Manual["Manual entry"] --> RT
        RT --> D2[("trades<br/>source='manual'/'slip'")]

        Receipt["Dividend receipt<br/>(Gross + Withholding, by eye)"] --> RD["Record Dividend page"]
        RD --> D3[("dividends<br/>source='manual'")]
    end

    subgraph Consume["3. Consumption"]
        D1 --> Dash["Dashboard<br/>(blended KPIs, cutoff-split)"]
        D2 --> Dash
        D3 --> Dash
        X1 --> Dash

        D1 --> Rec["Reconciliation page"]
        D2 --> Rec
        D3 --> Rec
        X1 --> Rec
        Rec -->|"mark_reconciled_bulk()"| D1
        Rec -->|"mark_reconciled_bulk()"| D2
        Rec -->|"mark_reconciled_bulk()"| D3
    end
```

The pre-existing monthly statement pipeline (`scripts/extract_statement.py`
-> `scripts/merge_into_workbook.py`, PDF -> updated xlsx) is **unchanged**
and out of scope for everything in this app -- it's what produces the
`Offshore_Statements_*.xlsx` file these flows read from. See
`docs/ROADMAP.md` for why `scripts/reconcile.py` and
`scripts/merge_into_workbook.py` specifically were confirmed broken/one-off
during V2 research and deliberately not touched.

See `docs/DATA_MODEL.md` for the schema these flows write to, and
`docs/METHODOLOGY.md` for how the Dashboard/Reconciliation calculations
themselves work.
