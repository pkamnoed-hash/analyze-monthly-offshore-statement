"""Hermes Agent MCP server for the "Rich" Telegram bot -- portfolio Q&A tools,
backed by the SAME core/db.py / core/calculations.py this Streamlit app already
uses (imported directly, not duplicated, so a future bugfix to either module is
picked up automatically on the VPS's next `git pull` -- see docs/ROADMAP.md's
V4.13 section for the full design).

Runs as a local stdio subprocess, spawned directly by Hermes per the `rich`
profile's own config.yaml (see mcp_server/README.md for the exact VPS wiring).
Deliberately NOT a Streamlit process -- core/db.py has zero Streamlit dependency
by design (see its own module docstring), so this script can import and call it
straight, with no `st.cache_data`/session-state machinery in the way.

Credentials: TURSO_DATABASE_URL / TURSO_AUTH_TOKEN, read from the process
environment via python-dotenv's load_dotenv() below (from mcp_server/.env on the
VPS -- see .env.example). Write access: this file's own code only ever calls
read functions plus the two specific derived-data writes documented per-tool
below (reference-line "passed" marking, reference-line auto-capture) -- never
save_trade/save_dividend/save_symbol_types. See docs/ROADMAP.md V4.13 "Write
access" decision for why.
"""

import os
import sys

# Hermes spawns this script by absolute path (see docstring above), so Python's
# default sys.path[0] is this file's own mcp_server/ directory, not the repo
# root -- `core` wouldn't be importable without this. Same pattern
# scripts/backup_symbol_types_before_migration.py already uses.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import pandas as pd  # noqa: E402
from mcp.server import MCPServer  # noqa: E402

from core import calculations, db, market_data  # noqa: E402

mcp = MCPServer("Portfolio")

# Same xlsx + blending Dashboard's/Monitor Stocks' own dividend figures use
# (calculations.blended_dividends) -- "Dividends Received" is ACTUAL dividends
# paid out, a different, backward-looking figure from the "Expected Div/Yr"
# projection Monitor Stocks also shows.
DATA_FILE = os.path.join(ROOT, "data", "Offshore_Statements_2023-01_to_2026-06.xlsx")
DIVIDEND_ENTRY_TYPES = ["Dividends", "Div. Adj(NRA Withheld)", "Dividend", "Capital Distribution"]


def _load_xlsx() -> dict:
    """Same load as dashboard.py's load_data() -- the official broker statement
    history every P/L tool below blends with live DB data via
    calculations.blended_realized_pl()/blended_dividends(). Loaded fresh per call
    (no caching layer here -- st.cache_data isn't available outside Streamlit, and
    this file is a handful of tool calls per Telegram question, not a page
    rerendering on every interaction, so the xlsx's small read cost doesn't
    justify adding one)."""
    xls = pd.ExcelFile(DATA_FILE)
    summary = pd.read_excel(xls, "Summary")
    holdings = pd.read_excel(xls, "Holdings")
    transactions = pd.read_excel(xls, "Transactions")
    income = pd.read_excel(xls, "Income")

    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    holdings["Month"] = pd.to_datetime(holdings["Month"], format="%Y-%m")
    holdings["Quantity"] = pd.to_numeric(holdings["Quantity"], errors="coerce")
    for frame in (transactions, income):
        frame["Month"] = pd.to_datetime(frame["Month"], format="%Y-%m")
        frame["Trade Date"] = pd.to_datetime(frame["Trade Date"], format="%m/%d/%Y", errors="coerce")

    cutoff = summary["Month"].max() + pd.offsets.MonthEnd(0)
    return {
        "summary": summary, "holdings": holdings, "transactions": transactions,
        "income": income, "cutoff": cutoff,
    }


def _dividends_received_by_symbol(xlsx: dict) -> pd.Series:
    """Actual dividends received per symbol, full history -- same blended
    (xlsx <= cutoff, live DB > cutoff) figure Monitor Stocks' own "Dividends
    Received" column uses (see app_pages/monitor_stocks.py's own
    _dividends_received_by_symbol)."""
    blended_income = calculations.blended_dividends(xlsx["income"], db.fetch_dividends(), xlsx["cutoff"])
    rows = blended_income[blended_income["Entry Type"].isin(DIVIDEND_ENTRY_TYPES) & blended_income["Symbol"].notna()]
    return rows.groupby("Symbol")["Net Amt"].sum()


