"""Live market-data enrichment (description, asset class, beta, 90-day
price history) via yfinance. Kept free of Streamlit imports so it can be
unit tested in isolation (see tests/test_market_data.py, which injects a
fake yfinance-like module and never touches the real network).
"""

import pandas as pd
import yfinance as _yfinance

HISTORY_DAYS = 90
DIVIDEND_LOOKBACK_DAYS = 365

_EMPTY_COLUMNS = {
    "Symbol": "object",
    "Description": "object",
    "Sector": "object",
    "Industry": "object",
    "Quote Type": "object",
    "Beta": "float64",
    "History90D": "object",
    "Latest Price": "float64",
    "High90D": "float64",
    "Low90D": "float64",
    "Dividend Per Year": "float64",
    "Dividend Yield %": "float64",
    "Dividend Frequency": "object",
    "Ex-Date": "datetime64[ns]",
}

_FREQUENCY_LABELS = {0: "None", 1: "Annual", 2: "Semi-Annual", 4: "Quarterly", 12: "Monthly"}


def _frequency_label(payout_count: int) -> str:
    return _FREQUENCY_LABELS.get(payout_count, f"Irregular ({payout_count}/yr)")


def fetch_usd_thb_rate(*, yf_module=None) -> float | None:
    """Live USD -> THB quote for the Dashboard's THB reference figure.
    yfinance quotes FX pairs (THB=X) through the same Ticker/.history()
    call fetch_stock_profile already uses for equities/ETFs -- no new API
    or dependency needed. A 5-calendar-day lookback (not just today) covers
    weekends/holidays where the most recent FX session isn't literally
    today. Returns None on any failure (network error, empty history) so
    the caller can fall back to a hardcoded default -- this is a "nice to
    have" pre-fill, not something the page should ever block on."""
    yf_module = yf_module or _yfinance
    today = pd.Timestamp.today().normalize()
    start = today - pd.Timedelta(days=5)
    end = today + pd.Timedelta(days=1)
    try:
        history = yf_module.Ticker("THB=X").history(start=start, end=end)
        if history is None or history.empty:
            return None
        return float(history["Close"].iloc[-1])
    except Exception:
        return None


