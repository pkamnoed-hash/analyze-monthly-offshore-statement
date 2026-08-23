"""Pure calculation functions for the Target Allocation tracker.

Kept free of Streamlit imports so it can be unit tested in isolation
(see tests/test_target_allocation.py) without needing a running app.

Unlike core/rebalance.py's get_dividend_holdings() (which calls
market_data.fetch_stock_profile() live on every invocation -- flagged in
docs/ROADMAP.md as a known inefficiency), every function here takes
already-fetched data as a parameter. Nothing in this module calls
market_data.* or db.* directly, which also means the tests need no fake
yfinance module.

No hardcoded category list -- the category universe (Growth/Dividend/Others
today, potentially more later) is derived at call time as the union of
"categories with at least one held stock" and "categories with a stored
target", so a new category appearing in symbol_types needs zero code changes
here to be picked up correctly.

v4.9 -- RELATIVE-TO-PARENT targets. Every level's stored Target % is now a
share of its own parent, not of the whole portfolio: Category is relative to
the whole portfolio (it has no parent, so its stored value already IS the
effective one), Sector is relative to its Category, and Stock is relative to
its Sector -- the standard "pie of pies" shape used by nested allocation
tools generally (changing a parent's own split doesn't require retyping
every child underneath it, since each child is stored as a ratio).

Two Target-%-shaped columns appear at the Sector/Stock levels because of
this: "Target % of Parent" is the raw, user-edited, stored value (e.g.
Technology = 30 means "30% of Growth"). "Target %" is the *effective*,
whole-portfolio-comparable value (raw/100 x the parent's own effective
Target %) -- this is the one directly comparable to Actual %, and the one
Delta %/Status/Action/Trade $ are computed from at every level, matching the
column name and meaning this module already used before v4.9 (minimizing
the blast radius on every caller that already reads "Target %" expecting a
number comparable to Actual %).

Because Stock's effective % depends on Sector's, which depends on
Category's, the three levels can no longer be computed independently in any
order -- see compute_full_target_status() below for the required
tag -> category -> sector -> stock sequence, and tag_holdings_category()
for why Category-tagging had to be split out of what used to be
compute_stock_target_status()'s own first step.

v4.9.1 -- UNTARGETED status. A real-data check surfaced that "no target set
defaults to 0%" (true since v1) becomes much more visible once relative
targets cascade through 3 levels: with only Category targets ever saved
(the common early state), EVERY sector/stock effectively reads Target % = 0,
so anything held above the +/-2pp tolerance reads "Over Target -> Sell" --
technically correct under the 0%-default rule, but indistinguishable from a
genuine over-allocation and easy to misread as investment advice on data
that was never actually entered. Every level now carries an "Untargeted"
boolean, True when NEITHER this row's own Target % of Parent was ever
explicitly stored NOR its parent is itself Untargeted (propagates down
exactly like effective % does, just as a boolean OR instead of a product).
An EXPLICIT stored value of 0% (a real decision -- "I want zero of this")
is NOT Untargeted; only a genuinely absent row is. When Untargeted,
_classify() reports Status="Untargeted"/Action="--" instead of computing
Over/Short/Hit from a meaningless 0% comparison, and
compute_stock_target_status() blanks Trade $/Trade Shares for the same
reason (Hit Target rows are still blanked by callers for display -- a
UI choice; Untargeted rows are blanked here, in the calculation layer,
because the number itself is meaningless, not just uninteresting).
"""

import pandas as pd

from core import calculations

TOLERANCE_PCT = 2.0  # +/- 2 percentage points = "Hit Target" -- fixed in v1, not configurable


