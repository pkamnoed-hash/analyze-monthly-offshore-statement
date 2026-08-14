import os

import pandas as pd
import streamlit as st

import cached_db
from core import db, reconciliation

DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "Offshore_Statements_2023-01_to_2026-06.xlsx"
)

st.title("Reconciliation")

cutoff, xlsx_transactions, xlsx_income = reconciliation.load_xlsx_for_reconciliation(DATA_FILE)
st.caption(
    f"Confirms live-logged trades/dividends dated on or before **{cutoff.strftime('%b %d, %Y')}** "
    "(the last officially processed statement) against the audited xlsx. Once confirmed, a row "
    "is marked reconciled here and won't be checked again."
)

trade_candidates = db.fetch_unreconciled_trades(cutoff)
dividend_candidates = db.fetch_unreconciled_dividends(cutoff)

matched_trades = reconciliation.match_trades(trade_candidates, xlsx_transactions)
matched_dividends = reconciliation.match_dividends(dividend_candidates, xlsx_income)

ready_trades = matched_trades[matched_trades["matched"]].assign(_table="trades")
ready_dividends = matched_dividends[matched_dividends["matched"]].assign(_table="dividends")
ready = pd.concat([ready_trades, ready_dividends], ignore_index=True)
if not ready.empty:
    ready["_month_str"] = ready["xlsx_month"].dt.strftime("%Y-%m")


def _mark_reconciled(rows: pd.DataFrame):
    """Groups by (table, statement month) since mark_reconciled_bulk() takes
    one table and one month per call -- a "Mark all" spanning the ~900-row
    first-run backlog can cover dozens of distinct months at once."""
    touched_tables = set()
    for (table, month), group in rows.groupby(["_table", "_month_str"]):
        db.mark_reconciled_bulk(table, group["id"].tolist(), month)
        touched_tables.add(table)
    # v4.5 -- mark_reconciled_bulk() updates trades/dividends rows directly, so the
    # cached reads need invalidating same as any other write to those tables.
    if "trades" in touched_tables:
        cached_db.invalidate_trades()
    if "dividends" in touched_tables:
        cached_db.invalidate_dividends()


st.header("Ready to confirm")
st.caption("Live-logged rows that were found in the xlsx exactly as entered.")

c1, c2, c3 = st.columns(3)
c1.metric("Matched trades", len(ready_trades))
c2.metric("Matched dividends/interest", len(ready_dividends))
c3.metric("Total ready to confirm", len(ready))

if ready.empty:
    st.caption("Nothing to confirm right now.")
else:
    months = sorted(ready["_month_str"].unique())
    with st.popover(f"Mark all {len(ready)} as reconciled"):
        st.write(f"Mark all {len(ready)} matched rows across {len(months)} statement month(s) as reconciled?")
        if st.button("Yes, mark all as reconciled", key="confirm_mark_all_reconciled"):
            _mark_reconciled(ready)
            st.rerun()

    for month in months:
        month_df = ready[ready["_month_str"] == month]
        month_trades = month_df[month_df["_table"] == "trades"]
        month_dividends = month_df[month_df["_table"] == "dividends"]
        with st.expander(f"{month} -- {len(month_df)} row(s)"):
            if not month_trades.empty:
                st.caption(f"Trades ({len(month_trades)})")
                display = month_trades[["Trade Date", "Symbol", "Side", "Quantity", "Price", "Amount"]].copy()
                display["Trade Date"] = display["Trade Date"].dt.strftime("%Y-%m-%d")
                st.dataframe(display, use_container_width=True, hide_index=True)
            if not month_dividends.empty:
                st.caption(f"Dividends/Interest ({len(month_dividends)})")
                display = month_dividends[["Trade Date", "Symbol", "Entry Type", "Net Amt"]].copy()
                display["Trade Date"] = display["Trade Date"].dt.strftime("%Y-%m-%d")
                st.dataframe(display, use_container_width=True, hide_index=True)
            if st.button(f"Mark {len(month_df)} row(s) in {month} as reconciled", key=f"mark_month_{month}"):
                _mark_reconciled(month_df)
                st.rerun()

st.divider()
st.header("Needs review")
st.caption(
    "Live-logged rows that don't match anything in the xlsx -- likely a data-entry mistake. "
    "No edit-in-place: fix by deleting the row and re-entering it correctly in Record Trade / "
    "Record Dividend, the same correction pattern used everywhere else in this app."
)

unmatched_trades = matched_trades[~matched_trades["matched"]]
unmatched_dividends = matched_dividends[~matched_dividends["matched"]]

if unmatched_trades.empty and unmatched_dividends.empty:
    st.caption("Nothing flagged -- every candidate matched.")
else:
    if not unmatched_trades.empty:
        st.subheader(f"Trades ({len(unmatched_trades)})")
        display = unmatched_trades[["Trade Date", "Symbol", "Side", "Quantity", "Price", "Amount", "reason"]].copy()
        display["Trade Date"] = display["Trade Date"].dt.strftime("%Y-%m-%d")
        display = display.rename(columns={"reason": "Reason"})
        st.dataframe(display, use_container_width=True, hide_index=True)
    if not unmatched_dividends.empty:
        st.subheader(f"Dividends/Interest ({len(unmatched_dividends)})")
        display = unmatched_dividends[["Trade Date", "Symbol", "Entry Type", "Net Amt", "reason"]].copy()
        display["Trade Date"] = display["Trade Date"].dt.strftime("%Y-%m-%d")
        display = display.rename(columns={"reason": "Reason"})
        st.dataframe(display, use_container_width=True, hide_index=True)

st.divider()
st.header("Official activity not yet logged")
st.caption(
    "xlsx rows with no SQLite counterpart at all -- activity that was never entered into this "
    "app. Fix by logging it fresh in Record Trade / Record Dividend."
)

scan_full_history = st.checkbox("Scan full history (all months, not just the newest statement)")
# Defaults to the newest statement month only, for speed -- checked against the FULL trades/
# dividends tables (not just unreconciled candidates), so a row a prior run already reconciled
# is correctly recognized as covered rather than looking like a fresh gap.
since = None if scan_full_history else pd.Timestamp(cutoff.year, cutoff.month, 1)

all_trades = cached_db.cached_fetch_trades()
all_dividends = cached_db.cached_fetch_dividends()
gap_trades = reconciliation.unmatched_xlsx_trades(all_trades, xlsx_transactions, since=since)
gap_income = reconciliation.unmatched_xlsx_income(all_dividends, xlsx_income, since=since)

if gap_trades.empty and gap_income.empty:
    scope = "in the newest statement month" if not scan_full_history else "in the scanned history"
    st.caption(f"No gaps found {scope}.")
else:
    if not gap_trades.empty:
        st.subheader(f"Trades ({len(gap_trades)})")
        display = gap_trades[["Trade Date", "Symbol", "Side", "Quantity", "Price", "Amount", "Description"]].copy()
        display["Trade Date"] = display["Trade Date"].dt.strftime("%Y-%m-%d")
        st.dataframe(display, use_container_width=True, hide_index=True)
    if not gap_income.empty:
        st.subheader(f"Dividends/Interest ({len(gap_income)})")
        display = gap_income.copy()
        display["Trade Date"] = display["Trade Date"].dt.strftime("%Y-%m-%d")
        st.dataframe(display, use_container_width=True, hide_index=True)
