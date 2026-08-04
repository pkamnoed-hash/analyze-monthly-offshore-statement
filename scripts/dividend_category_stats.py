"""One-off, read-only script for the v4.1 scoping discussion: computes
dividends-received (all-time, split by Allocation Type) and a Dividend-
category ROI figure, using the exact same core/ functions the app itself
uses -- no calculation logic duplicated here, no writes anywhere.

Loads .streamlit/secrets.toml itself to connect (same env-var bridge
dashboard_app.py uses) but only ever prints the 4 final result numbers --
never the URL/token or any raw exception text that could echo one.
Whichever database secrets.toml currently points at is what gets read.
"""

import os
import sys
import tomllib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd  # noqa: E402

from core import calculations, db, rebalance  # noqa: E402

DATA_FILE = os.path.join(PROJECT_ROOT, "data", "Offshore_Statements_2023-01_to_2026-06.xlsx")
SECRETS_FILE = os.path.join(PROJECT_ROOT, ".streamlit", "secrets.toml")
DIVIDEND_ENTRY_TYPES = ["Dividends", "Div. Adj(NRA Withheld)", "Dividend", "Capital Distribution"]
AS_OF_DATE = pd.Timestamp("2024-04-04")
WINDOW_START_DATE = pd.Timestamp("2024-10-01")


def _load_secrets():
    with open(SECRETS_FILE, "rb") as f:
        secrets = tomllib.load(f)
    os.environ.setdefault("TURSO_DATABASE_URL", secrets["TURSO_DATABASE_URL"])
    os.environ.setdefault("TURSO_AUTH_TOKEN", secrets["TURSO_AUTH_TOKEN"])