def _classify(delta_pct: pd.Series, untargeted: pd.Series = None) -> tuple[pd.Series, pd.Series]:
    """Shared Status/Action classification, used at every level so the
    +/-2pp tolerance band lives in exactly one place. delta_pct = Actual % -
    (effective) Target %. More than +TOLERANCE_PCT = Over Target/Sell; less
    than -TOLERANCE_PCT = Short Target/Buy More; within the band -- the
    boundary itself, exactly +/-2.0, still counts as Hit -- = Hit Target/Hold.
    Unaffected by the v4.9 relative-target rework: it only ever sees a plain
    delta_pct Series, with no notion of which level or parent it came from.

    v4.9.1 -- `untargeted` (optional, aligned to delta_pct's index) overrides
    any of the above to "Untargeted"/"--" wherever True -- see this module's
    own top docstring for why a genuinely never-configured row shouldn't be
    silently classified as Over/Short/Hit against an implicit 0% target."""
    status = pd.Series("Hit Target", index=delta_pct.index)
    status = status.mask(delta_pct > TOLERANCE_PCT, "Over Target")
    status = status.mask(delta_pct < -TOLERANCE_PCT, "Short Target")
    if untargeted is not None:
        status = status.mask(untargeted, "Untargeted")
    action = status.map({
        "Over Target": "Sell", "Short Target": "Buy More", "Hit Target": "Hold", "Untargeted": "—",
    })
    return status, action


def compute_actual_weights(trades: pd.DataFrame, profile: pd.DataFrame) -> pd.DataFrame:
    """Per-stock Actual % for every currently-held symbol (quantity > 0,
    FIFO -- calculations.compute_current_positions()). `profile` is an
    already-fetched DataFrame shaped like market_data.fetch_stock_profile()/
    db.fetch_market_profile_cache() (needs Symbol, Latest Price, Sector,
    Industry, Quote Type) -- dependency-injected by the caller, not fetched
    here, so this stays testable with a plain DataFrame.

    Current Value = Quantity x Latest Price. Actual % = Current Value /
    total portfolio value (sum across every row here, i.e. ALL
    currently-held symbols, matching Monitor Stocks' own "Weight %") x 100.
    A whole-portfolio fact, untouched by the v4.9 relative-target rework --
    only how Target % is computed changed, not Actual %.

    Classification = Sector for equities, Industry otherwise -- the same
    blended field core/rebalance.py's get_dividend_holdings() computes,
    reused here for consistency with the rest of the app.

    A symbol whose Latest Price is NaN (profile fetch failed for it -- see
    market_data.fetch_stock_profile()'s own convention) gets Current Value =
    NaN and Actual % = NaN; pandas' .sum() treats NaN as 0 when computing
    the total, so a failed-fetch symbol is silently EXCLUDED from the total
    portfolio value, which slightly overstates every other symbol's Actual
    %. Callers should surface any NaN Latest Price before trusting the
    Actual %/Status columns -- this function doesn't warn on it itself
    (calculations stay presentation-free).

    Returns: Symbol, Quantity, Avg Cost, Cost Basis, Latest Price,
    Classification, Current Value, Actual %."""
    positions = calculations.compute_current_positions(trades)
    holdings = positions.merge(
        profile[["Symbol", "Latest Price", "Sector", "Industry", "Quote Type"]], on="Symbol", how="left",
    )
    holdings["Classification"] = holdings["Sector"].where(holdings["Quote Type"] == "EQUITY", holdings["Industry"])
    holdings = holdings.drop(columns=["Sector", "Industry", "Quote Type"])

    holdings["Current Value"] = holdings["Quantity"] * holdings["Latest Price"]
    total_value = holdings["Current Value"].sum()
    holdings["Actual %"] = (holdings["Current Value"] / total_value * 100) if total_value else 0.0
    return holdings


def tag_holdings_category(holdings: pd.DataFrame, symbol_types: pd.DataFrame) -> pd.DataFrame:
    """v4.9 -- extracted from what used to be compute_stock_target_status()'s
    own first two lines. Splitting Category-tagging out into its own function
    is what makes the new relative-target chain possible at all: Category's
    own effective % has to be computed BEFORE Sector's (which scales by it),
    which has to happen BEFORE Stock's (which scales by Sector's) -- but
    computing Category status needs Category-tagged holdings, and tagging
    used to only happen inside the stock-level function, i.e. after the
    point where Category status would need it. Extracting it breaks that
    circular dependency; see compute_full_target_status() for the resulting
    call order.

    `holdings` = compute_actual_weights()'s output. `symbol_types` = shaped
    like db.fetch_symbol_types() (Symbol, Allocation Type). Untagged symbols
    default to "Others" -- the same convention db.fetch_symbol_types() itself
    already guarantees, re-applied here defensively for callers passing a
    partial frame.

    Returns `holdings` with one added column: Category."""
    df = holdings.merge(symbol_types.rename(columns={"Allocation Type": "Category"}), on="Symbol", how="left")
    df["Category"] = df["Category"].fillna("Others")
    return df