def fetch_stock_profile(symbols: list[str], *, yf_module=None) -> pd.DataFrame:
    """Batch-fetches, per symbol, exactly what the Monitor Stocks page's
    first pass needs: Description (longName/shortName), Sector, Industry
    (both from yfinance's info dict -- displayed on the page as "Portfolio
    Group" and "Asset Class" respectively, matching the prototype project's
    reference table), Beta, and daily closing prices over the last 90
    *calendar* days -- the latter doubles as both the "90 Day Trend"
    sparkline source and the latest close used for Weight % (no separate
    "current price" call this pass; Step 0 found yfinance's own
    currentPrice/regularMarketPrice fields inconsistent across symbol
    types -- e.g. present for an equity but None for a short-duration bond
    ETF -- so the history's own last close is used instead, which is
    always present whenever history succeeds at all).

    Sector/Industry are equity-specific concepts -- yfinance leaves them
    blank for virtually every ETF (confirmed: 28 of 52 real holdings, almost
    all ETFs). Real ETF-appropriate fields exist in the same info dict
    though: `category` (a Morningstar-style fund classification, e.g.
    "Ultrashort Bond", "High Yield Bond") and `fundFamily` (the issuer,
    e.g. "iShares", "Schwab ETFs"). Used here as fallbacks -- Sector falls
    back to fundFamily, Industry falls back to category -- only when the
    equity-specific field is blank, so equities are unaffected. Not a
    perfect semantic match (fundFamily is "who issued it," not "what
    sector it's in"), but real, free, and beats a blank cell.

    Beta has the same equity-vs-fund gap: yfinance's `beta` (Yahoo's
    5-year-monthly figure) is blank for most ETFs (confirmed: 29 of 52
    real holdings); `beta3Year` (Yahoo's 3-year-monthly figure, the
    convention funds are typically reported under -- confirmed to match
    finviz.com's own ETF Beta value for a real holding) fills in as a
    fallback when `beta` is blank. Confirmed no equity has `beta3Year`
    populated, so this can't override a real equity beta.

    Also returns `Quote Type` (yfinance's `quoteType`, e.g. "EQUITY"/"ETF")
    -- used by the page to decide, per row, whether Sector or Industry is
    the more meaningful classification for a given holding (e.g. for the
    blended Sector/Asset-Class pie chart).

    Dividends have the same equity-vs-fund gap as Sector/Industry/Beta, but
    the fix is different: yfinance's `dividendRate`/`trailingAnnualDividendRate`
    info-dict fields are blank or `0.0` for ETFs even when they clearly pay
    dividends (confirmed: SHV, SCHD both showed $0.00 despite real payouts).
    Instead, `Dividend Per Year`/`Dividend Yield %`/`Dividend Frequency` are
    computed from `Ticker.dividends` -- yfinance's actual per-share payout
    history with real dates -- summed over the trailing `DIVIDEND_LOOKBACK_DAYS`
    (365) calendar days, which works correctly for both equities and ETFs.
    `Dividend Yield %` is `Dividend Per Year / Latest Price * 100`, computed
    here rather than on the page since both inputs already live in this same
    function -- deliberately not yfinance's own `dividendYield` field, which
    is correctly populated but doesn't always reconcile exactly to Dividend
    Per Year / Latest Price. `Dividend Frequency` is a label derived from the
    trailing-365-day payout count (0->"None", 1->"Annual", 2->"Semi-Annual",
    4->"Quarterly", 12->"Monthly", else->"Irregular (N/yr)"). A symbol with no
    payouts in the window (confirmed real: ARM, a non-dividend-paying equity)
    gets `0.0`/`0.0`/"None" -- valid data, not a fetch failure -- distinct
    from an unresolvable symbol, which gets NaN/NaN/None like every other
    field in the exception branch below.

    `Ex-Date` is the most recent entry in `Ticker.dividends`'s own index
    (unbounded by the 365-day window above, since it's just "when was the
    last one," not a sum) -- populated for every dividend payer,
    weekly/monthly funds included. (A `Payout Date` column was tried
    alongside this, sourced from `info["dividendDate"]`, but that field is
    blank for most funds and confirmed *stale* for at least one ETF -- SHV
    returned 2018-04-06 for a fund paying monthly in 2026 -- so it was
    removed rather than shown unreliably.)

    `High90D`/`Low90D` are the max High / min Low over that same 90-day
    history -- no separate fetch, just two more aggregates off the `history`
    DataFrame already pulled for `History90D`/`Latest Price`. Persisted as
    part of `market_profile_cache` (see core/db.py) alongside the rest of
    this profile.

    Deliberately uses explicit `start`/`end` dates (calendar days), not
    yfinance's `period="90d"` shorthand -- confirmed by direct comparison
    that `period="90d"` actually returns 90 *trading* days, spanning ~131
    calendar days (about 4.3 months), not 90 calendar days. That silently
    mismatched the user's own reference formula
    (`GOOGLEFINANCE(symbol, "Price", TODAY()-90, TODAY())`, which is
    calendar-day arithmetic) -- explicit dates here match it exactly.

    `yf_module` can be injected for testing (see tests/test_market_data.py)
    -- production callers always omit it and get the real yfinance module.
    A symbol yfinance can't resolve, or that raises for any other reason
    (network failure, no history available), gets a NaN/empty row rather
    than aborting the whole batch -- mirrors db.fetch_symbol_types()'s
    "never silently drop a row" convention."""
    yf_module = yf_module or _yfinance

    # end is exclusive in yfinance's start/end range, so +1 day includes today's session --
    # matches GOOGLEFINANCE(symbol, "Price", TODAY()-90, TODAY())'s inclusive-of-today range.
    today = pd.Timestamp.today().normalize()
    start = today - pd.Timedelta(days=HISTORY_DAYS)
    end = today + pd.Timedelta(days=1)

    rows = []
    for symbol in symbols:
        try:
            ticker = yf_module.Ticker(symbol)
            info = ticker.info or {}
            history = ticker.history(start=start, end=end)
            if history is None or history.empty:
                raise ValueError(f"No price history returned for {symbol}")
            history_90d = history["Close"].tolist()
            high_90d = float(history["High"].max())
            low_90d = float(history["Low"].min())
            # Beta is equity-specific too, same as Sector/Industry above -- yfinance's
            # standard `beta` (Yahoo's 5-year-monthly figure) is blank for most ETFs;
            # `beta3Year` (Yahoo's 3-year-monthly figure, the convention funds are
            # typically reported under) fills in for those. Confirmed no equity has
            # `beta3Year` populated, so this can't override a real equity beta.
            beta = info.get("beta")
            if beta is None:
                beta = info.get("beta3Year")
            latest_price = float(history_90d[-1])

            dividends = ticker.dividends
            if dividends is not None and not dividends.empty:
                div_index = pd.to_datetime(dividends.index).tz_localize(None)
                trailing_divs = dividends[div_index >= today - pd.Timedelta(days=DIVIDEND_LOOKBACK_DAYS)]
                dividend_per_year = float(trailing_divs.sum())
                payout_count = len(trailing_divs)
                ex_date = div_index.max()
            else:
                dividend_per_year = 0.0
                payout_count = 0
                ex_date = pd.NaT
            dividend_yield_pct = (dividend_per_year / latest_price * 100) if latest_price else 0.0

            rows.append({
                "Symbol": symbol,
                "Description": info.get("longName") or info.get("shortName"),
                "Sector": info.get("sector") or info.get("fundFamily"),
                "Industry": info.get("industry") or info.get("category"),
                "Quote Type": info.get("quoteType"),
                "Beta": float(beta) if beta is not None else float("nan"),
                "History90D": history_90d,
                "Latest Price": latest_price,
                "High90D": high_90d,
                "Low90D": low_90d,
                "Dividend Per Year": dividend_per_year,
                "Dividend Yield %": dividend_yield_pct,
                "Dividend Frequency": _frequency_label(payout_count),
                "Ex-Date": ex_date,
            })
        except Exception:
            rows.append({
                "Symbol": symbol,
                "Description": None,
                "Sector": None,
                "Industry": None,
                "Quote Type": None,
                "Beta": float("nan"),
                "History90D": [],
                "Latest Price": float("nan"),
                "High90D": float("nan"),
                "Low90D": float("nan"),
                "Dividend Per Year": float("nan"),
                "Dividend Yield %": float("nan"),
                "Dividend Frequency": None,
                "Ex-Date": pd.NaT,
            })

    if not rows:
        return pd.DataFrame({col: pd.Series([], dtype=dtype) for col, dtype in _EMPTY_COLUMNS.items()})
    return pd.DataFrame(rows)


