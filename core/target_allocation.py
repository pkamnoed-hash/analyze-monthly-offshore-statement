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
"""

import pandas as pd

from core import calculations

TOLERANCE_PCT = 2.0  # +/- 2 percentage points = "Hit Target" -- fixed in v1, not configurable


def _classify(delta_pct: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Shared Status/Action classification, used at every level so the
    +/-2pp tolerance band lives in exactly one place. delta_pct = Actual % -
    Target %. More than +TOLERANCE_PCT = Over Target/Sell; less than
    -TOLERANCE_PCT = Short Target/Buy More; within the band -- the boundary
    itself, exactly +/-2.0, still counts as Hit -- = Hit Target/Hold."""
    status = pd.Series("Hit Target", index=delta_pct.index)
    status = status.mask(delta_pct > TOLERANCE_PCT, "Over Target")
    status = status.mask(delta_pct < -TOLERANCE_PCT, "Short Target")
    action = status.map({"Over Target": "Sell", "Short Target": "Buy More", "Hit Target": "Hold"})
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


def compute_stock_target_status(
    holdings: pd.DataFrame, symbol_types: pd.DataFrame, target_allocations: pd.DataFrame,
) -> pd.DataFrame:
    """Level 3 (stock). Adds Category (from symbol_types, defaulting
    untagged to "Others" -- the same convention db.fetch_symbol_types()
    itself already guarantees, re-applied here defensively for callers
    passing a partial frame), Target % (from target_allocations, defaulting
    untargeted to 0.0, same defensive re-application), Delta % (Actual % -
    Target %), Status, Action, Trade $, Trade Shares.

    `holdings` = compute_actual_weights()'s output. `symbol_types` = shaped
    like db.fetch_symbol_types() (Symbol, Allocation Type).
    `target_allocations` = shaped like db.fetch_target_allocations()
    (Symbol, Target %).

    A held symbol with no target ever set defaults to Target % = 0.0 -- NOT
    unconditionally "Over Target": it only reads Over Target if its own
    Actual % exceeds +2pp (Delta % = Actual % - 0). A small, newly-bought,
    not-yet-targeted position (e.g. Actual % = 1.5%) reads Hit Target even
    at the 0% default, since 1.5 - 0 = 1.5, inside the +/-2pp band.

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
    "Invest $" already makes."""
    df = holdings.merge(symbol_types.rename(columns={"Allocation Type": "Category"}), on="Symbol", how="left")
    df["Category"] = df["Category"].fillna("Others")
    df = df.merge(target_allocations, on="Symbol", how="left")
    df["Target %"] = df["Target %"].fillna(0.0)
    df["Delta %"] = df["Actual %"] - df["Target %"]
    df["Status"], df["Action"] = _classify(df["Delta %"])
    total_value = df["Current Value"].sum()
    df["Trade $"] = -(df["Delta %"] / 100 * total_value)
    df["Trade Shares"] = df["Trade $"] / df["Latest Price"]
    return df