def compute_category_target_status(tagged_holdings: pd.DataFrame, category_targets: pd.DataFrame) -> pd.DataFrame:
    """Level 1 (category). Category has no parent -- the whole portfolio is
    its parent -- so its stored Target % already IS the effective,
    whole-portfolio-comparable value; nothing about this function's formula
    changed under the v4.9 relative-target rework, only its input changed
    from `stock_status` to `tagged_holdings` (tag_holdings_category()'s
    output), since stock-level Target %/Delta % computation now happens
    AFTER category status, not before (see compute_full_target_status()).

    Row set is every category actually held UNIONED with every category that
    has a stored target -- not a fixed list (see this module's own
    docstring) -- so a category with a target but nothing currently held in
    it still appears, at Actual % = 0.

    Category Value = sum of Current Value for that category's rows. Actual
    % = Category Value / TOTAL portfolio value
    (tagged_holdings["Current Value"].sum() -- the same grand total every
    stock's own Actual % was computed against) x 100. By construction this
    makes this function's Actual % column sum to ~100%, matching
    compute_actual_weights()'s own stock-level sum -- a real sanity check
    worth running against live data.

    `category_targets` = shaped like db.fetch_target_categories()
    (Category, Target %).

    v4.9.1 -- "Untargeted" is True for a category with no row at all in
    `category_targets` (never saved -- Section 1's own number_input only
    calls db.set_target_category_pct() when the value actually changes from
    its current default, so a category left untouched genuinely has no row).
    A category explicitly saved at 0% is NOT Untargeted -- that's a real
    decision, not an absence of one.

    Returns: Category, Current Value, Actual %, Target %, Delta %, Status,
    Action, Untargeted."""
    total_value = tagged_holdings["Current Value"].sum()

    universe = sorted(set(tagged_holdings["Category"]) | set(category_targets["Category"]))
    explicitly_targeted = set(category_targets["Category"])

    by_category = tagged_holdings.groupby("Category")["Current Value"].sum()
    by_category = by_category.reindex(universe, fill_value=0.0)

    df = pd.DataFrame({"Category": universe, "Current Value": by_category.values})
    df["Actual %"] = (df["Current Value"] / total_value * 100) if total_value else 0.0
    df = df.merge(category_targets, on="Category", how="left")
    df["Target %"] = df["Target %"].fillna(0.0)
    df["Untargeted"] = ~df["Category"].isin(explicitly_targeted)
    df["Delta %"] = df["Actual %"] - df["Target %"]
    df["Status"], df["Action"] = _classify(df["Delta %"], df["Untargeted"])
    return df


