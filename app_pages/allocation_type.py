import streamlit as st

import cached_db
from core import db

st.title("Allocation Type")
st.caption(
    "Classifies each symbol you've ever traded as Dividend or Growth, matching how you "
    "already track two separate portfolios. Others is the default for anything not yet "
    "sorted -- this is a one-time tag per symbol, not per trade, and you can change it "
    "here anytime."
)

symbol_types = cached_db.cached_fetch_symbol_types()
unclassified = int((symbol_types["Allocation Type"] == "Others").sum())
st.metric("Symbols still in Others", f"{unclassified} of {len(symbol_types)}")

st.caption(
    "Check the box next to several symbols and use a bulk-set button below -- faster "
    "than editing the dropdown one row at a time. Bulk actions apply immediately; the "
    "dropdown column still needs the Save button for one-off changes."
)

type_filter = st.radio(
    "Filter by type", ["All", "Others", "Dividend", "Growth"], horizontal=True, label_visibility="collapsed",
)

grid_source = symbol_types.copy()
if type_filter != "All":
    grid_source = grid_source[grid_source["Allocation Type"] == type_filter]
st.caption(f"Showing {len(grid_source)} of {len(symbol_types)} symbols.")
grid_source.insert(0, "Select", False)

edited = st.data_editor(
    grid_source,
    use_container_width=True,
    hide_index=True,
    disabled=["Symbol"],
    column_config={
        "Select": st.column_config.CheckboxColumn("Select", default=False),
        "Allocation Type": st.column_config.SelectboxColumn(
            "Allocation Type", options=["Others", "Dividend", "Growth"], required=True,
        ),
    },
    key="allocation_type_editor",
)

selected = edited[edited["Select"]]


def _bulk_apply(symbols, allocation_type):
    for symbol in symbols:
        if allocation_type == "Others":
            db.clear_symbol_type(symbol)
        else:
            db.set_symbol_type(symbol, allocation_type)
    cached_db.invalidate_symbol_types()
    st.success(f"Set {len(symbols)} symbol(s) to {allocation_type}.")
    st.rerun()


bcol1, bcol2, bcol3 = st.columns(3)
with bcol1:
    if st.button(f"Set {len(selected)} selected to Dividend", disabled=selected.empty):
        _bulk_apply(selected["Symbol"].tolist(), "Dividend")
with bcol2:
    if st.button(f"Set {len(selected)} selected to Growth", disabled=selected.empty):
        _bulk_apply(selected["Symbol"].tolist(), "Growth")
with bcol3:
    if st.button(f"Set {len(selected)} selected to Others", disabled=selected.empty):
        _bulk_apply(selected["Symbol"].tolist(), "Others")

st.divider()

if st.button("Save dropdown changes"):
    previous = dict(zip(symbol_types["Symbol"], symbol_types["Allocation Type"]))
    changed = 0
    for _, row in edited.iterrows():
        symbol, new_type = row["Symbol"], row["Allocation Type"]
        if new_type == previous.get(symbol):
            continue
        changed += 1
        if new_type == "Others":
            db.clear_symbol_type(symbol)
        else:
            db.set_symbol_type(symbol, new_type)

    if changed:
        cached_db.invalidate_symbol_types()
        st.success(f"Updated {changed} symbol(s).")
        st.rerun()
    else:
        st.caption("No changes to save.")