_PRICE_HISTORY_COLUMNS = ["Date", "Open", "High", "Low", "Close"]


def fetch_price_history(symbol: str, days: int, *, yf_module=None) -> pd.DataFrame:
    """Daily OHLC for a single symbol over the trailing `days` calendar days --
    used by the Symbol Analysis drill-down page (app_pages/symbol_analysis.py)
    for its candlestick chart. Distinct from fetch_stock_profile() above,
    which batches many symbols but returns only one aggregated row each (a
    Close-price list, plus 90-day High/Low scalars) -- this returns the full
    per-day series for one symbol, with a `days` window the caller controls
    (the page fetches 365 once, then slices for its 90D/6M/1Y toggle).

    Returns an empty DataFrame (right columns, zero rows) on any failure
    (unresolved symbol, network error) rather than raising -- matches
    fetch_stock_profile()'s "never abort, return something inspectable"
    convention. `yf_module` can be injected for testing (see
    tests/test_market_data.py); production callers always omit it."""
    yf_module = yf_module or _yfinance
    today = pd.Timestamp.today().normalize()
    start = today - pd.Timedelta(days=days)
    end = today + pd.Timedelta(days=1)

    try:
        history = yf_module.Ticker(symbol).history(start=start, end=end)
        if history is None or history.empty:
            return pd.DataFrame({col: pd.Series([], dtype="float64" if col != "Date" else "datetime64[ns]") for col in _PRICE_HISTORY_COLUMNS})
    except Exception:
        return pd.DataFrame({col: pd.Series([], dtype="float64" if col != "Date" else "datetime64[ns]") for col in _PRICE_HISTORY_COLUMNS})

    out = history[["Open", "High", "Low", "Close"]].copy()
    out.insert(0, "Date", pd.to_datetime(out.index).tz_localize(None))
    return out.reset_index(drop=True)


_FUNDAMENTALS_EMPTY_COLUMNS = {
    "Symbol": "object",
    "Currency": "object",
    "Financial Currency": "object",
    "Current Price": "float64",
    "Target Mean Price": "float64",
    "Number Of Analysts": "float64",
    "Income Statement": "object",
    "Balance Sheet": "object",
    "Cash Flow": "object",
}