def compute_sector_target_status(
    tagged_holdings: pd.DataFrame, target_sectors: pd.DataFrame, category_status: pd.DataFrame,
) -> pd.DataFrame:
    """Level 2 (sector). v4.9 -- new `category_status` parameter (this
    level's parent's own status, from compute_category_target_status()) so a
    sector's raw stored Target % (now "% of its Category," not "% of the
    whole portfolio") can be scaled into an effective, whole-portfolio value
    before being compared against Actual %.

    Groups by (Category, Classification), summing Current Value; Actual % is
    that sum divided by the SAME total portfolio value every stock's own
    Actual % was computed against (tagged_holdings["Current Value"].sum() --
    across ALL holdings, not just this sector) -- so summing this function's
    Actual % across every row reproduces ~100%, matching
    compute_actual_weights()'s own stock-level sum. Unaffected by v4.9 --
    Actual % is still a whole-portfolio fact.

    "Target % of Parent" = the raw stored value from `target_sectors`,
    defaulting untargeted pairs to 0.0 (same defensive re-application as
    before). "Target %" (effective) = Target % of Parent / 100 x this row's
    Category's own effective Target % (from `category_status`) -- e.g. a
    sector stored at 30% inside a Category whose own effective target is
    60% has an effective Target % of 18%. If the Category's own effective
    target is 0% (untargeted), every sector under it is effectively 0% too,
    regardless of its own stored ratio -- multiplying by zero is zero; this
    is intentional (see this module's top docstring) and the UI should set
    expectations for it, not this calculation layer.

    Row set is every (Category, Classification) pair actually present among
    current holdings, UNIONED with every (Category, Sector) pair that has a
    stored target -- a sector with a target but nothing (currently) held in
    it still appears, at Actual % = 0, not silently dropped; a held sector
    with no stored target still appears, at Target % of Parent = 0.

    `target_sectors` = shaped like db.fetch_target_sectors() (Category,
    Sector, Target %). `category_status` must include the "Untargeted"
    column (compute_category_target_status()'s own output already does).

    v4.9.1 -- "Untargeted" is True when this (Category, Sector) pair has no
    row of its own in `target_sectors` (never saved), OR its Category is
    itself Untargeted -- propagates down exactly like effective % does
    (a product of ratios becomes an OR of "is anything missing" flags). A
    sector explicitly saved at 0% under a targeted Category is NOT
    Untargeted -- that's a real "I want zero of this" decision.

    Returns: Category, Sector, Current Value, Actual %, Target % of Parent,
    Target %, Delta %, Status, Action, Untargeted."""
    total_value = tagged_holdings["Current Value"].sum()

    held_pairs = tagged_holdings[["Category", "Classification"]].drop_duplicates().rename(
        columns={"Classification": "Sector"}
    )
    targeted_pairs = target_sectors[["Category", "Sector"]]
    universe = pd.concat([held_pairs, targeted_pairs], ignore_index=True).drop_duplicates()

    by_pair = (
        tagged_holdings.groupby(["Category", "Classification"])["Current Value"]
        .sum()
        .reset_index()
        .rename(columns={"Classification": "Sector"})
    )

    df = universe.merge(by_pair, on=["Category", "Sector"], how="left")
    df["Current Value"] = df["Current Value"].fillna(0.0)
    df["Actual %"] = (df["Current Value"] / total_value * 100) if total_value else 0.0

    # .astype(bool) after fillna is required, not cosmetic: a left-merge leaves the
    # unmatched rows of a bool column as NaN, which upcasts the whole column to object
    # dtype -- fillna(False) then still leaves it object-dtype (a mix of real Python
    # bool and former-NaN bool). `~` on an object-dtype "bool" silently does Python's
    # bitwise-NOT on the underlying int (~True == -2, ~False == -1 -- both truthy!),
    # not logical negation, corrupting every downstream Untargeted computation.
    has_own_target = target_sectors[["Category", "Sector"]].assign(_has_own=True)
    df = df.merge(has_own_target, on=["Category", "Sector"], how="left")
    df["_has_own"] = df["_has_own"].fillna(False).astype(bool)

    df = df.merge(target_sectors, on=["Category", "Sector"], how="left")
    df["Target %"] = df["Target %"].fillna(0.0)
    df = df.rename(columns={"Target %": "Target % of Parent"})

    category_effective = category_status[["Category", "Target %", "Untargeted"]].rename(
        columns={"Target %": "_category_effective_pct", "Untargeted": "_category_untargeted"}
    )
    df = df.merge(category_effective, on="Category", how="left")
    df["_category_effective_pct"] = df["_category_effective_pct"].fillna(0.0)
    df["_category_untargeted"] = df["_category_untargeted"].fillna(True).astype(bool)
    df["Target %"] = df["Target % of Parent"] / 100 * df["_category_effective_pct"]
    df["Untargeted"] = (~df["_has_own"]) | df["_category_untargeted"]
    df = df.drop(columns=["_has_own", "_category_effective_pct", "_category_untargeted"])

    df["Delta %"] = df["Actual %"] - df["Target %"]
    df["Status"], df["Action"] = _classify(df["Delta %"], df["Untargeted"])
    return df.reset_index(drop=True)


