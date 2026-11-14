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
read functions plus the derived-data writes documented per-tool below
(reference-line "passed" marking, reference-line auto-capture); as of V4.19,
two confirm-gated REAL-data writes: record_trades_batch/record_dividends_batch
-- a deliberate, narrow exception to let Rich record real trades/dividends from
a photo of a Dime! Activity screenshot, never a free pass (see each tool's own
docstring for the two-call confirm=False/confirm=True contract this depends on);
and as of V4.20, update_rebalance_plan/reset_rebalance_plan, the same
confirm-gated pattern applied to the Rebalance & Reallocate plan tables
(rebalance_plans/rebalance_plan_items) -- these still record no real trade or
dividend, only the plan (a reminder of what to buy), which is itself inert data.
As of V4.23, record_option_trades_batch -- the same confirm-gated pattern applied
to option_trades, for Rich to record real option trades from a Dime! trade-
confirmation screenshot or plain chat.
save_symbol_types remains categorically forbidden -- there is still no tool here
that touches it. See docs/ROADMAP.md V4.13 "Write access" decision, and its
V4.19/V4.20 sections for why each exception was made.
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
from core.dividend_entry import (  # noqa: E402
    describe_dividend_batch_preview, preview_dividend_batch, record_dividend_batch,
)
from core.fundamentals_capture import capture_fundamentals  # noqa: E402
from core.fundamentals_summary import (  # noqa: E402
    normalize_symbol, summarize_fundamentals, summarize_holdings_health,
)
from core.option_entry import (  # noqa: E402
    describe_option_batch_preview, preview_option_batch, record_option_batch,
)
from core.option_pl import compute_option_summary, resolved_events  # noqa: E402
from core.rebalance_entry import (  # noqa: E402
    apply_rebalance_edit, apply_rebalance_reset, describe_plan, get_plan_with_holdings,
    preview_rebalance_edit, preview_rebalance_reset,
)
from core.trade_entry import (  # noqa: E402
    describe_trade_batch_preview, preview_trade_batch, record_trade_batch,
)

mcp = MCPServer("Portfolio")

