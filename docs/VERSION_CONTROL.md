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
| `v2.3.1-rebalance-reallocate` | Rebalance / Reallocate Investment feature branch. Cut from `main` after v2.2 was merged in. Restarted from scratch under this name after an initial `v2.3-rebalance-reallocate` attempt (Steps 1-4 built and tested, but never committed to git) was discarded and rebuilt as v2.3.1 per the user's own version-numbering choice. |

Naming convention: `vN-short-description` (or `vN.M-short-description` for a
smaller, additive feature that doesn't warrant a new whole version number),
capital V on whole versions, hyphen-separated.

## My version quick note
Version planning
- 1 - basic "record trade and dividend"
- 2 - intermediate "reconciliation", "tools v1"
	○ 2.1 Classification dividend and growth
	○ 2.2 Monitor connect market price | retrieve these data symbol description, 90 day trend, adding asset class, portfolio group, beta, weight %
  ○ 2.3 rebalance and label version in the web application
  ○ 2.4 simplify UXUI for usage (e.g., adding, updating, etc)
  ○ 2.5 add backing up data feature
- 3 - advance "dashhboard, tools v2, intetration" 
- 4 - cosmetic
- 5 - tax management

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

## Current status (as of `v2.3.1-rebalance-reallocate`)

`main` has V1, V2, V2.1, and V2.2 merged in (`v2.2-monitor-stocks` merged
via `git merge --no-ff`, 164/164 passing on `main` post-merge). Work has
restarted on `v2.3.1-rebalance-reallocate`, cut from that updated `main`.
