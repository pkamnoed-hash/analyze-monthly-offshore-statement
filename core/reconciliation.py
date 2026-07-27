"""Matches live-logged SQLite trades/dividends against the official xlsx
statement once the reconciliation cutoff advances -- confirms a live entry
was accurate, or flags it for manual review if it doesn't match anything.

Pure logic, no Streamlit import, no `conn` param (DataFrame-in/DataFrame-out
except the xlsx loader) -- kept separate from calculations.py since this is a
distinct concern (comparing two sources against each other, not computing
over one), matching this project's one-module-per-concern pattern.
"""

import pandas as pd

# xlsx Income vocabulary for a dividend -- mirrors scripts/seed_from_xlsx.py's
# DIVIDEND_ENTRY_TYPES exactly, since that's what actually produced the
# seeded SQLite rows being matched against here.
_XLSX_DIVIDEND_ENTRY_TYPES = ["Dividends", "Div. Adj(NRA Withheld)"]
_XLSX_INTEREST_ENTRY_TYPE = "Credit/Margin Interest"


def _empty_match_result(left):
    """Shared empty-input shortcut for the three matchers below -- avoids
    feeding an empty frame through groupby/merge (see core/db.py's DB_PATH
    postmortem: an empty result set silently losing its numeric dtypes has
    already caused a real merge failure once in this project)."""
    result = left.copy()
    result["matched"] = pd.Series(dtype=bool)
    result["xlsx_month"] = pd.Series(dtype="datetime64[ns]")
    result["reason"] = pd.Series(dtype=object)
    return result


def load_xlsx_for_reconciliation(path):
    """Loads Summary/Transactions/Income only, parses Trade Date the same way
    scripts/seed_from_xlsx.py does. Returns (cutoff, transactions, income).
    cutoff mirrors app_pages/dashboard.py's inline computation (Summary
    Month max + MonthEnd(0)) -- a second independent loader, same precedent
    as seed_from_xlsx.py's own loader; not imported from dashboard.py since
    that one is page-level Streamlit code reading sheets (Holdings/Fees/
    Deposits & Withdrawals) this doesn't need."""
    xls = pd.ExcelFile(path)
    summary = pd.read_excel(xls, "Summary")
    transactions = pd.read_excel(xls, "Transactions")
    income = pd.read_excel(xls, "Income")

    summary["Month"] = pd.to_datetime(summary["Month"], format="%Y-%m")
    transactions["Trade Date"] = pd.to_datetime(transactions["Trade Date"], format="%m/%d/%Y", errors="coerce")
    income["Trade Date"] = pd.to_datetime(income["Trade Date"], format="%m/%d/%Y", errors="coerce")

    cutoff = summary["Month"].max() + pd.offsets.MonthEnd(0)
    return cutoff, transactions, income


def _pair_1to1(left, right, key_cols):
    """Generic duplicate-safe 1:1 pairing: ranks duplicates within each key
    group on both sides (groupby(..., dropna=False).cumcount()) and folds
    that rank into the join key, so e.g. two rows sharing a key on one side
    only match if the other side also has two rows sharing that key --
    excess duplicates are correctly left unmatched rather than fanning out
    (a plain merge on key_cols alone would cross-join same-key duplicates).
    dropna=False so a NaN-valued key column (e.g. Price on a rights
    distribution) still gets a real per-row rank instead of every NaN-key
    row collapsing into one undifferentiated group.

    Left join: every `left` row survives with an indicator column showing
    whether an xlsx counterpart was found (`_merge` == 'both'/'left_only')."""
    left = left.copy()
    right = right.copy()
    left["_rank"] = left.groupby(key_cols, dropna=False).cumcount()
    right["_rank"] = right.groupby(key_cols, dropna=False).cumcount()
    merged = left.merge(right, on=key_cols + ["_rank"], how="left", suffixes=("", "_xlsx"), indicator=True)
    return merged.drop(columns="_rank")


def match_trades(sqlite_trades, xlsx_transactions):
    """One row per sqlite_trades input, +matched(bool)/xlsx_month/reason
    (populated when unmatched). Key: exact (Trade Date, Symbol,
    round(Quantity,6), round(Price,4)) -- rounded to match what Record
    Trade's number_input can actually produce (xlsx carries more decimal
    places than the UI accepts); no other fuzzy/tolerance matching. xlsx
    side is filtered to Symbol.notna() first (journal/cash rows don't
    belong here, matching build_trade_rows()'s own filter). A NaN Price
    (e.g. a rights-offering distribution) still matches correctly --
    pd.merge treats NaN as equal on join keys."""
    if sqlite_trades.empty:
        return _empty_match_result(sqlite_trades)

    left = sqlite_trades.copy()
    left["_qty_key"] = left["Quantity"].round(6)
    left["_price_key"] = left["Price"].round(4)

    right = xlsx_transactions[xlsx_transactions["Symbol"].notna()].copy()
    right["_qty_key"] = right["Quantity"].round(6)
    right["_price_key"] = right["Price"].round(4)
    right["xlsx_month"] = right["Trade Date"].dt.to_period("M").dt.to_timestamp()
    right = right[["Trade Date", "Symbol", "_qty_key", "_price_key", "xlsx_month"]]

    key_cols = ["Trade Date", "Symbol", "_qty_key", "_price_key"]
    paired = _pair_1to1(left, right, key_cols)

    paired["matched"] = paired["_merge"] == "both"
    paired["reason"] = paired["matched"].map({True: None, False: "No matching xlsx trade found"})
    return paired.drop(columns=["_qty_key", "_price_key", "_merge"])


