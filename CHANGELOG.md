# Changelog

## Unified Portfolio Web App (v1): record trades and dividends, don't just view them

Adds a write path on top of the previously read-only dashboard, so new
activity no longer needs re-typing into a separate Excel workbook between
official broker statements.

- **Login gate**: a single shared password (salted SHA-256, `auth.py`)
  guards every page, including the Dashboard -- Streamlit binds to
  `0.0.0.0` by default, so this was a real, current exposure, not a
  hypothetical.
- **Persistent storage**: `data/portfolio.db` (SQLite, `db.py`), seeded once
  from the existing audited xlsx (`scripts/seed_from_xlsx.py`) so FIFO
  lot-matching and dividend continuity work from day one.
- **Multi-page shell**: `dashboard_app.py` is now a thin `st.navigation`
  router; the dashboard body moved to `app_pages/dashboard.py`.
- **Dashboard blending**: KPIs blend the audited xlsx (up to the last
  official statement) with a live FIFO recompute of anything logged since --
  see `docs/METHODOLOGY.md` for the full FIFO-vs-average-cost writeup. A new
  "Since Last Statement" panel shows what's been logged and the Realized
  P/L it produced, per row.
- **Record Trade page**: Upload Slip (Claude vision API parses a Dime!
  trade confirmation screenshot into an editable confirm form) and Manual
  Entry, both writing to the same trade record. Shows the current FIFO
  position and a live estimated Realized P/L as a sell is typed, before
  it's saved. Blocks saving a sell that exceeds the current position behind
  an explicit confirmation checkbox. Symbol is a searchable dropdown sourced
  from trade history, with a free-text fallback for a genuinely new symbol.
  Delete (with a confirmation popover) for mistakes -- editing is delete +
  re-enter, not in-place.
- **Record Dividend page**: a bulk entry grid (several rows in one sitting)
  taking Gross Amount + Withholding Tax as the two separate numbers Dime!
  itself shows, computing Net automatically -- deliberately not an assumed
  flat 15%, since Capital Distribution rows have no withholding line in the
  real data. A Recent list (with the same delete-with-confirm pattern) and a
  Matrix view (symbols x months, full history, with row/column totals).
- **`requirements.txt`** added, pinning all direct dependencies including
  the new `anthropic` SDK.

## Dashboard KPI clarity pass

- **Total Fees tooltip**: clarifies that the figure is the Fees sheet *plus*
  Transactions' Commission column, since the Fees tab table only shows the
  former (a real point of confusion when cross-checking against the raw
  tables).
- **Split "Dividends + Interest" into two KPIs**: Dividends (net of the 15%
  Thai withholding tax) and Interest, each with their own tooltip. Interest
  is genuinely $0 for most months in this account's history, so combining it
  with Dividends obscured which one was actually driving the number.
- **Added "Avg. Monthly Dividend"**: Dividends ÷ number of months in the
  selected period, so it stays meaningful across any duration filter.
- **Reorganized the KPI grid into three tiers**, mirroring the source
  statements' own sectioning (Cash/Account Summary vs. Realized Gain/Loss vs.
  Income Summary):
  1. Headline outcomes: Portfolio Value, Net Deposits, Investment Gain/Loss, ROI
  2. Income: Dividends, Avg. Monthly Dividend, Interest
  3. Capital gains/losses & costs: Realized P/L, Unrealized P/L, Total Fees
- **THB reference values**: a sidebar "USD → THB rate" input (adjustable,
  default 33.0) drives a small, unemphasized gray "≈ ฿..." line under every
  dollar KPI. It's a single flat rate applied uniformly -- not historically
  accurate for older months, just a present-day rough reference.

## Prior session: full-history data quality audit

See commit `41002b8` for the underlying data fixes this dashboard reports on:
- Reclassified $22,448.92 in misfiled "Journal Entry(Cash)" rows (Income →
  Deposits & Withdrawals), fixing Net Deposits (tracked).
- Fixed `compute_realized_pl`'s same-day transaction ordering, which had
  overstated MSTY's realized loss by $131.45 (a 2025-12-08 reverse split vs.
  same-day buy conflict).
- Hardened `compute_realized_pl` against unclassified Entry Types (e.g. a
  rights-offering distribution), currently a $0-impact fix.
- Re-verified all 42 months (Jan 2023 - Jun 2026) against source PDFs and
  against the workbook's own Validation sheet: fully clean.