def compute_stock_target_status(
    tagged_holdings: pd.DataFrame, target_allocations: pd.DataFrame, sector_status: pd.DataFrame,
) -> pd.DataFrame:
    """Level 3 (stock). v4.9 -- `sector_status` replaces the old
    `symbol_types` parameter: Category-tagging now happens upstream, in
    tag_holdings_category() (see compute_full_target_status()), and this
    level needs its parent Sector's own effective % (from `sector_status`)
    to scale its own raw stored Target % into an effective one -- the same
    scaling relationship compute_sector_target_status() has with Category.

    "Target % of Parent" = the raw stored value from `target_allocations`
    (from db.fetch_target_allocations(), Symbol + Target %), defaulting
    untargeted symbols to 0.0. "Target %" (effective) = Target % of Parent /
    100 x this stock's own Sector's effective Target % (looked up from
    `sector_status` by (Category, Sector)) -- e.g. a stock stored at 50%
    inside a sector whose own effective target is 18% has an effective
    Target % of 9%. `Delta %` = Actual % - (effective) Target %, `Status`/
    `Action` via the shared ±2pp `_classify()`, exactly as before.

    A held stock with no target ever set defaults to Target % of Parent =
    0.0 -- NOT unconditionally "Over Target": its effective Target % is then
    also 0, so it only reads Over Target if its own Actual % exceeds +2pp
    (Delta % = Actual % - 0). A small, newly-bought, not-yet-targeted
    position (e.g. Actual % = 1.5%) reads Hit Target even at the 0% default,
    since 1.5 - 0 = 1.5, inside the +/-2pp band -- unchanged from before.

    Trade $ / Trade Shares answer "how much should I actually buy or sell"
    -- deliberately the OPPOSITE sign convention from Delta % (where positive
    means "you have too much"): here, positive means BUY this many
    dollars/shares, negative means SELL, so the number is directly
    actionable. Trade $ = -(Delta % / 100 x total portfolio value) -- e.g.
    Over Target (Delta % > 0, too much) gives a negative Trade $ (sell).
    Trade Shares = Trade $ / Latest Price. Both are a same-total-value
    approximation: buying Trade $ worth with fresh cash actually grows the
    portfolio total slightly, which would in turn shift every OTHER
    symbol's own target dollar amount too -- a good starting point, not an
    exact prescription, same simplification core/rebalance.py's own
    "Invest $" already makes. Unchanged formula-wise from before v4.9 --
    only now fed the effective Target % instead of a raw absolute one.

    `tagged_holdings` = tag_holdings_category()'s output (Category already
    merged in). `sector_status` must include the "Untargeted" column
    (compute_sector_target_status()'s own output already does).

    v4.9.1 -- "Untargeted" is True when this Symbol has no row of its own in
    `target_allocations` (never saved), OR its Sector is itself Untargeted --
    same propagation as compute_sector_target_status() has with Category. A
    stock explicitly saved at 0% under a targeted Sector is NOT Untargeted.
    When Untargeted, Trade $/Trade Shares are blanked here (not left to
    callers, unlike the Hit-Target blanking every caller does for display) --
    the number is meaningless with no real target to trade toward, not just
    uninteresting to show.

    Returns: Symbol, ..., Category, Target % of Parent, Target %, Delta %,
    Status, Action, Trade $, Trade Shares, Untargeted."""
    df = tagged_holdings.merge(target_allocations, on="Symbol", how="left")
    df["Target %"] = df["Target %"].fillna(0.0)
    df = df.rename(columns={"Target %": "Target % of Parent"})

    # .astype(bool) after fillna required here too -- see compute_sector_target_status()'s
    # matching comment for why an object-dtype "bool" column silently breaks `~`.
    has_own_target = target_allocations[["Symbol"]].assign(_has_own=True)
    df = df.merge(has_own_target, on="Symbol", how="left")
    df["_has_own"] = df["_has_own"].fillna(False).astype(bool)

    sector_effective = sector_status[["Category", "Sector", "Target %", "Untargeted"]].rename(
        columns={"Sector": "Classification", "Target %": "_sector_effective_pct", "Untargeted": "_sector_untargeted"}
    )
    df = df.merge(sector_effective, on=["Category", "Classification"], how="left")
    df["_sector_effective_pct"] = df["_sector_effective_pct"].fillna(0.0)
    df["_sector_untargeted"] = df["_sector_untargeted"].fillna(True).astype(bool)
    df["Target %"] = df["Target % of Parent"] / 100 * df["_sector_effective_pct"]
    df["Untargeted"] = (~df["_has_own"]) | df["_sector_untargeted"]
    df = df.drop(columns=["_has_own", "_sector_effective_pct", "_sector_untargeted"])

    df["Delta %"] = df["Actual %"] - df["Target %"]
    df["Status"], df["Action"] = _classify(df["Delta %"], df["Untargeted"])
    total_value = df["Current Value"].sum()
    df["Trade $"] = -(df["Delta %"] / 100 * total_value)
    df.loc[df["Untargeted"], "Trade $"] = None
    df["Trade Shares"] = df["Trade $"] / df["Latest Price"]
    return df