def match_dividend_rows(sqlite_dividends, xlsx_income):
    """Non-Interest subset (SQLite Entry Type != 'Interest' -- covers both
    'Dividend' and 'Capital Distribution', since the xlsx has no separate
    vocabulary for the latter and it's matched the same way, on amount
    alone). xlsx Income rows are grouped by (Trade Date, Symbol), summing
    Net Amt over the Dividends + Div. Adj(NRA Withheld) rows -- every real
    xlsx dividend is exactly this pair (gross + negative NRA withholding),
    and that's exactly what produced the seeded SQLite net_amount, so the
    grouped sum is what has to match. Key: (Trade Date, Symbol,
    round(Net Amt, 2)) -- entry_type deliberately excluded from the key,
    since the xlsx/SQLite vocabularies never line up 1:1."""
    left = sqlite_dividends[sqlite_dividends["Entry Type"] != "Interest"].copy()
    if left.empty:
        return _empty_match_result(left)
    left["_amt_key"] = left["Net Amt"].round(2)

    div = xlsx_income[xlsx_income["Entry Type"].isin(_XLSX_DIVIDEND_ENTRY_TYPES) & xlsx_income["Symbol"].notna()].copy()
    grouped = div.groupby(["Trade Date", "Symbol"], as_index=False)["Net Amt"].sum()
    grouped["_amt_key"] = grouped["Net Amt"].round(2)
    grouped["xlsx_month"] = grouped["Trade Date"].dt.to_period("M").dt.to_timestamp()
    grouped = grouped[["Trade Date", "Symbol", "_amt_key", "xlsx_month"]]

    key_cols = ["Trade Date", "Symbol", "_amt_key"]
    paired = _pair_1to1(left, grouped, key_cols)
    paired["matched"] = paired["_merge"] == "both"
    paired["reason"] = paired["matched"].map({True: None, False: "No matching xlsx dividend found"})
    return paired.drop(columns=["_amt_key", "_merge"])


def match_interest_rows(sqlite_dividends, xlsx_income):
    """Interest subset only (SQLite Entry Type == 'Interest'). xlsx Symbol
    is ignored entirely -- scripts/seed_from_xlsx.py hardcodes symbol=None
    for every interest row regardless of what the xlsx actually shows, so
    matching against the real xlsx Symbol would never line up with
    seeded/live data. Key: (Trade Date, round(Net Amt, 2)) plus
    _pair_1to1's duplicate rank, since same-day multi-symbol interest
    postings are real (e.g. SGOV + SHV credited the same day) and must
    pair positionally rather than being grouped/summed together."""
    left = sqlite_dividends[sqlite_dividends["Entry Type"] == "Interest"].copy()
    if left.empty:
        return _empty_match_result(left)
    left["_amt_key"] = left["Net Amt"].round(2)

    right = xlsx_income[xlsx_income["Entry Type"] == _XLSX_INTEREST_ENTRY_TYPE].copy()
    right["_amt_key"] = right["Net Amt"].round(2)
    right["xlsx_month"] = right["Trade Date"].dt.to_period("M").dt.to_timestamp()
    right = right[["Trade Date", "_amt_key", "xlsx_month"]]

    key_cols = ["Trade Date", "_amt_key"]
    paired = _pair_1to1(left, right, key_cols)
    paired["matched"] = paired["_merge"] == "both"
    paired["reason"] = paired["matched"].map({True: None, False: "No matching xlsx interest found"})
    return paired.drop(columns=["_amt_key", "_merge"])


def match_dividends(sqlite_dividends, xlsx_income):
    """pd.concat([match_dividend_rows(...), match_interest_rows(...)]) --
    Entry Type is a strict two-way partition (Interest vs. everything
    else), so together these cover every input row exactly once."""
    return pd.concat(
        [match_dividend_rows(sqlite_dividends, xlsx_income), match_interest_rows(sqlite_dividends, xlsx_income)],
        ignore_index=True,
    )


