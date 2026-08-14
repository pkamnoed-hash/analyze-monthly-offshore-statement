# Version control

Describes the branching/merge approach actually used on this repo so far --
not an aspirational process, a record of what's been done.

## Branches

| Branch | Purpose |
|---|---|
| `main` | Stable line. Only receives completed, tested versions via merge. |
| `V1-record-trade-and-view` | V1 feature branch (login, storage, Record Trade, Record Dividend, blended Dashboard). Merged into `main`, kept around afterward rather than deleted. |
| `v2-Reconciliation` | V2 feature branch (Reconciliation page). Branched from `main` *after* V1 was merged in, not from the V1 branch directly -- keeps each version's diff clean and reviewable on its own. |
| `v2.1-allocation-type` | V2.1 feature branch (Dividend/Growth/Others symbol classification, plus first-entry tagging in Record Trade). A minor version off `main` rather than a new whole version, since it's a smaller, additive feature building on top of V2 rather than a major build phase. |
| `v2.2-monitor-stocks` | V2.2 feature branch (Monitor Stocks page -- live `yfinance` market data for current holdings). Minor version off `main`, same rationale as v2.1. Merged into `main`. |
| `v2.3-system-backup` | V2.3 feature branch (System Backup page -- manual, on-demand backups of `data/portfolio.db` and the official Statement xlsx). Cut from `main` after v2.2 was merged in. Pulled forward ahead of Rebalance/Reallocate, which shifts to v2.4 (see "My version quick note" below) -- repurposed from an earlier `v2.3.1-rebalance-reallocate` branch that had no real code on it yet. Merged into `main`. |
| `v2.4-rebalance-reallocate` | V2.4 feature branch (Rebalance / Reallocate Investment -- decide where new cash goes across Dividend-classified holdings). Cut from `main` after v2.3 was merged in. Rebuilt from scratch, per the user's explicit choice -- none of the earlier discarded `v2.3-rebalance-reallocate`/`v2.3.1-rebalance-reallocate` design was reused; wireframe and flow were re-derived through fresh discussion. Merged into `main`. |
| `v3-hosting-prep` | V3 feature branch (hosting preparation -- deploy to the cloud, keep it free, make writes actually persist). Cut from `main` after v2.4 was merged in. Explored Hugging Face Spaces first (scored highest in an initial comparison) but reversed that decision mid-build once its Docker SDK turned out to require a paid PRO plan; landed on Streamlit Community Cloud (compute) + Turso (persistence) instead, decoupling the two since no single free host offered both. Live at `myinvestment27.streamlit.app`, verified working end-to-end including write persistence across a restart. Merged into `main`. |
| `v3.1-testing-environment` | V3.1 feature branch (understand and prepare a safe testing environment now that real data lives in Turso). Cut from `main` after V3 was merged in. Adds a sidebar dev/prod environment badge (`APP_ENV` secret), `docs/BACKUP_AND_TESTING.md` (backup/rollback/safe-testing/schema-change reference), and a 5-scenario hands-on practice lab, each scenario actually run once with a confirmed checkpoint. Root-caused an early local-dev connection failure to a freshly-created Turso branch's brief propagation delay, not a code bug. Merged into `main`. |
| `v4-dashboard-tools-integration` | V4 feature branch (first batch under "advance dashboard, tools v2, integration"). Cut from `main` after V3.1 was merged in. Regroups Dashboard's KPI cards and Monitor Stocks' Category Summary into labeled sections (worked out iteratively against a mockup + live screenshots), defaults the USD -> THB rate from a live yfinance quote, and fixes a real dtype bug that showed literal "None" instead of a blank Realized P/L cell for a buy-only trade. No `core/db.py`/schema changes. Merged into `main`. |
| `v4.1.1-fix-oversell-precision` | V4.1.1 hotfix branch, cut from `main` (not from v4.1 -- unrelated to that branch's theme). Fixes a floating-point false-positive on Record Trade's oversell check (selling an entire position built from many small buys could trip "this would sell more than you have"), adding the same `+1e-9` tolerance `estimate_sell_realized_pl()` already used. No database/schema changes. Merged into `main`. |
| `v4.1-revised-number-and-stats` | V4.1 feature branch (revised number and stats). Cut from `main` after V4 was merged in. Started as an open scoping discussion (dividend/ROI numbers, via a one-off `scripts/dividend_category_stats.py`), then built real Monitor Stocks features: per-symbol `Total P/L`/`Total P/L %` (actual Dividends Received + Unrealized), `Holding Period (Years)`/`Total P/L %/yr`, a category-level "Total Return" group in Category Summary, and the per-symbol table split into 5 tabs. No `core/db.py`/schema changes. Merged into `main`. |
| `v4.1.2-fix-holding-period-dtype` | V4.1.2 hotfix branch, cut from `main` (not from v4.1 -- unrelated feature work, same rationale as v4.1.1). Fixes a production-only `AttributeError` on Monitor Stocks (`.dt.days` called on a pandas Series that fell back to `dtype=object` instead of `datetime64` after a `.map()` with zero index overlap) by forcing `pd.to_datetime(..., errors="coerce")` before the `.dt` accessor. No database/schema changes. Merged into `main`, tagged `v4.1.2`. |
| `v4.2-monitor-stocks-ex-date` | V4.2 feature branch. Cut from `main` after v4.1.2 was merged in, originally as `v4.2-auto-trend-line` -- explored automatic trend line drawing via discussion and a live MSFT proof-of-concept (published as a standalone Artifact, not part of this repo), confirming the approach (Plotly + numpy linear regression, no external chart API) but never implementing it in the app. Redirected mid-branch to a different, real gap instead: adds an `Ex-Date` column to Monitor Stocks (Overview + Dividends tabs), highlighted when it falls in the current month. A companion "Payout Date" column was tried and removed after `yfinance`'s `info["dividendDate"]` was found blank for most funds and stale for at least one ETF (SHV). Renamed to match what shipped. No `core/db.py`/schema changes. Merged into `main`. |
| `v4.3-rebalance-columns` | V4.3 feature branch. Cut from `main` after v4.2 was merged in. Splits Rebalance & Reallocate's single wide table into 5 tabs (Overview/Weight/Dividend Impact/Performance/Analyze) -- Analyze ended up as the sole editable tab (Overview flipped to read-only once Analyze needed editing too, avoiding two simultaneous editable forms). Adds `Dividends Received`/`Total P/L`/`Total P/L %` (mirroring Monitor Stocks), a `Beta` column, a standalone THB->USD reference calculator, and a Current/New KPI redesign for the Summary section. Also adds a Monitor Stocks Monthly Dividend chart (validated against a real broker statement PDF) and fixes a real Streamlit `$`-pair-as-inline-math rendering bug across 4 pages. No `core/db.py`/schema changes. Merged into `main`. |
| `v4.3.1` (no branch -- direct to `main`) | Two small follow-ups to V4.3, applied directly to `main` per explicit user request rather than a feature branch: reorders Rebalance & Reallocate's tabs (Analyze first), and adds an `Ex-Date` column (Overview + Analyze) with the same current-month amber highlight as Monitor Stocks. Commits `c9f423d`/`fa1a24d`. No `core/db.py`/schema changes. |
| `v4.4-support-resistance-analysis` | V4.4 feature branch. Cut from `main` after v4.3.1 was applied. Adds Monitor Stocks' Trendline tab (Pivot Points S3-R3, all 52 holdings, anchored to Avg Cost) with an `Action` cell linking to a new per-symbol "Auto Trendline" page. That page's chart went through several overlay iterations (diagonal Trend Line, clustered S/R Zones, a Nearest R/S readout) before consolidating -- per direct user feedback after live-testing the readout ("too many competing line concepts") -- into a single **Reference Line** concept: swing highs/lows nearest to current price, captured at a deliberate moment (not recomputed on Timeline/Interval navigation) via a "Regenerate" button, with full drag/delete/create editing and DB persistence (`reference_lines` table). Fixed two real bugs found via live testing (a ~1.6s DB write on every drag, and a delete-button click silently swallowed by the chart's own drag-detection listener). 299/299 tests passing. Full design history in `docs/ROADMAP.md` (V4.4). Merged into `main` (`23cf008`, `--no-ff`), tagged `v4.4`. |
| `v4.4.1-reference-line-summary` | V4.4.1 feature branch. Cut from `main` after v4.4 was merged in. Builds the portfolio-wide "nearest Reference Line" summary table across all held symbols -- explicitly requested as a follow-up once the per-symbol Reference Line feature (v4.4) was working, with the key requirement that it compute on first load rather than requiring each symbol's own Auto Trendline page be visited first. A read-only chat preview (real Turso + yfinance data, `compute_reference_lines()` run for all ~52 holdings) was shown and confirmed working before this branch was cut. Numbered `4.4.1` (not the originally-discussed `4.5`) since it's simply the next thing built after v4.4, in chronological order -- matches how every other `x.y.z` number in this project was assigned, and avoids reserving a number in advance for something (biometric login) that ended up being built later. Adds Monitor Stocks' Reference Lines + Highlight tabs, renames Overview to Overall, and (a live-testing tweak round) moves the "passed" highlight onto the Nearest Resistance/Support cells themselves, reorders Highlight to the leftmost tab, trims Overall's now-redundant Pivot Points columns, and adds a matching "Passed R/S" column to Auto Trendline's own Zone 5 table -- which also surfaced and fixed a real pre-existing bug (visiting a symbol's page in a fresh session silently wiped its `passed_at`). 317/317 tests passing. Full design history in `docs/ROADMAP.md` (V4.4.1). Merged into `main` (`f45e857`, `--no-ff`), tagged `v4.4.1`. |

Naming convention: `vN-short-description` (or `vN.M-short-description` for a
smaller, additive feature that doesn't warrant a new whole version number),
capital V on whole versions, hyphen-separated.

## My version quick plan note
Version planning
- 1 - basic "record trade and dividend"
- 2 - intermediate "reconciliation", "tools v1"
	○ 2.1 Classification dividend and growth
	○ 2.2 Monitor connect market price | retrieve these data symbol description, 90 day trend, adding asset class, portfolio group, beta, weight %
  ○ 2.3 system backup page (backup db + official statement xlsx)
  ○ 2.4 rebalance and label version in the web application
- 3 - hosting preparation and improvement (deploy to Streamlit Community Cloud + Turso, make source code cloud-compatible -- Hugging Face Spaces was the original target, reversed once its Docker SDK went paid-only)
	○ 3.1 understand and prepare testing environment (dev/prod environment badge, Turso branching for safe testing, schema-change pattern, hands-on practice lab)
- 4 - advance "dashhboard, tools v2, intetration"
	○ 4.0 (`v4-dashboard-tools-integration`) Dashboard/Monitor Stocks KPI regrouping, live USD/THB default, Realized P/L dtype fix
	○ 4.1.1 (`v4.1.1-fix-oversell-precision`) hotfix: oversell false-positive floating-point tolerance
	○ 4.1 (`v4.1-revised-number-and-stats`) Monitor Stocks Total P/L/%, Holding Period, category-level Total Return, tabbed columns
	○ 4.1.2 (`v4.1.2-fix-holding-period-dtype`) hotfix: fix production-only dtype crash on Monitor Stocks
	○ 4.2 (`v4.2-monitor-stocks-ex-date`) Monitor Stocks Ex-Date column + current-month highlighting; automatic trend line explored (live demo, not yet built) -- deferred
	○ 4.3 (`v4.3-rebalance-columns`) Rebalance & Reallocate 5-tab split (Analyze is the sole editable tab), Total P/L, Beta, THB calculator, Summary KPI redesign; Monitor Stocks Monthly Dividend chart; $-pair rendering bug fixed across 4 pages
	○ 4.3.1 (direct to `main`, no branch) Rebalance & Reallocate tab reorder (Analyze first) + Ex-Date column with current-month highlighting
  ○ 4.4 (`v4.4-support-resistance-analysis`) Monitor Stocks Trendline tab (Pivot Points, all 52 holdings) + new "Auto Trendline" per-symbol page, consolidated down to a single "Reference Line" concept (swing-based, nearest-to-price, captured-at-a-moment, drag/delete/create, DB-persisted) after iterating through Trend Line/S-R Zones/Nearest-R-S overlays first. Merged into `main`, tagged `v4.4` -- see `docs/ROADMAP.md` for the full arc.
  ○ 4.4.1 (`v4.4.1-reference-line-summary`) portfolio-wide "nearest Reference Line" summary table across all held symbols, computed on first load rather than requiring each symbol's own page be visited first -- see ROADMAP.md's V4.4 "Considered and explicitly deferred" note; a live preview of this table (read-only, real data, not yet built into the app) was shown in chat and confirmed working. Branch cut from `main` after v4.4 merged in. Merged into `main`, tagged `v4.4.1` -- see `docs/ROADMAP.md` for the full arc. (Renumbered from the originally-discussed `4.5` -- see the Branches table entry above for why.)
  ○ 4.4.2 (or later) bio login (face or finger) -- not started; number to be decided when actually built, not reserved in advance
- 5 - cosmetic
- 6 - tax management

## Merge strategy

- Each version is built, tested, and manually verified end-to-end on its
  own branch first.
- Merged into `main` with `git merge --no-ff` (an explicit merge commit,
  not a fast-forward and not squashed) -- keeps each version's individual
  commits visible in history rather than collapsing the whole build into
  one commit.
- No pull-request review today -- solo project, direct merge after local
  verification. Revisit (real PRs, CI checks before merge) if a
  collaborator ever joins.
- A new version's branch is cut from the *updated* `main`, after the prior
  version is merged in -- not stacked on top of the previous feature
  branch.
- **Tag the merge commit** (`git tag -a vN -m "..."`, matching the
  version's CHANGELOG.md headline, then `git push origin vN`) right after
  merging into `main`. Not optional bookkeeping -- `core/version.py`'s
  sidebar/backup-filename label only reads the branch name while you're
  *on* a version branch (e.g. `v4-...` -> "v4"); once merged, `main`'s
  branch name doesn't match that pattern, so it falls back to
  `git describe --tags`, which walks back to the nearest tag. Tagging
  stopped after v2.2 for several versions (v2.3 through v3.1 were never
  tagged) and nobody noticed until `main` was showing "v2.2-22-gXXXXXXX"
  instead of "v4" -- retroactively fixed by tagging each missed merge
  commit, but don't let it happen again.

## Commit messages

Observed style from `git log`: short imperative summary line (`Add
persistent storage and historical seed`, `Restructure: group core modules
under core/, fix bugs it surfaced`), no strict conventional-commits
prefix (`feat:`/`fix:`), but each message describes *what changed* clearly
enough to scan `git log --oneline` and reconstruct the build order. Merge
commits get a descriptive message too (not the git-generated default),
e.g. `Merge V1-record-trade-and-view: Record Trade, Record Dividend,
blended Dashboard`.

## Cleanup

Stale branches get deleted once fully merged and confirmed not needed
(`check-dashboard-numbers` was removed this way after V1 merged, since its
tip commit was already an ancestor of `main`). Feature branches that are
still useful as a labeled reference point (`V1-record-trade-and-view`,
`v2-Reconciliation`) are kept rather than deleted by default.

## Current status (as of `v4.4.1`, merged)

`main` has V1, V2, V2.1, V2.2, V2.3, V2.4, V3, V3.1, V4, V4.1.1, V4.1,
V4.1.2, V4.2, V4.3, V4.3.1, V4.4, and **V4.4.1** merged in (317/317
passing on `main`, tagged `v4.4.1`). V4.3 overhauls Rebalance &
Reallocate (5-tab split, Analyze as the sole editable tab, Total P/L,
Beta, THB calculator, Summary KPI redesign), adds a Monitor Stocks
Monthly Dividend chart (validated against a real broker statement
PDF), and fixes a real Streamlit rendering bug (bare `$` pairs read as
inline math) across 4 pages. V4.3.1 is two small direct-to-`main`
follow-ups (no feature branch, per explicit request): reorders
Rebalance & Reallocate's tabs (Analyze first) and adds an `Ex-Date`
column (Overview + Analyze) with the same current-month highlight
Monitor Stocks already has. A live production `KeyError` seen right
after V4.3's initial deploy turned out to be Streamlit Community
Cloud's incremental-redeploy leaving stale `__pycache__` bytecode, not
a code defect -- resolved via a full app Reboot, no code change
needed. V4.4 finally builds what V4.2 deferred -- automatic trend line
drawing -- as Monitor Stocks' Trendline tab (Pivot Points) plus a new
per-symbol "Auto Trendline" page, whose chart consolidated down to a
single "Reference Line" concept after iterating through several
overlays first. V4.4.1 builds a portfolio-wide "nearest Reference
Line" summary across all held symbols (Reference Lines + Highlight
tabs, Overview renamed to Overall), then a live-testing tweak round
that moved the "passed" highlight onto the Nearest Resistance/Support
cells themselves, reordered Highlight to the leftmost tab, trimmed
Overall's now-redundant Pivot Points columns, added a matching
"Passed R/S" column to Auto Trendline's own Zone 5 table, and fixed a
real pre-existing bug where visiting a symbol's page in a fresh
session silently wiped its `passed_at`. See `docs/ROADMAP.md`'s V4.4
and V4.4.1 sections for the full build and bug-fix history.

**Nothing in progress.** Still open: whether to apply the "Total
Return" concept (Total P/L/%) to the Dashboard page (raised during
v4.1); biometric login (number to be decided when actually built, see
the quick-plan note above).
