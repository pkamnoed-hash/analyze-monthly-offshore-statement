# Changelog

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