def _statement_to_dict(statement: pd.DataFrame) -> dict:
    """Converts one yfinance statement DataFrame (index = row label, e.g. "Total
    Revenue"; columns = period-end Timestamps) into a JSON-friendly
    {row_label: {date_str: value}} dict for fundamentals_cache (see core/db.py).
    Columns are stringified to "YYYY-MM-DD" (a bare Timestamp isn't JSON-serializable);
    cell values are cast to plain Python float (yfinance's are numpy.float64, also not
    JSON-serializable) with NaN normalized to None (a JSON null round-trips more
    portably than the non-standard "NaN" token Python's json module would otherwise
    emit). An empty DataFrame -- confirmed real for ETFs/funds, e.g. SPY's
    `income_stmt` -- returns {} rather than raising; the caller treats that as "no
    statements for this symbol," not a fetch failure."""
    if statement is None or statement.empty:
        return {}
    dated = statement.copy()
    dated.columns = [c.strftime("%Y-%m-%d") if hasattr(c, "strftime") else str(c) for c in dated.columns]
    raw = dated.to_dict(orient="index")
    return {
        label: {date: (None if pd.isna(value) else float(value)) for date, value in values.items()}
        for label, values in raw.items()
    }


def fetch_fundamentals(symbols: list[str], *, yf_module=None) -> pd.DataFrame:
    """Batch-fetches, per symbol, what the Company Fundamentals page needs: the
    annual income statement, balance sheet, and cash flow statement (each as a
    {row_label: {date_str: value}} dict via _statement_to_dict above -- confirmed
    empirically that `Ticker.income_stmt` carries 4 fiscal years of columns while
    `Ticker.balance_sheet`/`Ticker.cashflow` carry 5, so each statement keeps its own
    year range rather than being forced onto a shared one), plus the Analyst Target
    valuation trio (Current Price, Target Mean Price, Number Of Analysts) and the two
    currency fields needed to detect a statement/quote currency mismatch.

    `Currency` (`info["currency"]`, the quote's trading currency) vs `Financial
    Currency` (`info["financialCurrency"]`, what the statements are actually reported
    in) can differ for a foreign ADR -- confirmed empirically for TSM (USD quote,
    TWD statements). The page compares these two fields directly rather than this
    function flagging a boolean, so the check stays generic (any symbol with a
    mismatch is caught) instead of hardcoding TSM.

    `Target Mean Price`/`Number Of Analysts` are `None` -- not 0, not a failure --
    for a symbol yfinance has no analyst coverage for; confirmed empirically for an
    ETF (SPY), which also returns an entirely empty `income_stmt` (financial
    statements don't exist for a fund, not just "coverage is thin"). Both are real,
    valid outcomes the page renders as "No analyst coverage" / "No financial
    statements available," never confused with the row below failing outright.

    `Current Price` (`info["currentPrice"]`, falling back to `info["regularMarketPrice"]`
    the same way Monitor Stocks' profile fetch falls back across equivalent fields)
    is, by contrast, confirmed present even for a no-coverage ETF -- so it's used as
    this function's own failure signal, mirroring fetch_stock_profile()'s `Latest
    Price`: a genuine success always has a real Current Price (even when everything
    else is legitimately blank), while any exception -- or the rare case where even
    the price fields are missing -- produces `float("nan")` here and blank/empty
    everywhere else. core.calculations.apply_fundamentals_fallback() checks exactly
    this field the same way apply_market_profile_fallback() checks `Latest Price`.

    `yf_module` can be injected for testing (see tests/test_market_data.py) --
    production callers always omit it and get the real yfinance module."""
    yf_module = yf_module or _yfinance

    rows = []
    for symbol in symbols:
        try:
            ticker = yf_module.Ticker(symbol)
            info = ticker.info or {}
            current_price = info.get("currentPrice")
            if current_price is None:
                current_price = info.get("regularMarketPrice")
            if current_price is None:
                raise ValueError(f"No current price available for {symbol}")

            target_mean = info.get("targetMeanPrice")
            num_analysts = info.get("numberOfAnalystOpinions")

            rows.append({
                "Symbol": symbol,
                "Currency": info.get("currency"),
                "Financial Currency": info.get("financialCurrency"),
                "Current Price": float(current_price),
                "Target Mean Price": float(target_mean) if target_mean is not None else None,
                "Number Of Analysts": int(num_analysts) if num_analysts is not None else None,
                "Income Statement": _statement_to_dict(ticker.income_stmt),
                "Balance Sheet": _statement_to_dict(ticker.balance_sheet),
                "Cash Flow": _statement_to_dict(ticker.cashflow),
            })
        except Exception:
            rows.append({
                "Symbol": symbol,
                "Currency": None,
                "Financial Currency": None,
                "Current Price": float("nan"),
                "Target Mean Price": None,
                "Number Of Analysts": None,
                "Income Statement": {},
                "Balance Sheet": {},
                "Cash Flow": {},
            })

    if not rows:
        return pd.DataFrame({col: pd.Series([], dtype=dtype) for col, dtype in _FUNDAMENTALS_EMPTY_COLUMNS.items()})
    return pd.DataFrame(rows)