@mcp.tool()
def get_holdings_count() -> str:
    """How many different stocks/ETFs/funds are currently held in the
    portfolio. A fully-exited (fully sold) symbol does not count -- only
    positions still open right now."""
    trades = db.fetch_trades()
    positions = calculations.compute_current_positions(trades)
    count = len(positions)
    return f"You currently hold {count} different symbol{'s' if count != 1 else ''}."


@mcp.tool()
def get_upcoming_ex_dates() -> str:
    """Which currently-held symbols have an Ex-Dividend Date falling in the
    current calendar month. Note: yfinance only ever reports a PAST Ex-Date
    (never a future one), so this means "already went ex-dividend this
    month," not an upcoming/future date to watch for."""
    positions = calculations.compute_current_positions(db.fetch_trades())
    if positions.empty:
        return "No current holdings."
    held = set(positions["Symbol"])
    profile = db.fetch_market_profile_cache()
    profile = profile[profile["Symbol"].isin(held)]
    matches = sorted(
        row["Symbol"] for _, row in profile.iterrows()
        if calculations.is_ex_date_this_month(row["Ex-Date"])
    )
    if not matches:
        return "No currently-held symbol has an Ex-Date in the current calendar month."
    return f"Ex-Date this month for: {', '.join(matches)}."


@mcp.tool()
def get_holdings_pl() -> str:
    """Current-holdings-only Total P/L: live Unrealized (current price minus
    cost basis, from real-time cached prices) plus actual Dividends Received,
    summed across every symbol still held today. Matches Monitor Stocks' own
    Total P/L exactly. Does NOT include realized gains already locked in from
    a symbol you've fully sold -- use get_lifetime_pl for that all-time,
    every-position figure instead."""
    positions = calculations.compute_current_positions(db.fetch_trades())
    if positions.empty:
        return "No current holdings."

    profile = db.fetch_market_profile_cache()
    price_map = dict(zip(profile["Symbol"], profile["Latest Price"]))
    positions = positions.copy()
    positions["Latest Price"] = positions["Symbol"].map(price_map)
    priced = positions[positions["Latest Price"].notna()].copy()
    if priced.empty:
        return "No cached price data available yet for any current holding."

    priced["Position Value"] = priced["Quantity"] * priced["Latest Price"]
    priced["Unrealized"] = priced["Position Value"] - priced["Cost Basis"]

    xlsx = _load_xlsx()
    div_by_symbol = _dividends_received_by_symbol(xlsx)
    priced["Dividends Received"] = priced["Symbol"].map(div_by_symbol).fillna(0.0)

    pl = calculations.compute_holdings_pl(priced["Unrealized"], priced["Dividends Received"], priced["Cost Basis"])
    total = pl["Total P/L"].sum()
    missing = len(positions) - len(priced)
    note = f" ({missing} holding{'s' if missing != 1 else ''} skipped, no cached price yet)" if missing else ""
    return f"Current-holdings Total P/L across {len(priced)} symbols: ${total:,.2f}{note}."


