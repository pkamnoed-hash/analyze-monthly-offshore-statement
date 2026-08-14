import pandas as pd
import streamlit as st

import cached_db
from core import db

ENTRY_TYPES = ["Dividend", "Interest", "Capital Distribution"]


def _blank_grid() -> pd.DataFrame:
    return pd.DataFrame([{
        "Date": pd.Timestamp.today().date(), "Symbol": None, "Entry Type": "Dividend",
        "Gross Amount": 0.0, "Withholding Tax": 0.0,
    }])


st.title("Record Dividend")
st.caption(
    "Enter one or more rows (e.g. several dividends posted the same day), then Save all rows. "
    "Gross Amount and Withholding Tax should match the two separate lines Dime! shows for each "
    "dividend -- Net is computed automatically. Leave Withholding Tax at 0 when there's no separate "
    "tax line (e.g. Capital Distribution). Leave Symbol blank for Interest."
)

if "dividend_grid" not in st.session_state:
    st.session_state["dividend_grid"] = _blank_grid()
if "dividend_grid_key" not in st.session_state:
    st.session_state["dividend_grid_key"] = 0

# Sourced from trade history (not past dividends) -- a dividend can't happen before the
# stock was bought, so this covers virtually every real case, including a symbol's very
# first dividend. Only gap: the buy itself hasn't been logged in Record Trade yet.
known_symbols = sorted(cached_db.cached_fetch_trades()["Symbol"].dropna().unique().tolist())

edited = st.data_editor(
    st.session_state["dividend_grid"],
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Date": st.column_config.DateColumn("Date", required=True, default=pd.Timestamp.today().date()),
        "Symbol": st.column_config.SelectboxColumn(
            "Symbol", options=known_symbols,
            help="Pick from symbols you've traded. Leave blank for Interest. "
                 "If a symbol is missing, record its trade in Record Trade first.",
        ),
        "Entry Type": st.column_config.SelectboxColumn("Entry Type", options=ENTRY_TYPES, required=True, default="Dividend"),
        "Gross Amount": st.column_config.NumberColumn(
            "Gross Amount", format="%.2f", required=True, default=0.0,
            help="The dividend/distribution amount before tax, as shown in Dime!",
        ),
        "Withholding Tax": st.column_config.NumberColumn(
            "Withholding Tax", format="%.2f", default=0.0,
            help="The separate 'Dividend Withholding Tax' line Dime! shows, if any. Leave at 0 if there isn't one.",
        ),
    },
    # Keyed with a counter (not just a fixed name) so a post-save reset always starts
    # a genuinely fresh widget -- reassigning the seed DataFrame alone isn't reliably
    # enough to clear data_editor's own internal added/edited/deleted row tracking
    # tied to an unchanged key.
    key=f"dividend_editor_{st.session_state['dividend_grid_key']}",
)