def compute_sector_target_status(stock_status: pd.DataFrame, target_sectors: pd.DataFrame) -> pd.DataFrame:
    """Level 2 (sector). Groups by (Category, Classification), summing
    Current Value; Actual % is that sum divided by the SAME total portfolio
    value every stock's own Actual % was computed against
    (stock_status["Current Value"].sum() -- across ALL holdings, not just
    this sector) -- so summing this function's Actual % across every row
    reproduces ~100%, matching compute_actual_weights()'s own stock-level
    sum.

    Row set is every (Category, Classification) pair actually present among
    current holdings, UNIONED with every (Category, Sector) pair that has a
    stored target -- a sector with a target but nothing (currently) held in
    it still appears, at Actual % = 0, not silently dropped; a held sector
    with no stored target still appears, at Target % = 0.

    `target_sectors` = shaped like db.fetch_target_sectors() (Category,
    Sector, Target %).

    Returns: Category, Sector, Current Value, Actual %, Target %, Delta %,
    Status, Action."""
    total_value = stock_status["Current Value"].sum()

    held_pairs = stock_status[["Category", "Classification"]].drop_duplicates().rename(
        columns={"Classification": "Sector"}
    )
    targeted_pairs = target_sectors[["Category", "Sector"]]
    universe = pd.concat([held_pairs, targeted_pairs], ignore_index=True).drop_duplicates()

    by_pair = (
        stock_status.groupby(["Category", "Classification"])["Current Value"]
        .sum()
        .reset_index()
        .rename(columns={"Classification": "Sector"})
    )

    df = universe.merge(by_pair, on=["Category", "Sector"], how="left")
    df["Current Value"] = df["Current Value"].fillna(0.0)
    df["Actual %"] = (df["Current Value"] / total_value * 100) if total_value else 0.0

    df = df.merge(target_sectors, on=["Category", "Sector"], how="left")
    df["Target %"] = df["Target %"].fillna(0.0)
    df["Delta %"] = df["Actual %"] - df["Target %"]
    df["Status"], df["Action"] = _classify(df["Delta %"])
    return df.reset_index(drop=True)


def compute_category_target_status(stock_status: pd.DataFrame, category_targets: pd.DataFrame) -> pd.DataFrame:
    """Level 1 (category). Row set is every category actually held UNIONED
    with every category that has a stored target -- not a fixed list (see
    this module's own docstring) -- so a category with a target but nothing
    currently held in it still appears, at Actual % = 0.

    Category Value = sum of Current Value for that category's rows. Actual
    % = Category Value / TOTAL portfolio value
    (stock_status["Current Value"].sum() -- the same grand total every
    stock's own Actual % was computed against) x 100. By construction this
    makes this function's Actual % column sum to ~100%, matching
    compute_actual_weights()'s own stock-level sum -- a real sanity check
    worth running against live data.

    `category_targets` = shaped like db.fetch_target_categories()
    (Category, Target %).

    Returns: Category, Current Value, Actual %, Target %, Delta %, Status, Action."""
    total_value = stock_status["Current Value"].sum()

    universe = sorted(set(stock_status["Category"]) | set(category_targets["Category"]))

    by_category = stock_status.groupby("Category")["Current Value"].sum()
    by_category = by_category.reindex(universe, fill_value=0.0)

    df = pd.DataFrame({"Category": universe, "Current Value": by_category.values})
    df["Actual %"] = (df["Current Value"] / total_value * 100) if total_value else 0.0
    df = df.merge(category_targets, on="Category", how="left")
    df["Target %"] = df["Target %"].fillna(0.0)
    df["Delta %"] = df["Actual %"] - df["Target %"]
    df["Status"], df["Action"] = _classify(df["Delta %"])
    return df


def sum_stock_targets_by_sector(stock_status: pd.DataFrame) -> pd.DataFrame:
    """Informational only, per user request -- sums each (Category,
    Sector)'s currently-held stocks' own Target % (NOT enforced to equal
    that sector's own stored target; the two are independently user-set
    values, this is just so the UI can show both side by side for the user
    to eyeball). Scoped to (Category, Classification) pairs actually held --
    a pair with nothing held has no stock targets to sum in the first
    place, unlike compute_sector_target_status()'s fuller universe.

    Returns: Category, Sector, Target %."""
    totals = stock_status.groupby(["Category", "Classification"])["Target %"].sum()
    return totals.reset_index().rename(columns={"Classification": "Sector"})


def sum_sector_targets_by_category(sector_status: pd.DataFrame) -> pd.DataFrame:
    """Informational only -- sums each category's sectors' own stored
    Target % (from compute_sector_target_status()'s output), for eyeballing
    against that category's own stored target. Same non-enforcement as
    sum_stock_targets_by_sector().

    Returns: Category, Target %."""
    totals = sector_status.groupby("Category")["Target %"].sum()
    return totals.reset_index()
