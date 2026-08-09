"""Pure calculation functions for the Rebalance & Reallocate feature.

Kept free of Streamlit imports so it can be unit tested in isolation
(see tests/test_rebalance.py) without needing a running app.
"""

import pandas as pd

from core import calculations, db, market_data

# Mirrors app_pages/monitor_stocks.py's own WITHHOLDING_TAX_RATE. Duplicated
# rather than imported -- core/ never imports from app_pages/ (see
# CLAUDE.md) -- but kept at the same 15% Thai (NRA) withholding assumption
# so this feature's dollar dividend figures stay consistent with every
# other page's "Expected Div" numbers.
WITHHOLDING_TAX_RATE = 0.15


def get_dividend_holdings(*, conn=None, yf_module=None) -> pd.DataFrame:
    """Current holdings for every Dividend-classified symbol still held
    (quantity > 0) -- the universe Section 3's table is built from. Merges
    db.fetch_symbol_types() (filtered to "Dividend"), calculations
    .compute_current_positions() (FIFO quantity/cost), and market_data
    .fetch_stock_profile() (live price, sector/industry, dividend rate). A
    Dividend-tagged symbol that's been fully sold out of is simply absent,
    same convention compute_current_positions() itself uses.

    Adds:
    - Classification: Sector for equities, Industry for everything else --
      same blended field app_pages/monitor_stocks.py uses for its own
      sector/asset-class pie, reused here for consistency.
    - Current Value: Quantity x Latest Price.
    - Current Cat Weight %: this symbol's share of the total dividend-
      basket value. Unlike monitor_stocks.py's "Category Weight %", no
      groupby is needed -- every row here is already Dividend, so it's a
      plain share-of-total.
    - Current Unrealized $ / %: Current Value vs Cost Basis, same shape as
      monitor_stocks.py's Unrealized columns.
    - Current Expected Div/Yr and Div/Mo: Current Value x Dividend Yield %,
      net of WITHHOLDING_TAX_RATE (matches Monitor Stocks' convention).
    - Current Expected Div/Yr %: this stock's own dividend yield, net of
      WITHHOLDING_TAX_RATE -- Dividend Yield % x (1 - WITHHOLDING_TAX_RATE).
      Quantity-independent (same value whether you hold 1 share or 1000),
      unlike Current Div Contrib % below, which is this stock's weighted
      contribution to the whole basket's blended yield.
    - Current Div Contrib %: this symbol's contribution to the whole
      basket's blended dividend yield -- Current Cat Weight % / 100 x
      Dividend Yield % x (1 - WITHHOLDING_TAX_RATE). Mirrors
      monitor_stocks.py's "Div Return Contribution %" column exactly.
      Summed across every row, reproduces the basket's overall blended
      yield (Total Expected Div/Yr / Total Value x 100) -- a small,
      high-yield holding can contribute as much here as a much larger,
      low-yield one, which Cat Weight % alone would hide.
    """
    symbol_types = db.fetch_symbol_types(conn=conn)
    dividend_symbols = set(symbol_types.loc[symbol_types["Allocation Type"] == "Dividend", "Symbol"])

    positions = calculations.compute_current_positions(db.fetch_trades(conn=conn))
    holdings = positions[positions["Symbol"].isin(dividend_symbols)].reset_index(drop=True)

    profile = market_data.fetch_stock_profile(holdings["Symbol"].tolist(), yf_module=yf_module)
    holdings = holdings.merge(profile, on="Symbol", how="left")

    holdings["Classification"] = holdings["Sector"].where(holdings["Quote Type"] == "EQUITY", holdings["Industry"])

    holdings["Current Value"] = holdings["Quantity"] * holdings["Latest Price"]
    total_value = holdings["Current Value"].sum()
    holdings["Current Cat Weight %"] = (holdings["Current Value"] / total_value * 100) if total_value else 0.0

    holdings["Current Unrealized $"] = holdings["Current Value"] - holdings["Cost Basis"]
    holdings["Current Unrealized %"] = (
        holdings["Current Unrealized $"] / holdings["Cost Basis"] * 100
    ).where(holdings["Cost Basis"] > 0, float("nan"))

    holdings["Current Expected Div/Yr"] = (
        holdings["Current Value"] * (holdings["Dividend Yield %"] / 100) * (1 - WITHHOLDING_TAX_RATE)
    )
    holdings["Current Expected Div/Mo"] = holdings["Current Expected Div/Yr"] / 12

    holdings["Current Expected Div/Yr %"] = holdings["Dividend Yield %"] * (1 - WITHHOLDING_TAX_RATE)

    holdings["Current Div Contrib %"] = (
        holdings["Current Cat Weight %"] / 100 * holdings["Dividend Yield %"] * (1 - WITHHOLDING_TAX_RATE)
    )

    return holdings