def compute_full_target_status(
    trades: pd.DataFrame,
    profile: pd.DataFrame,
    symbol_types: pd.DataFrame,
    category_targets: pd.DataFrame,
    target_sectors: pd.DataFrame,
    target_allocations: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """v4.9 -- single entry point running the full tag -> category -> sector
    -> stock pipeline in the dependency order the relative-target model now
    requires (each level's effective % depends on the one above it having
    already been computed). Every caller (the Target Allocation page itself,
    and the three v4.8 reminder touchpoints on Record Trade/Rebalance &
    Reallocate/Monitor Stocks) should use this instead of hand-rolling the
    old two-call compute_actual_weights()+compute_stock_target_status()
    pattern, both for less duplicated wiring and so a future change to the
    pipeline order only has to happen in one place.

    Returns (category_status, sector_status, stock_status) -- the outputs of
    compute_category_target_status(), compute_sector_target_status(), and
    compute_stock_target_status() respectively, all fed from the same
    tagged holdings so their Category/Sector groupings agree with each
    other."""
    holdings = compute_actual_weights(trades, profile)
    tagged = tag_holdings_category(holdings, symbol_types)
    category_status = compute_category_target_status(tagged, category_targets)
    sector_status = compute_sector_target_status(tagged, target_sectors, category_status)
    stock_status = compute_stock_target_status(tagged, target_allocations, sector_status)
    return category_status, sector_status, stock_status


def sum_stock_targets_by_sector(stock_status: pd.DataFrame) -> pd.DataFrame:
    """Informational only, per user request -- sums each (Category,
    Sector)'s currently-held stocks' own **raw** "Target % of Parent" (NOT
    the effective "Target %" -- summing effective/whole-portfolio values
    wouldn't answer a meaningful question here). v4.9 -- this sum is now
    directly comparable to a flat 100%, since "do this sector's stocks add
    up to 100% of the sector" is exactly what "% of Parent" values summing
    to 100 means; before v4.9 this was only comparable to the sector's own
    absolute target, a much less intuitive check. Not enforced to equal
    100 -- the two are independently user-set values, this is just so the UI
    can show the running total for the user to eyeball. Scoped to (Category,
    Classification) pairs actually held -- a pair with nothing held has no
    stock targets to sum in the first place, unlike
    compute_sector_target_status()'s fuller universe.

    Returns: Category, Sector, Target % of Parent."""
    totals = stock_status.groupby(["Category", "Classification"])["Target % of Parent"].sum()
    return totals.reset_index().rename(columns={"Classification": "Sector"})


def sum_sector_targets_by_category(sector_status: pd.DataFrame) -> pd.DataFrame:
    """Informational only -- sums each category's sectors' own **raw**
    "Target % of Parent" (from compute_sector_target_status()'s output), for
    eyeballing against a flat 100% (v4.9 -- "do this category's sectors add
    up to 100% of the category," replacing the old "vs the category's own
    absolute target" comparison). Same non-enforcement as
    sum_stock_targets_by_sector().

    Returns: Category, Target % of Parent."""
    totals = sector_status.groupby("Category")["Target % of Parent"].sum()
    return totals.reset_index()
