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
| `v3-hosting-prep` | V3 feature branch (hosting preparation -- deploy to the cloud, keep it free, make writes actually persist). Cut from `main` after v2.4 was merged in. Explored Hugging Face Spaces first (scored highest in an initial comparison) but reversed that decision mid-build once its Docker SDK turned out to require a paid PRO plan; landed on Streamlit Community Cloud (compute) + Turso (persistence) instead, decoupling the two since no single free host offered both. Live at `myinvestment27.streamlit.app`, verified working end-to-end including write persistence across a restart. |

Naming convention: `vN-short-description` (or `vN.M-short-description` for a
smaller, additive feature that doesn't warrant a new whole version number),
capital V on whole versions, hyphen-separated.

## My version quick note
Version planning
- 1 - basic "record trade and dividend"
- 2 - intermediate "reconciliation", "tools v1"
	○ 2.1 Classification dividend and growth
	○ 2.2 Monitor connect market price | retrieve these data symbol description, 90 day trend, adding asset class, portfolio group, beta, weight %
  ○ 2.3 system backup page (backup db + official statement xlsx)
  ○ 2.4 rebalance and label version in the web application
- 3 - hosting preparation and improvement (deploy to Streamlit Community Cloud + Turso, make source code cloud-compatible -- Hugging Face Spaces was the original target, reversed once its Docker SDK went paid-only)
- 4 - advance "dashhboard, tools v2, intetration"
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

## Current status (as of `v3-hosting-prep`)

`main` has V1, V2, V2.1, V2.2, V2.3, and V2.4 merged in. `v3-hosting-prep`
is built, tested (217/217), and verified working live at
`myinvestment27.streamlit.app` -- not yet merged into `main`. Next up per
"My version quick note" above, once v3 merges: v4, advance
dashboard/tools v2/integration.