def unmatched_xlsx_trades(sqlite_trades_all, xlsx_transactions, since=None):
    """xlsx Transactions rows (Symbol.notna()) with no SQLite counterpart at
    all -- official activity that was never logged live. Arguably the
    highest-value check in the whole feature: a trade like this has no
    other surface in the app that would ever mention it. Takes the FULL
    trades table (e.g. db.fetch_trades()), not just unreconciled
    candidates -- otherwise a row a prior reconciliation run already
    confirmed would look like a fresh gap here. `since` optionally scopes
    the xlsx side to Trade Date >= since, for speed once the backlog
    clears and only the newest statement month needs checking. Same
    key/rounding as match_trades(), just run in the reverse direction
    (xlsx driving, SQLite as the lookup side)."""
    xlsx = xlsx_transactions[xlsx_transactions["Symbol"].notna()].copy()
    if since is not None:
        xlsx = xlsx[xlsx["Trade Date"] >= since]
    if xlsx.empty:
        return xlsx.reset_index(drop=True)
    xlsx["_qty_key"] = xlsx["Quantity"].round(6)
    xlsx["_price_key"] = xlsx["Price"].round(4)

    sqlite = sqlite_trades_all.copy()
    sqlite["_qty_key"] = sqlite["Quantity"].round(6)
    sqlite["_price_key"] = sqlite["Price"].round(4)
    sqlite = sqlite[["Trade Date", "Symbol", "_qty_key", "_price_key"]]

    key_cols = ["Trade Date", "Symbol", "_qty_key", "_price_key"]
    paired = _pair_1to1(xlsx, sqlite, key_cols)
    gaps = paired[paired["_merge"] == "left_only"]
    return gaps.drop(columns=["_qty_key", "_price_key", "_merge"]).reset_index(drop=True)


def _unmatched_xlsx_dividend_rows(sqlite_dividends_all, xlsx_income, since=None):
    div = xlsx_income[xlsx_income["Entry Type"].isin(_XLSX_DIVIDEND_ENTRY_TYPES) & xlsx_income["Symbol"].notna()].copy()
    if since is not None:
        div = div[div["Trade Date"] >= since]
    grouped = div.groupby(["Trade Date", "Symbol"], as_index=False)["Net Amt"].sum()
    grouped["Entry Type"] = "Dividend"
    if grouped.empty:
        return grouped[["Trade Date", "Symbol", "Entry Type", "Net Amt"]]
    grouped["_amt_key"] = grouped["Net Amt"].round(2)

    sqlite = sqlite_dividends_all[sqlite_dividends_all["Entry Type"] != "Interest"].copy()
    sqlite["_amt_key"] = sqlite["Net Amt"].round(2)
    sqlite = sqlite[["Trade Date", "Symbol", "_amt_key"]]

    key_cols = ["Trade Date", "Symbol", "_amt_key"]
    paired = _pair_1to1(grouped, sqlite, key_cols)
    gaps = paired[paired["_merge"] == "left_only"]
    return gaps[["Trade Date", "Symbol", "Entry Type", "Net Amt"]].reset_index(drop=True)


def _unmatched_xlsx_interest_rows(sqlite_dividends_all, xlsx_income, since=None):
    interest = xlsx_income[xlsx_income["Entry Type"] == _XLSX_INTEREST_ENTRY_TYPE].copy()
    if since is not None:
        interest = interest[interest["Trade Date"] >= since]
    interest = interest.assign(**{"Entry Type": "Interest"})  # normalize to SQLite vocabulary for display
    if interest.empty:
        return interest[["Trade Date", "Symbol", "Entry Type", "Net Amt"]]
    interest["_amt_key"] = interest["Net Amt"].round(2)

    sqlite = sqlite_dividends_all[sqlite_dividends_all["Entry Type"] == "Interest"].copy()
    sqlite["_amt_key"] = sqlite["Net Amt"].round(2)
    sqlite = sqlite[["Trade Date", "_amt_key"]]

    key_cols = ["Trade Date", "_amt_key"]
    paired = _pair_1to1(interest, sqlite, key_cols)
    gaps = paired[paired["_merge"] == "left_only"]
    # Symbol here is the xlsx's real reported symbol (e.g. "SHV") -- informational only,
    # deliberately not part of the match key (see match_interest_rows' docstring).
    return gaps[["Trade Date", "Symbol", "Entry Type", "Net Amt"]].reset_index(drop=True)


def unmatched_xlsx_income(sqlite_dividends_all, xlsx_income, since=None):
    """xlsx Income rows (grouped dividends + interest) with no SQLite
    counterpart at all -- dividend/interest counterpart to
    unmatched_xlsx_trades(). Takes the FULL dividends table (e.g.
    db.fetch_dividends()), same already-reconciled caveat as that
    function. `since` optionally scopes the xlsx side to Trade Date >=
    since. Returns a uniform (Trade Date, Symbol, Entry Type, Net Amt)
    shape regardless of which of the two sub-checks a row came from."""
    return pd.concat(
        [
            _unmatched_xlsx_dividend_rows(sqlite_dividends_all, xlsx_income, since),
            _unmatched_xlsx_interest_rows(sqlite_dividends_all, xlsx_income, since),
        ],
        ignore_index=True,
    )