# Same xlsx + blending Dashboard's/Monitor Stocks' own dividend figures use
# (calculations.blended_dividends) -- "Dividends Received" is ACTUAL dividends
# paid out, a different, backward-looking figure from the "Expected Div/Yr"
# projection Monitor Stocks also shows.
DATA_FILE = os.path.join(ROOT, "data", "Offshore_Statements_2023-01_to_2026-09.xlsx")
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

    # V4.22 -- option_events feeds compute_investment_gain's option_realized term,
    # same "one formula, never two numbers that quietly disagree" property Dashboard's
    # own call shares.
    option_events = resolved_events(db.fetch_option_trades())
    result = calculations.compute_investment_gain(
        realized_events, unrealized, blended_income, start, end, option_realized=option_events,
    )
    statement_date = latest_month.strftime("%b %Y")
    return (
        f"Lifetime Total P/L: ${result['total']:,.2f} "
        f"(Realized ${result['realized']:,.2f} + Unrealized ${result['unrealized']:,.2f} as of the "
        f"{statement_date} statement + Dividends ${result['dividends']:,.2f} + "
        f"Interest ${result['interest']:,.2f} + Options ${result['options']:,.2f})."
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


@mcp.tool()
def get_company_fundamentals(symbol: str, refresh: bool = False) -> str:
    """Fundamentals for ONE stock, as shown on the web app's Company Fundamentals
    page: business profile, the Analyst Target valuation (Overvalued / Undervalued /
    Fair value), latest-fiscal-year Revenue, Net Income, Free Cash Flow and Total Debt
    with year-over-year change, the key ratios (gross margin, operating margin,
    return on equity, debt/equity, current ratio), and the Health rating (Healthy /
    Mixed / Weak, with each measure and what to watch). Use it for questions like "is KO
    overvalued?", "what are AAPL's key ratios?" or "is KO healthy?". `symbol` is a ticker such as KO and
    does not have to be a holding -- a ticker never looked up before is fetched live
    from Yahoo Finance and saved. ETFs and funds have no financial statements or
    analyst coverage, so only their profile comes back. The answer states the date of
    the data. Stored data can be days or weeks old: pass refresh=true when the user
    wants current numbers, which re-fetches live from Yahoo Finance and saves it.
    Writes only to the fundamentals cache, never to trades or dividends."""
    ticker = normalize_symbol(symbol)
    if ticker is None:
        return f"{symbol!r} doesn't look like a ticker symbol (expected letters and digits, e.g. KO or BRK.B)."

    cached = db.fetch_fundamentals_cache()
    match = cached[cached["Symbol"] == ticker]
    note = ""
    if match.empty or refresh:
        if capture_fundamentals(ticker):
            note = "Refreshed live just now." if not match.empty else "Not stored before -- looked up live just now and saved."
            cached = db.fetch_fundamentals_cache()
            match = cached[cached["Symbol"] == ticker]
        elif match.empty:
            return (
                f"Couldn't fetch fundamentals for {ticker} from Yahoo Finance (unknown symbol, or a temporary "
                "Yahoo problem). Nothing was saved."
            )
        else:
            note = "A live refresh failed just now (likely a temporary Yahoo Finance problem), so this is the last stored data."
    text = summarize_fundamentals(match.iloc[0])
    return f"{note}\n{text}" if note else text


@mcp.tool()
def get_holdings_health() -> str:
    """The Health rating of every stock currently held, at once -- what the web
    app's Monitor Stocks "Health" column shows. Groups the holdings Weak / Mixed /
    Healthy (a rule-of-thumb read of each company's annual statements: profit & cash
    flow, debt & risk, growth), gives the reason for every Weak and Mixed one, and
    names ETFs/funds (no statements, so not rated) and any held symbol not stored
    yet. Use it for "which of my stocks look weak?" or "how healthy is my portfolio?".
    For the full breakdown of one company use get_company_fundamentals. Read-only:
    it uses only data already stored (which can be weeks old -- the answer says the
    date range), fetches nothing live and writes nothing."""
    positions = calculations.compute_current_positions(db.fetch_trades())
    if positions.empty:
        return "No current holdings."
    return summarize_holdings_health(positions["Symbol"].tolist(), db.fetch_fundamentals_cache())


@mcp.tool()
def record_trades_batch(trades: list[dict], confirm: bool = False) -> str:
    """Records one or more REAL buy/sell trades to the live database -- the
    same write Record Trade on the web app performs, batched. ALWAYS call
    with confirm=False first: it checks every row against what's already
    recorded, tells you what's new vs. already-logged, and writes NOTHING.
    Show that preview to the user verbatim and wait for an explicit yes.
    Only then call again with the SAME `trades` list and confirm=True to
    actually write the new rows (already-recorded ones are skipped
    automatically either way).

    Each item in `trades`: {"trade_date": "YYYY-MM-DD", "side": "buy"|"sell",
    "symbol": str, "quantity": number, "price": number, and optionally
    commission_fee/vat/reserved_fee/fee_rebate/order_type/order_id -- leave
    these out if you don't have them, e.g. when reading a Dime! Activity
    screenshot, which shows no fee detail.} Never invent a value for a field
    you can't actually see -- leave it out or ask the user."""
    try:
        if not confirm:
            return describe_trade_batch_preview(preview_trade_batch(trades))

        result = record_trade_batch(trades)
        lines = [f"Recorded {len(result['recorded'])} trade(s)."]
        if result["skipped_already_recorded"]:
            lines.append(f"Skipped {len(result['skipped_already_recorded'])} already-recorded row(s).")
        if result["failed"]:
            lines.append(f"{len(result['failed'])} row(s) failed to save:")
            for f in result["failed"]:
                lines.append(f"  - {f['symbol']} {f['trade_date']}: {f['error']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't process this trade batch: {e}"


@mcp.tool()
def record_dividends_batch(dividends: list[dict], confirm: bool = False) -> str:
    """Records one or more REAL dividends/interest/capital distributions --
    the same write Record Dividend performs, batched. Same two-call pattern
    as record_trades_batch: confirm=False previews and writes nothing,
    confirm=True (same list) writes the new rows.

    Each item: {"trade_date": "YYYY-MM-DD", "entry_type": "Dividend"|
    "Interest"|"Capital Distribution", "gross_amount": number,
    "withholding_tax": number (0 if none), "symbol": str (omit only for
    Interest)}. On a Dime! Activity screenshot, a dividend is TWO adjacent
    list entries with the same timestamp -- "Dividend <SYM>" (gross_amount)
    and "Dividend Withholding Tax <SYM>" (its absolute value is
    withholding_tax) -- combine them into one item here, don't send them as
    two separate dividends."""
    try:
        if not confirm:
            return describe_dividend_batch_preview(preview_dividend_batch(dividends))

        result = record_dividend_batch(dividends)
        lines = [f"Recorded {len(result['recorded'])} dividend/interest entry(ies)."]
        if result["skipped_already_recorded"]:
            lines.append(f"Skipped {len(result['skipped_already_recorded'])} already-recorded row(s).")
        if result["failed"]:
            lines.append(f"{len(result['failed'])} row(s) failed to save:")
            for f in result["failed"]:
                lines.append(f"  - {f.get('symbol')} {f['trade_date']}: {f['error']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't process this dividend batch: {e}"


@mcp.tool()
def record_option_trades_batch(trades: list[dict], confirm: bool = False) -> str:
    """Records one or more REAL option trades to the live database -- same
    two-call confirm pattern as record_trades_batch. ALWAYS call with
    confirm=False first: it checks every row against what's already recorded
    (and against how many contracts are currently open for that symbol), tells
    you what's new vs. already-logged vs. an overclose warning, and writes
    NOTHING. Show that preview to the user verbatim and wait for an explicit
    yes, then call again with the SAME `trades` list and confirm=True to
    actually write the new rows.

    Each item in `trades`: {"trade_date": "YYYY-MM-DD", "underlying": str,
    "option_type": "Call"|"Put", "strike": number, "expiration_date":
    "YYYY-MM-DD", "side": "buy"|"sell", "position_effect": "open"|"close",
    "contracts": number, "price": number (per-contract premium), and
    optionally commission/vat/occ_fee/orf_fee/taf_fee/sec_fee -- leave these
    out if you don't have them, e.g. when reading a screenshot that doesn't
    show every fee line. `side` and `position_effect` are INDEPENDENT --
    never infer one from the other: selling to open (collecting premium
    up front, e.g. a covered call or cash-secured put) is side="sell",
    position_effect="open", not "close". Never invent a value for a field
    you can't actually see -- leave it out or ask the user."""
    try:
        if not confirm:
            return describe_option_batch_preview(preview_option_batch(trades))

        result = record_option_batch(trades)
        lines = [f"Recorded {len(result['recorded'])} option trade(s)."]
        if result["skipped_already_recorded"]:
            lines.append(f"Skipped {len(result['skipped_already_recorded'])} already-recorded row(s).")
        if result["failed"]:
            lines.append(f"{len(result['failed'])} row(s) failed to save:")
            for f in result["failed"]:
                lines.append(f"  - {f['symbol']} {f['trade_date']}: {f['error']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't process this option trade batch: {e}"


@mcp.tool()
def get_option_portfolio_status() -> str:
    """Options-only trading status: win rate, total realized $ P/L and
    aggregate % P/L across every closed/expired option round trip, and every
    still-open position with its expiration date (soonest first). This is
    NOT blended with stock/dividend P/L -- use get_lifetime_pl for the
    whole-portfolio figure, which already folds resolved option P/L into its
    own "Options" term. Use this for "how am I doing on options?" or "what
    options do I still have open?". Read-only."""
    option_trades = db.fetch_option_trades()
    if option_trades.empty:
        return "No option trades recorded yet."

    summary = compute_option_summary(option_trades)
    resolved_count = summary["win_count"] + summary["loss_count"]
    lines = []
    if resolved_count:
        lines.append(
            f"Win rate: {summary['win_rate_pct']:.1f}% ({summary['win_count']}W / {summary['loss_count']}L). "
            f"Total realized P/L: ${summary['total_realized_pl']:,.2f} ({summary['total_pl_pct']:+.1f}%)."
        )
    else:
        lines.append("No closed or expired option trades yet.")

    open_positions = sorted(summary["open"], key=lambda r: pd.Timestamp(r["expiration_date"]))
    if open_positions:
        today = pd.Timestamp.now().normalize()
        lines.append(f"{len(open_positions)} open position(s):")
        for r in open_positions:
            exp = pd.Timestamp(r["expiration_date"])
            days = (exp - today).days
            qty = (
                f"{r['contracts']:g} of {r['total_contracts']:g}"
                if r["contracts"] != r["total_contracts"] else f"{r['contracts']:g}"
            )
            lines.append(
                f"  - {r['underlying']} {r['strike']:g} {r['option_type']}, exp {exp.strftime('%Y-%m-%d')} "
                f"({days}d): {qty} contract(s)"
            )
    else:
        lines.append("No open positions.")

    if summary["errors"]:
        lines.append("Some rows couldn't be matched cleanly:")
        lines.extend(f"  - {e}" for e in summary["errors"])
    return "\n".join(lines)


@mcp.tool()
def get_option_status(
    underlying: str, strike: float = None, option_type: str = None, expiration_date: str = None,
) -> str:
    """Status of one or more specific option contracts: open/closed/expired,
    $ and % P/L. `underlying` is required (e.g. "MNST"). Narrow to one exact
    contract with `strike`, `option_type` ("Call"/"Put"), and/or
    `expiration_date` ("YYYY-MM-DD") whenever the user's own wording gives you
    these -- if only `underlying` is given and several contracts are on file
    for it, every matching contract is listed rather than guessing one.
    Read-only."""
    ticker = (underlying or "").strip().upper()
    if not ticker:
        return "Need at least an underlying ticker, e.g. 'MNST'."

    option_trades = db.fetch_option_trades()
    if option_trades.empty:
        return "No option trades recorded yet."

    rows = option_trades[option_trades["underlying"].str.upper() == ticker]
    if strike is not None:
        rows = rows[rows["strike"] == float(strike)]
    if option_type:
        rows = rows[rows["option_type"].str.lower() == option_type.strip().lower()]
    if expiration_date:
        rows = rows[rows["expiration_date"] == pd.Timestamp(expiration_date)]

    if rows.empty:
        return f"No option trades found for {ticker} matching those details."

    summary = compute_option_summary(rows)
    lines = []
    for sym in sorted(rows["symbol"].unique()):
        meta = rows[rows["symbol"] == sym].iloc[0]
        label = (
            f"{meta['underlying']} {meta['strike']:g} {meta['option_type']}, "
            f"exp {pd.Timestamp(meta['expiration_date']).strftime('%Y-%m-%d')}"
        )

        for r in summary["open"]:
            if r["symbol"] != sym:
                continue
            qty = (
                f"{r['contracts']:g} of {r['total_contracts']:g}"
                if r["contracts"] != r["total_contracts"] else f"{r['contracts']:g}"
            )
            lines.append(
                f"{label}: OPEN -- {qty} contract(s), opened {pd.Timestamp(r['opened']).strftime('%Y-%m-%d')}."
            )

        for r in summary["closed"]:
            if r["symbol"] != sym:
                continue
            lines.append(
                f"{label}: CLOSED -- {r['contracts']:g} contract(s), "
                f"opened {pd.Timestamp(r['opened']).strftime('%Y-%m-%d')} / "
                f"closed {pd.Timestamp(r['closed']).strftime('%Y-%m-%d')}, "
                f"P/L ${r['pl']:,.2f} ({r['pl_pct']:+.1f}%)."
            )

        for r in summary["expired"]:
            if r["symbol"] != sym:
                continue
            lines.append(
                f"{label}: EXPIRED -- {r['contracts']:g} contract(s), "
                f"opened {pd.Timestamp(r['opened']).strftime('%Y-%m-%d')}, "
                f"P/L ${r['pl']:,.2f} ({r['pl_pct']:+.1f}%)."
            )

    if summary["errors"]:
        lines.append("Some rows couldn't be matched cleanly:")
        lines.extend(f"  - {e}" for e in summary["errors"])

    return "\n".join(lines) if lines else f"No resolved or open status found for {ticker}."


@mcp.tool()
def get_rebalance_plan() -> str:
    """Shows the current Rebalance & Reallocate plan: the $ amount to invest,
    % allocated/remaining, each Dividend-classified holding's % Reinvest/
    Invest $/Bought status, and the resulting blended Div Contrib %/Expected
    Div/Mo before vs. after this allocation. Read-only -- auto-starts a blank
    plan if none is active yet (same as opening the page for the first
    time), but changes nothing else. Note: Div Contrib %/Expected Div/Mo
    match the web page; Total P/L-style figures are not reported here, since
    those need the offshore-statement workbook this tool doesn't load."""
    try:
        data = get_plan_with_holdings()
        if data["holdings"].empty:
            return "No Dividend-classified holdings currently held -- nothing to rebalance."
        return describe_plan(data["holdings"], data["plan"])
    except Exception as e:
        return f"Couldn't read the rebalance plan: {e}"


@mcp.tool()
def update_rebalance_plan(amount: float = None, items: list[dict] = None, confirm: bool = False) -> str:
    """Edits the Rebalance & Reallocate plan -- the $ amount and/or any
    symbol's % Reinvest / Bought? tick. Same two-call pattern as
    record_trades_batch: ALWAYS call with confirm=False first -- it shows
    the plan as it is now and what it would become, and writes NOTHING.
    Show that to the user and wait for an explicit yes, then call again
    with the SAME amount/items and confirm=True to actually save it.

    `items`: list of {"symbol": str, "pct": number 0-100 (optional),
    "bought": bool (optional)} -- only symbols already in the current plan
    (i.e. currently-held Dividend-classified holdings) are valid; omit a
    field you don't want to change for that symbol. `amount` is the whole
    plan's $ amount to invest, omit to leave it unchanged. This does not buy
    anything or record a trade -- Bought? is a reminder only, same as on the
    web page; the real purchase still goes through record_trades_batch."""
    try:
        if not confirm:
            result = preview_rebalance_edit(amount, items or [])
            text = f"Current plan:\n{result['before']}\n\nIf you confirm, it would become:\n{result['after']}"
            if result["errors"]:
                text += "\n\nSome of this couldn't be understood:\n" + "\n".join(f"  - {e}" for e in result["errors"])
            text += "\n\nReply to confirm, then I'll call this again with confirm=true to actually save it."
            return text

        result = apply_rebalance_edit(amount, items or [])
        lines = [f"Saved. Updated: {', '.join(result['applied']) if result['applied'] else 'nothing (no changes)'}."]
        if result["plan_completed"]:
            lines.append("Every row was bought, so that plan is complete -- a fresh blank plan was started automatically.")
        if result["errors"]:
            lines.append("Some of this couldn't be applied:")
            lines.extend(f"  - {e}" for e in result["errors"])
        lines.append("")
        lines.append(result["after"])
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't update the rebalance plan: {e}"


@mcp.tool()
def reset_rebalance_plan(confirm: bool = False) -> str:
    """Abandons the current Rebalance & Reallocate plan (amount, every %
    Reinvest, every Bought? tick) and starts a fresh blank one -- the same
    as clicking Reset plan on the web page. confirm=False previews what
    would be abandoned and writes nothing; confirm=True actually resets
    it."""
    try:
        if not confirm:
            result = preview_rebalance_reset()
            return (
                f"This would abandon the current plan:\n{result['before']}\n\n"
                "Reply to confirm, then I'll call this again with confirm=true to actually reset it."
            )
        result = apply_rebalance_reset()
        return f"Plan reset. A fresh blank plan has started:\n{result['after']}"
    except Exception as e:
        return f"Couldn't reset the rebalance plan: {e}"


if __name__ == "__main__":
    mcp.run()