@mcp.tool()
def get_lifetime_pl() -> str:
    """All-time Total P/L across the whole portfolio's full history:
    Realized P/L (blended -- every trade ever, audited xlsx history plus live
    DB trades) + Unrealized (as of the LAST OFFICIAL BROKER STATEMENT, not
    live-priced -- see get_holdings_pl for a live figure) + Dividends +
    Interest, all-time. Matches Dashboard's own "Investment Gain/Loss" KPI
    exactly when its Duration filter is set to "All". Can lag real prices by
    up to ~1 month if a new statement hasn't been imported recently -- the
    response always names the statement date the Unrealized component is
    frozen as of, so this is never silently implied as live."""
    xlsx = _load_xlsx()
    db_trades = db.fetch_trades()
    db_dividends = db.fetch_dividends()

    realized_events = calculations.blended_realized_pl(
        calculations.compute_realized_pl(xlsx["transactions"]), db_trades, xlsx["cutoff"]
    )
    blended_income = calculations.blended_dividends(xlsx["income"], db_dividends, xlsx["cutoff"])

    latest_month = xlsx["holdings"]["Month"].max()
    latest_holdings = xlsx["holdings"][
        (xlsx["holdings"]["Month"] == latest_month) & (xlsx["holdings"]["Symbol"] != "*Cash")
    ]
    latest_holdings = latest_holdings[latest_holdings["Market Value"].notna()]
    unrealized = latest_holdings["Unrealized"].sum()

    # data_end mirrors dashboard.py's own "All" duration exactly -- extends past the
    # xlsx's last covered month if a live trade/dividend has been logged more
    # recently than the last official statement.
    latest_live_date = pd.concat([db_trades["Trade Date"], db_dividends["Trade Date"]]).max()
    data_end = max(xlsx["cutoff"], latest_live_date) if pd.notna(latest_live_date) else xlsx["cutoff"]
    start = xlsx["summary"]["Month"].min()
    end = pd.Timestamp(data_end.year, data_end.month, 1)

    result = calculations.compute_investment_gain(realized_events, unrealized, blended_income, start, end)
    statement_date = latest_month.strftime("%b %Y")
    return (
        f"Lifetime Total P/L: ${result['total']:,.2f} "
        f"(Realized ${result['realized']:,.2f} + Unrealized ${result['unrealized']:,.2f} as of the "
        f"{statement_date} statement + Dividends ${result['dividends']:,.2f} + "
        f"Interest ${result['interest']:,.2f})."
    )


def _fetch_current_prices(symbols: set[str]) -> dict:
    """Latest cached price per symbol, DB-first -- the same `latest_prices` shape
    Monitor Stocks' own page builds before calling reference_line_summary()."""
    profile = db.fetch_market_profile_cache()
    profile = profile[profile["Symbol"].isin(symbols)]
    return dict(zip(profile["Symbol"], profile["Latest Price"]))


@mcp.tool()
def get_reference_line_status() -> str:
    """Which currently-held symbols have PASSED their nearest support or
    resistance Reference Line -- i.e. price has crossed a previously-identified
    swing high/low. A symbol checked here for the first time gets its
    Reference Lines auto-captured (same YTD/Daily swing analysis Auto
    Trendline itself seeds a never-visited symbol with) -- this tool DOES
    write to the reference_lines table (new lines for a never-seen symbol,
    and a "passed_at" timestamp the moment price crosses an existing line),
    but never touches trades/dividends/symbol_types, matching what the
    Monitor Stocks page itself already does silently on every load."""
    positions = calculations.compute_current_positions(db.fetch_trades())
    if positions.empty:
        return "No current holdings."
    symbols = sorted(positions["Symbol"])
    latest_prices = _fetch_current_prices(set(symbols))

    # Auto-capture: mirrors cached_db.py's reference_line_summary() exactly,
    # just called directly instead of through its st.cache_data wrapper.
    captured = db.fetch_reference_lines()
    captured_symbols = set(captured["Symbol"]) if not captured.empty else set()
    today = pd.Timestamp.today().normalize()
    cutoff = pd.Timestamp(year=today.year, month=1, day=1)  # YTD, same default basis

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

    passed = []
    for symbol in symbols:
        latest_price = latest_prices.get(symbol)
        symbol_lines = captured[captured["Symbol"] == symbol] if not captured.empty else captured
        if latest_price is None or pd.isna(latest_price) or symbol_lines.empty:
            continue
        line_dicts = [{"price": row["Price"], "passed_at": row["Passed At"]} for _, row in symbol_lines.iterrows()]
        resistance_lines = [d for d, side in zip(line_dicts, symbol_lines["Captured Side"]) if side == "resistance"]
        support_lines = [d for d, side in zip(line_dicts, symbol_lines["Captured Side"]) if side == "support"]
        r_cell = calculations.nearest_reference_cell(resistance_lines, "resistance", latest_price)
        s_cell = calculations.nearest_reference_cell(support_lines, "support", latest_price)
        if r_cell["passed"]:
            passed.append(f"{symbol} passed resistance {r_cell['text']}")
        if s_cell["passed"]:
            passed.append(f"{symbol} passed support {s_cell['text']}")

    if not passed:
        return f"No currently-held symbol has passed its nearest support/resistance line right now ({len(symbols)} symbols checked)."
    return "; ".join(passed) + "."


if __name__ == "__main__":
    mcp.run()