def apply_allocation(holdings: pd.DataFrame, amount: float, pct_by_symbol: dict) -> pd.DataFrame:
    """Adds New-prefixed columns showing the effect of investing `amount`
    dollars across `holdings`, split per pct_by_symbol (symbol -> % of
    `amount`; missing symbols default to 0% and percentages aren't required
    to sum to 100 -- unallocated % just isn't invested). Buys the extra
    shares of each symbol at its own Latest Price.

    New Unrealized $ works out equal to Current Unrealized $ (algebraically:
    New Value = Current Value + Invest $, New Cost Basis = Cost Basis +
    Invest $, so their difference is unchanged) -- the newly bought shares
    contribute zero unrealized gain/loss at the moment of purchase, which is
    the correct real-world effect of buying at the market price, not a
    modeling artifact. New Unrealized % still moves (down, usually) because
    the denominator (Cost Basis) grew while the numerator didn't.

    New Expected Div/Yr % mirrors Current Expected Div/Yr % -- Dividend
    Yield % x (1 - WITHHOLDING_TAX_RATE). Numerically unchanged from Current
    (a stock's own yield rate doesn't move just because you bought more of
    it at market price), same relationship as New Unrealized $ above.

    New Div Contrib % mirrors Current Div Contrib % (see
    get_dividend_holdings()) using New Cat Weight % instead -- summed
    across every row, gives the basket's blended yield after this
    allocation, for comparison against the current blended yield.

    New Total P/L = New Unrealized $ + Dividends Received -- requires the
    caller to have already merged a "Dividends Received" column into
    `holdings` (app_pages/rebalance.py does this; not computed here since
    it needs the xlsx + Streamlit caching this module deliberately stays
    free of). Numerically equal to Current Total P/L (New Unrealized $
    equals Current Unrealized $, and buying more doesn't change dividends
    already received) -- only New Total P/L % moves, since New Cost Basis
    grew. Same relationship as New Unrealized $ vs New Unrealized % above.
    """
    df = holdings.copy()
    pct = df["Symbol"].map(pct_by_symbol).fillna(0.0)
    df["Invest $"] = amount * pct / 100
    df["New Quantity"] = df["Quantity"] + df["Invest $"] / df["Latest Price"]
    df["New Cost Basis"] = df["Cost Basis"] + df["Invest $"]
    df["New Value"] = df["New Quantity"] * df["Latest Price"]

    new_total_value = df["New Value"].sum()
    df["New Cat Weight %"] = (df["New Value"] / new_total_value * 100) if new_total_value else 0.0

    df["New Unrealized $"] = df["New Value"] - df["New Cost Basis"]
    df["New Unrealized %"] = (
        df["New Unrealized $"] / df["New Cost Basis"] * 100
    ).where(df["New Cost Basis"] > 0, float("nan"))

    df["New Expected Div/Yr"] = df["New Value"] * (df["Dividend Yield %"] / 100) * (1 - WITHHOLDING_TAX_RATE)
    df["New Expected Div/Mo"] = df["New Expected Div/Yr"] / 12

    df["New Expected Div/Yr %"] = df["Dividend Yield %"] * (1 - WITHHOLDING_TAX_RATE)

    df["New Div Contrib %"] = df["New Cat Weight %"] / 100 * df["Dividend Yield %"] * (1 - WITHHOLDING_TAX_RATE)

    df["New Total P/L"] = df["New Unrealized $"] + df["Dividends Received"]
    df["New Total P/L %"] = (
        df["New Total P/L"] / df["New Cost Basis"] * 100
    ).where(df["New Cost Basis"] > 0, float("nan"))

    return df


def sector_breakdown(holdings: pd.DataFrame, value_col: str) -> pd.Series:
    """Groups by Classification (see get_dividend_holdings()), sums
    `value_col`, returns each group's % share of the total. Called once
    with "Current Value" and once with "New Value" (apply_allocation()'s
    output) to feed Pie 2's existing-vs-new comparison."""
    totals = holdings.groupby("Classification", dropna=True)[value_col].sum()
    total = totals.sum()
    return (totals / total * 100) if total else totals * 0.0