def main():
    _load_secrets()

    xls = pd.ExcelFile(DATA_FILE)
    summary = pd.read_excel(xls, "Summary")
    transactions = pd.read_excel(xls, "Transactions")
    income = pd.read_excel(xls, "Income")

    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    for frame in (transactions, income):
        frame["Month"] = pd.to_datetime(frame["Month"], format="%Y-%m")
        frame["Trade Date"] = pd.to_datetime(frame["Trade Date"], format="%m/%d/%Y", errors="coerce")

    cutoff = summary["Month"].max() + pd.offsets.MonthEnd(0)

    db_trades = db.fetch_trades()
    db_dividends = db.fetch_dividends()
    symbol_types = db.fetch_symbol_types()
    type_map = dict(zip(symbol_types["Symbol"], symbol_types["Allocation Type"]))

    # --- Dividends received, all-time, blended xlsx (<=cutoff) + live db (>cutoff) ---
    blended_income = calculations.blended_dividends(income, db_dividends, cutoff)
    div_rows = blended_income[blended_income["Entry Type"].isin(DIVIDEND_ENTRY_TYPES) & blended_income["Symbol"].notna()]
    dividends_by_symbol = div_rows.groupby("Symbol")["Net Amt"].sum()

    total_dividends_all = dividends_by_symbol.sum()
    total_dividends_dividend_cat = sum(
        v for sym, v in dividends_by_symbol.items() if type_map.get(sym, "Others") == "Dividend"
    )
    total_dividends_growth_cat = sum(
        v for sym, v in dividends_by_symbol.items() if type_map.get(sym, "Others") == "Growth"
    )

    # --- Realized P/L, all-time, blended xlsx + live, Dividend-category symbols only ---
    xlsx_realized = calculations.compute_realized_pl(transactions)
    realized_events = calculations.blended_realized_pl(xlsx_realized, db_trades, cutoff)
    dividend_symbols = {sym for sym, t in type_map.items() if t == "Dividend"}
    realized_dividend_cat = realized_events[realized_events["Symbol"].isin(dividend_symbols)]["Realized P/L"].sum()

    # --- Currently-held Dividend-category positions: Unrealized $ + Cost Basis ---
    dividend_holdings = rebalance.get_dividend_holdings()
    unrealized_dividend_cat = dividend_holdings["Current Unrealized $"].sum()
    cost_basis_dividend_cat = dividend_holdings["Cost Basis"].sum()

    roi_dividend_cat = (
        (realized_dividend_cat + unrealized_dividend_cat + total_dividends_dividend_cat)
        / cost_basis_dividend_cat * 100
        if cost_basis_dividend_cat else float("nan")
    )
    # Income-only variant of #4: dividends received / cost basis, no price gain/loss --
    # i.e. "yield on cost" for the Dividend category, not total return.
    div_yield_on_cost = (
        total_dividends_dividend_cat / cost_basis_dividend_cat * 100
        if cost_basis_dividend_cat else float("nan")
    )

    print("=== v4.1 scoping numbers (read-only, no writes) ===")
    print(f"1) All dividends received, all-time (all categories): ${total_dividends_all:,.2f}")
    print(f"2) Dividends received, Dividend category only:        ${total_dividends_dividend_cat:,.2f}")
    print(f"3) Dividends received, Growth category only:          ${total_dividends_growth_cat:,.2f}")
    print(f"4) ROI on Dividend category:                          {roi_dividend_cat:.2f}%")
    print(
        f"   components -- realized P/L: ${realized_dividend_cat:,.2f}, "
        f"unrealized P/L (current holdings): ${unrealized_dividend_cat:,.2f}, "
        f"dividends: ${total_dividends_dividend_cat:,.2f}, "
        f"cost basis (current holdings): ${cost_basis_dividend_cat:,.2f}"
    )
    print(f"5) Dividend yield on cost (Dividend category, dividends / cost basis only): {div_yield_on_cost:.2f}%")

    # --- Same yield-on-cost, but reconstructed as of a past date instead of today ---
    # Cost basis: FIFO-replayed from only the trades that happened on/before AS_OF_DATE
    # (compute_current_positions on a date-filtered trade set = positions as of that date).
    # Dividends: same blended_income rows, filtered to Trade Date <= AS_OF_DATE.
    positions_as_of = calculations.compute_current_positions(db_trades[db_trades["Trade Date"] <= AS_OF_DATE])
    cost_basis_as_of_dividend_cat = positions_as_of[
        positions_as_of["Symbol"].map(type_map).fillna("Others") == "Dividend"
    ]["Cost Basis"].sum()

    div_rows_as_of = div_rows[div_rows["Trade Date"] <= AS_OF_DATE]
    dividends_as_of_by_symbol = div_rows_as_of.groupby("Symbol")["Net Amt"].sum()
    dividends_as_of_dividend_cat = sum(
        v for sym, v in dividends_as_of_by_symbol.items() if type_map.get(sym, "Others") == "Dividend"
    )

    yield_on_cost_as_of = (
        dividends_as_of_dividend_cat / cost_basis_as_of_dividend_cat * 100
        if cost_basis_as_of_dividend_cat else float("nan")
    )

    print(f"6) Same, as of {AS_OF_DATE.strftime('%Y-%m-%d')}: {yield_on_cost_as_of:.2f}%")
    print(
        f"   components -- dividends received by then: ${dividends_as_of_dividend_cat:,.2f}, "
        f"cost basis of Dividend-category positions held then: ${cost_basis_as_of_dividend_cat:,.2f}"
    )

    # --- Windowed yield: only dividends received SINCE WINDOW_START_DATE (not cumulative
    # since inception), against TODAY's cost basis -- then annualized by the actual elapsed
    # span, not assumed to be exactly one year. This is what #5's "16.09% since inception"
    # divided by ~3.5 years was crudely approximating -- this is the real, direct version.
    today = pd.Timestamp.today().normalize()
    div_rows_window = div_rows[div_rows["Trade Date"] >= WINDOW_START_DATE]
    dividends_window_by_symbol = div_rows_window.groupby("Symbol")["Net Amt"].sum()
    dividends_window_dividend_cat = sum(
        v for sym, v in dividends_window_by_symbol.items() if type_map.get(sym, "Others") == "Dividend"
    )
    yield_window = (
        dividends_window_dividend_cat / cost_basis_dividend_cat * 100
        if cost_basis_dividend_cat else float("nan")
    )
    window_years = (today - WINDOW_START_DATE).days / 365.25
    yield_window_annualized = yield_window / window_years if window_years else float("nan")

    print(
        f"7) Dividend yield on (today's) cost, dividends received since "
        f"{WINDOW_START_DATE.strftime('%Y-%m-%d')} only: {yield_window:.2f}% "
        f"over {window_years:.2f} years -> {yield_window_annualized:.2f}%/year annualized"
    )
    print(
        f"   components -- dividends received in window: ${dividends_window_dividend_cat:,.2f}, "
        f"cost basis (current holdings): ${cost_basis_dividend_cat:,.2f}"
    )


if __name__ == "__main__":
    main()
