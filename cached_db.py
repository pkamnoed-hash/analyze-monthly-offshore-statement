"""Streamlit-cached wrappers around core.db's trades/dividends/symbol_types reads,
plus matching invalidate functions. Lives here (project root, not core/) since it
needs a Streamlit import, which core/ deliberately avoids (see CLAUDE.md) -- and not
in app_pages/ since it's shared infrastructure, not a page.

v4.5 -- these three reads were previously called uncached on every single page load
(app_pages/dashboard.py's own old comment: "a trade just recorded should show up
immediately"), meaning every navigation/rerun paid a real Turso round trip per read,
even when nothing had changed -- one of three real causes behind the app's "always
reloads" feeling (see docs/ROADMAP.md V4.5). Cached here with no ttl (held for the
life of the session) and invalidated explicitly by whichever write actually changed
that table, same pattern this app already uses for yfinance's "Refresh now" button
(_cached_fetch_stock_profile.clear() in app_pages/monitor_stocks.py) -- so a save is
never stale, not even briefly, while plain navigation stops paying for a redundant
fetch.
"""
import pandas as pd
import streamlit as st

from core import calculations, db, market_data


@st.cache_data
def cached_fetch_trades():
    return db.fetch_trades()


@st.cache_data
def cached_fetch_dividends():
    return db.fetch_dividends()


@st.cache_data
def cached_fetch_symbol_types():
    return db.fetch_symbol_types()


@st.cache_data
def cached_fetch_target_categories():
    return db.fetch_target_categories()


@st.cache_data
def cached_fetch_target_sectors():
    return db.fetch_target_sectors()


@st.cache_data
def cached_fetch_target_allocations():
    return db.fetch_target_allocations()


def invalidate_trades():
    cached_fetch_trades.clear()


def invalidate_dividends():
    cached_fetch_dividends.clear()


def invalidate_symbol_types():
    cached_fetch_symbol_types.clear()


def invalidate_target_categories():
    cached_fetch_target_categories.clear()


def invalidate_target_sectors():
    cached_fetch_target_sectors.clear()


def invalidate_target_allocations():
    cached_fetch_target_allocations.clear()