if st.button("Save all rows"):
    rows = []
    summary_lines = []
    errors = []
    for idx, r in edited.iterrows():
        if pd.isna(r["Date"]) or pd.isna(r["Gross Amount"]) or float(r["Gross Amount"]) == 0 or not r["Entry Type"]:
            continue
        symbol = (r["Symbol"] or "").strip().upper() or None
        entry_type = r["Entry Type"]
        # Only Interest is symbol-less in the real Dime! data (matches the xlsx seed
        # convention) -- a Dividend/Capital Distribution with no symbol is a data-entry
        # mistake, not a valid row, so block the whole save rather than silently
        # skipping it or saving it as-is.
        if entry_type != "Interest" and not symbol:
            errors.append(f"Row {idx + 1}: Symbol is required for {entry_type} (only Interest can be left blank).")
            continue
        gross = float(r["Gross Amount"])
        withholding = float(r["Withholding Tax"]) if pd.notna(r["Withholding Tax"]) else 0.0
        # round() avoids float noise (e.g. 2.45 - 0.36 == 2.0900000000000003 in binary
        # floating point) leaking into stored data and later sums.
        net = round(gross - withholding, 2)
        rows.append({
            "trade_date": pd.Timestamp(r["Date"]).strftime("%Y-%m-%d"),
            "symbol": symbol,
            "entry_type": entry_type,
            "net_amount": net,
            "source": "manual",
        })
        # \$ escapes -- multiple bare $ across these joined lines pair up as Streamlit's
        # inline-math delimiter (worse across several rows, since st.success joins them all
        # into one markdown block), mangling the text into broken math-mode spans.
        summary_lines.append(f"- {symbol or '(Interest)'}: \\${gross:,.2f} − \\${withholding:,.2f} = **\\${net:,.2f}**")

    if errors:
        for e in errors:
            st.error(e)
    elif not rows:
        st.warning("No complete rows to save -- fill in Date, Entry Type, and a non-zero Gross Amount.")
    else:
        db.insert_dividends_bulk(rows)
        cached_db.invalidate_dividends()
        st.session_state["dividend_grid"] = _blank_grid()
        st.session_state["dividend_grid_key"] += 1
        st.success(f"Saved {len(rows)} row(s):\n" + "\n".join(summary_lines))
        st.rerun()

st.divider()

view = st.radio("View", ["Recent list", "Matrix"], horizontal=True, label_visibility="collapsed")

db_dividends = cached_db.cached_fetch_dividends()

if view == "Recent list":
    # Seed rows (imported xlsx history) are deliberately excluded here, same convention
    # as Record Trade's Recent Trades -- this list is only for entries made through this
    # page, and it's the only one with a delete button, so seed data stays protected.
    recent = db_dividends[db_dividends["source"] == "manual"].sort_values("Trade Date", ascending=False).head(20)
    st.caption("Dividends logged through this page. To fix a mistake, delete the row and re-enter it.")
    if recent.empty:
        st.caption("No manually-logged dividends yet.")
    else:
        header_cols = st.columns([1.2, 0.9, 1.3, 1, 0.8])
        for col, label in zip(header_cols, ["Date", "Symbol", "Entry Type", "Net Amount", ""]):
            col.markdown(f"**{label}**")
        for _, row in recent.iterrows():
            c1, c2, c3, c4, c5 = st.columns([1.2, 0.9, 1.3, 1, 0.8])
            c1.write(row["Trade Date"].strftime("%Y-%m-%d"))
            c2.write(row["Symbol"] if pd.notna(row["Symbol"]) else "—")
            c3.write(row["Entry Type"])
            c4.write(f"${row['Net Amt']:,.2f}")
            with c5.popover("Delete"):
                symbol_label = row["Symbol"] if pd.notna(row["Symbol"]) else row["Entry Type"]
                st.write(f"Delete this {row['Entry Type']} of ${row['Net Amt']:,.2f} for {symbol_label} on {row['Trade Date'].strftime('%Y-%m-%d')}?")
                if st.button("Yes, delete", key=f"confirm_delete_dividend_{row['id']}"):
                    db.delete_dividend(row["id"])
                    cached_db.invalidate_dividends()
                    st.rerun()
else:
    if db_dividends.empty:
        st.caption("No dividends logged yet.")
    else:
        matrix_source = db_dividends.copy()
        # Interest rows carry no symbol (matches the xlsx seed convention) -- give them a
        # readable row label instead of dropping them from the matrix or showing a blank row.
        matrix_source["Symbol"] = matrix_source["Symbol"].fillna("(Interest)")
        matrix_source["Month"] = matrix_source["Trade Date"].dt.to_period("M").dt.strftime("%Y-%m")
        pivot = pd.pivot_table(
            matrix_source, index="Symbol", columns="Month", values="Net Amt",
            aggfunc="sum", fill_value=0.0, margins=True, margins_name="Total",
        )
        st.dataframe(pivot.style.format("${:,.2f}"), use_container_width=True)