@st.cache_data(ttl=300)
def reference_line_summary(symbols: list[str], latest_prices: dict) -> pd.DataFrame:
    """v4.4.1 -- Monitor Stocks' Reference Lines summary tab. Builds the "Nearest
    Resistance (R %)"/"Nearest Support (S %)" cell for every symbol WITHOUT requiring its
    own Auto Trendline page be visited first: any symbol with no captured
    `reference_lines` row yet gets auto-captured here (real price-history fetch +
    calculations.compute_reference_lines + db.save_reference_lines, same YTD/Daily basis
    the per-symbol page seeds a never-visited symbol with) -- the whole point of this tab.

    Lives here (not app_pages/monitor_stocks.py) rather than the page that displays it --
    v4.5.1 moved it here after a real bug: Auto Trendline's Regenerate/drag/delete/add
    freshly writes reference_lines (resetting passed_at, see db.save_reference_lines) but
    that page has no access to a summary tab cache defined on a DIFFERENT page module --
    Streamlit pages aren't meant to import each other (a page module's top-level code runs
    its own st.* calls on import). Living in this shared, page-agnostic module instead
    lets symbol_analysis.py call invalidate_reference_line_summary() right after a save,
    the same "cache + invalidate on write" pattern this module's trades/dividends/
    symbol_types wrappers already use.

    Own 5-minute TTL, cleared explicitly by invalidate_reference_line_summary() (called by
    Monitor Stocks' "Refresh now" button and by Auto Trendline's own save path) -- a cold
    run can mean dozens of real price-history fetches, but once a symbol is captured this
    only costs a cheap DB read on every subsequent call (auto-capture skips anything
    already present), so the TTL mainly governs how often the "passed" check re-evaluates
    against a fresh price, not repeated capture work. `latest_prices` is a plain
    {symbol: price} dict rather than the profile DataFrame -- st.cache_data hashes it
    directly. v4.5.1 -- since profile data (and so `Latest Price`) is now read DB-first
    via monitor_stocks.py's own _cached_read_stock_profile_from_db, this dict stays
    identical for the whole session between Refresh clicks (not just within a 5-minute
    window like before), so this function still cache-hits across reruns whenever its own
    5-minute TTL hasn't separately expired.

    db.mark_reference_lines_passed() runs every call (cheap -- reuses `latest_prices`
    already fetched for the page, no extra network cost) so a line crossed since the last
    cache refresh gets its passed_at set before this tab's cells are built.

    Returns "_R Passed At"/"_S Passed At" alongside the visible columns -- hidden helper
    columns (see the render loop in monitor_stocks.py) carrying each side's own passed_at,
    so the Nearest Resistance/Support cells can be highlighted individually rather than
    only the shared "Passed R/S" date column.

    Known, accepted edge case: a genuinely flat/ultra-low-volatility symbol (real example:
    SGOV/SHV, near-zero price movement) can have ZERO swing candidates on either side, so
    compute_reference_lines returns two empty lists and save_reference_lines's
    delete-then-insert-all inserts nothing -- that symbol never shows up in `captured`,
    so it's never seen as "already captured" and gets re-attempted (one more real
    fetch_price_history call) every cache cycle instead of being permanently skipped.
    Accepted rather than fixed with extra schema complexity (e.g. a sentinel row) -- in
    practice this is a couple of symbols out of a real ~52-symbol portfolio, re-fetched at
    most once per 5-minute TTL window, not on every rerun."""
    captured = db.fetch_reference_lines()
    captured_symbols = set(captured["Symbol"]) if not captured.empty else set()
    today = pd.Timestamp.today().normalize()
    cutoff = pd.Timestamp(year=today.year, month=1, day=1)  # YTD -- matches the per-symbol
                                                              # page's own default basis for
                                                              # a never-visited symbol.

    for symbol in symbols:
        if symbol in captured_symbols:
            continue
        latest_price = latest_prices.get(symbol)
        if latest_price is None or pd.isna(latest_price):
            continue  # unresolved symbol -- nothing to anchor a capture against
        daily_history = market_data.fetch_price_history(symbol, 1825)
        if daily_history.empty:
            continue
        resampled_full = calculations.resample_ohlc(daily_history, "D")
        bars_in_range = int((resampled_full["Date"] >= cutoff).sum())
        window = min(25, max(3, bars_in_range // 25))
        reflines = calculations.compute_reference_lines(
            resampled_full["Date"], resampled_full["High"], resampled_full["Low"],
            latest_price, window=window, search_from=cutoff,
        )
        lines_to_save = [{"price": p, "is_override": False} for p in reflines["resistance"] + reflines["support"]]
        db.save_reference_lines(
            symbol, lines_to_save, latest_price=latest_price,
            captured_timeline="YTD", captured_interval="Day",
        )

    db.mark_reference_lines_passed(latest_prices)

    captured = db.fetch_reference_lines()  # re-fetch: includes anything just captured/marked passed
    rows = []
    for symbol in symbols:
        latest_price = latest_prices.get(symbol)
        symbol_lines = captured[captured["Symbol"] == symbol] if not captured.empty else captured
        if latest_price is None or pd.isna(latest_price) or symbol_lines.empty:
            rows.append({
                "Symbol": symbol, "Nearest Resistance (R %)": "—", "Nearest Support (S %)": "—",
                "Passed R/S": pd.NaT, "_R Passed At": pd.NaT, "_S Passed At": pd.NaT,
            })
            continue
        line_dicts = [
            {"price": row["Price"], "passed_at": row["Passed At"]} for _, row in symbol_lines.iterrows()
        ]
        resistance_lines = [d for d, side in zip(line_dicts, symbol_lines["Captured Side"]) if side == "resistance"]
        support_lines = [d for d, side in zip(line_dicts, symbol_lines["Captured Side"]) if side == "support"]
        r_cell = calculations.nearest_reference_cell(resistance_lines, "resistance", latest_price)
        s_cell = calculations.nearest_reference_cell(support_lines, "support", latest_price)
        # A symbol could, in principle, have both its nearest R and nearest S passed at
        # once (price whipsawed through both since the last capture) -- "Passed R/S" is
        # one column, so this surfaces the MORE RECENT of the two dates, the fresher alert.
        passed_dates = [d for d in (r_cell["passed_at"], s_cell["passed_at"]) if d is not None]
        rows.append({
            "Symbol": symbol,
            "Nearest Resistance (R %)": r_cell["text"],
            "Nearest Support (S %)": s_cell["text"],
            "Passed R/S": max(passed_dates) if passed_dates else pd.NaT,
            # Hidden helper columns (never in a TAB_COLUMNS list, kept off-screen via
            # column_order=cols below) -- "Passed R/S" alone can't tell the highlighter
            # WHICH side passed, only the more recent date of whichever side(s) did. These
            # carry each side's own passed_at so _highlight_passed_nearest_reference can
            # light up exactly the cell that was actually reached.
            "_R Passed At": r_cell["passed_at"] if r_cell["passed_at"] is not None else pd.NaT,
            "_S Passed At": s_cell["passed_at"] if s_cell["passed_at"] is not None else pd.NaT,
        })
    return pd.DataFrame(rows)


def invalidate_reference_line_summary():
    reference_line_summary.clear()
