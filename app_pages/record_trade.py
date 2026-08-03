import pandas as pd
import streamlit as st

from core import calculations, db, slip_parser


def render_trade_form(symbol, side, quantity, price, prefill=None, form_key="manual_trade", oversell_blocked=False):
    """Renders the remaining trade entry fields (Trade Date, fees, order
    info). Symbol/Side/Quantity/Price are captured outside this function
    (before it's called) so the live position lookup and sell-P/L preview
    can react to them -- st.form only reruns on submit, which would make
    that impossible if they were inside. Pre-filled if `prefill` is given
    (used by the Upload Slip confirm step; the caller is responsible for
    prefilling the outer Symbol/Side/Quantity/Price widgets too, since this
    function no longer renders them). `oversell_blocked` is computed by the
    caller from the shared position lookup -- True when this is a sell
    exceeding current holdings and the user hasn't ticked the confirmation
    checkbox shown above the tabs; blocks the save the same way for both
    Upload Slip and Manual Entry, since both call this function. Returns the
    collected field dict on a valid submit, else None."""
    prefill = prefill or {}
    with st.form(form_key, clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            trade_date = st.date_input("Trade Date", value=prefill.get("trade_date", pd.Timestamp.today().date()))
            order_type = st.text_input("Order Type (optional)", value=prefill.get("order_type", "") or "")
            order_id = st.text_input("Order ID (optional)", value=prefill.get("order_id", "") or "")
        with col2:
            # value=None (blank) when there's no prefill, same reasoning as
            # Quantity/Executed Price above -- but a real (even zero) prefilled value
            # from a parsed slip still shows pre-filled, since that's meant to be
            # reviewed/confirmed, not retyped.
            commission_fee = st.number_input(
                "Commission Fee", min_value=0.0, value=prefill.get("commission_fee") or None,
                placeholder="0.0000", format="%.4f",
            ) or 0.0
            vat = st.number_input(
                "VAT", min_value=0.0, value=prefill.get("vat") or None, placeholder="0.0000", format="%.4f",
            ) or 0.0
            reserved_fee = st.number_input(
                "Reserved Fee (SEC+TAF, sell only)", min_value=0.0,
                value=prefill.get("reserved_fee") or None, placeholder="0.0000", format="%.4f",
            ) or 0.0
            fee_rebate = st.number_input(
                "Fee Rebate (coupon, sell only)", min_value=0.0,
                value=prefill.get("fee_rebate") or None, placeholder="0.0000", format="%.4f",
            ) or 0.0

        st.caption(f"Net commission to be recorded: ${db.compute_net_commission(commission_fee, vat, reserved_fee, fee_rebate):,.4f}")
        submitted = st.form_submit_button("Save Trade")

    if not submitted:
        return None
    if not symbol:
        st.error("Symbol is required.")
        return None
    if quantity <= 0:
        st.error("Quantity must be greater than 0.")
        return None
    if price <= 0:
        st.error("Price must be greater than 0.")
        return None
    if oversell_blocked:
        st.error("This sells more than you currently hold -- tick the confirmation checkbox above before saving.")
        return None

    return {
        "trade_date": trade_date.strftime("%Y-%m-%d"),
        "side": side.lower(),
        "symbol": symbol,
        "quantity": quantity,
        "price": price,
        "commission_fee": commission_fee,
        "vat": vat,
        "reserved_fee": reserved_fee,
        "fee_rebate": fee_rebate,
        "order_type": order_type or None,
        "order_id": order_id or None,
    }


def slip_to_prefill(parsed: slip_parser.ParsedSlip) -> dict:
    """Maps a ParsedSlip's fee/order fields onto render_trade_form's prefill
    keys. Symbol/Side/Quantity/Price are handled separately by the caller --
    they're set into the shared outer widgets' session_state, not passed
    through this dict, since render_trade_form no longer renders them.

    Some slip screenshots don't show a date (e.g. cropped before the
    Completion date section) -- trade_date is left out of the dict in that
    case so render_trade_form's own default (today, editable) applies instead
    of crashing on an empty string."""
    prefill = {
        "commission_fee": parsed.commission_fee,
        "vat": parsed.vat,
        "reserved_fee": parsed.reserved_fee,
        "fee_rebate": parsed.fee_rebate,
        "order_type": parsed.order_type,
        "order_id": parsed.order_id,
    }
    if parsed.trade_date:
        try:
            prefill["trade_date"] = pd.to_datetime(parsed.trade_date).date()
        except (ValueError, TypeError):
            pass
    return prefill


def _handle_parse_slip():
    """on_click callback for the Parse Slip button -- runs *before* the script
    reruns top-to-bottom, so it's safe to write into the shared
    Symbol/Side/Quantity/Price widgets' session_state here. Doing this same
    assignment inline in the tab body (after those widgets have already been
    instantiated earlier in the same run) raises
    StreamlitAPIException("cannot be modified after the widget ... is
    instantiated") -- confirmed by hitting it during Step 5's real browser
    test. Errors/spinners can't reliably render from inside a callback, so
    status is stashed in session_state and drawn during the normal script
    body below instead."""
    uploaded = st.session_state.get("slip_uploader")
    if uploaded is None:
        return
    api_key = st.secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.session_state["slip_parse_error"] = "No ANTHROPIC_API_KEY configured in .streamlit/secrets.toml -- add one, or use Manual Entry instead."
        return
    try:
        parsed = slip_parser.parse_slip_image(uploaded.getvalue(), uploaded.type, api_key=api_key)
    except Exception as e:
        st.session_state["slip_parse_error"] = f"Couldn't parse this slip: {e}. Try Manual Entry instead."
        return

    st.session_state["slip_parse_error"] = None
    st.session_state["record_trade_symbol"] = parsed.symbol
    st.session_state["record_trade_side"] = "Buy" if parsed.side == "buy" else "Sell"
    st.session_state["record_trade_quantity"] = float(parsed.quantity)
    st.session_state["record_trade_price"] = float(parsed.price)
    st.session_state["parsed_slip"] = parsed


st.title("Record Trade")

# Fetched once, reused for the position lookup, the sell preview, the trade
# form's context, and the Recent Trades list below -- always the full history
# (seed + manual + slip), since a current position is a right-now snapshot,
# not duration-scoped.
db_trades = db.fetch_trades()

# Shared across both tabs, and rendered outside any form so they rerun
# immediately on each change -- a form only reruns on submit, which would make
# a live position lookup / sell preview impossible. Manual Entry types into
# these directly; Upload Slip pre-fills them (via session_state) once a slip
# is parsed, so the same position lookup / sell preview applies to both paths.
# Sourced from trade history so re-trading something you've held before is a pick, not
# a retype -- accept_new_options=True still lets a genuinely new symbol be typed in,
# unlike the Record Dividend grid's SelectboxColumn, which has no such escape hatch.
known_symbols = sorted(db_trades["Symbol"].dropna().unique().tolist())

oc1, oc2, oc3, oc4 = st.columns(4)
with oc1:
    symbol = st.selectbox(
        "Symbol", options=known_symbols, index=None, accept_new_options=True,
        placeholder="e.g. NVDY", key="record_trade_symbol",
    )
    symbol = (symbol or "").strip().upper()
with oc2:
    side = st.selectbox("Side", ["Buy", "Sell"], key="record_trade_side")
with oc3:
    # value=None (blank) instead of the min_value default of 0.0 -- typing a real
    # quantity then doesn't require backspacing a pre-filled "0.000000" first.
    quantity = st.number_input(
        "Quantity", min_value=0.0, value=None, placeholder="0.000000", format="%.6f", key="record_trade_quantity"
    ) or 0.0
with oc4:
    price = st.number_input(
        "Executed Price", min_value=0.0, value=None, placeholder="0.0000", format="%.4f", key="record_trade_price"
    ) or 0.0

# Only ever shown for a symbol that's never appeared in trades before -- an existing
# symbol (even one still sitting in "Others" from the one-time backfill) is never
# re-prompted here; that's Tools -> Allocation Type's job. Keyed by symbol so switching
# to a different new symbol before submitting doesn't carry over a stale selection --
# same reasoning as the confirm_oversell checkbox above.
is_new_symbol = bool(symbol) and symbol not in known_symbols
allocation_type = "Others"
if is_new_symbol:
    allocation_type = st.selectbox(
        "Allocation Type (first trade of this symbol)", ["Others", "Dividend", "Growth"],
        index=0, key=f"record_trade_allocation_type_{symbol}",
        help="Optional -- classify Dividend/Growth now, or leave as Others and set it later "
             "under Tools -> Allocation Type.",
    )

is_oversell = False
confirm_oversell = False
if symbol:
    positions = calculations.compute_current_positions(db_trades)
    match = positions[positions["Symbol"] == symbol]
    current_qty = match.iloc[0]["Quantity"] if not match.empty else 0.0
    if not match.empty:
        pos = match.iloc[0]
        st.info(
            f"Current position: **{pos['Quantity']:g} shares** of {symbol} at avg cost "
            f"**${pos['Avg Cost']:,.4f}/share** (cost basis ${pos['Cost Basis']:,.2f})"
        )
    else:
        st.caption(f"No current position in {symbol}.")

    if side == "Sell" and quantity > 0 and price > 0:
        preview = calculations.estimate_sell_realized_pl(db_trades, symbol, quantity, price)
        if preview is not None:
            sign = "gain" if preview >= 0 else "loss"
            st.info(f"Estimated Realized P/L: **${preview:,.2f}** ({sign}, excludes commission/fees)")

        # Tolerance matches calculations.estimate_sell_realized_pl's own oversell check
        # (quantity > available + 1e-9) -- current_qty is a float64 sum accumulated across
        # every FIFO lot, so a position built from many small buys (e.g. DRIP-style trades)
        # can land a hair below its displayed, rounded value (82.0812 shown, 82.08119999...
        # actually stored). Selling exactly the displayed full position must not trip this.
        is_oversell = quantity > current_qty + 1e-9
        if is_oversell:
            st.warning(f"You only hold {current_qty:g} shares of {symbol} -- this would sell more than you have.")
            # Keyed by symbol+quantity (not a fixed key) so a checked confirmation never
            # silently carries over to a different oversell scenario -- each distinct
            # symbol/quantity combination needs its own fresh acknowledgement.
            confirm_oversell = st.checkbox(
                "I understand this sells more than my current holding -- save anyway",
                key=f"confirm_oversell_{symbol}_{quantity}",
            )

# Shared by both tabs below -- Upload Slip and Manual Entry both call
# render_trade_form, so gating the save here covers both the same way.
oversell_blocked = is_oversell and not confirm_oversell

tab_upload, tab_manual = st.tabs(["Upload Slip", "Manual Entry"])

with tab_upload:
    uploaded = st.file_uploader("Slip screenshot", type=["png", "jpg", "jpeg"], key="slip_uploader")
    if uploaded is not None:
        st.image(uploaded, width=350)
        st.button("Parse Slip", on_click=_handle_parse_slip)

    if st.session_state.get("slip_parse_error"):
        st.error(st.session_state["slip_parse_error"])

    parsed_slip = st.session_state.get("parsed_slip")
    if parsed_slip:
        st.success("Slip parsed -- review the fields below (including Symbol/Side/Quantity/Price above), then confirm.")
        if not parsed_slip.trade_date:
            st.warning("Trade date wasn't visible on this slip -- defaulted to today. Please set it manually below.")
        result = render_trade_form(
            symbol, side, quantity, price, prefill=slip_to_prefill(parsed_slip),
            form_key="slip_trade", oversell_blocked=oversell_blocked,
        )
        if result:
            db.insert_trade(**result, source="slip")
            if is_new_symbol and allocation_type != "Others":
                db.set_symbol_type(symbol, allocation_type)
            del st.session_state["parsed_slip"]
            st.success(f"Saved: {result['side']} {result['quantity']:g} {result['symbol']} @ ${result['price']:,.4f}")
            st.rerun()
    else:
        st.caption("Upload a slip image and click Parse Slip to get started.")

with tab_manual:
    result = render_trade_form(symbol, side, quantity, price, form_key="manual_trade", oversell_blocked=oversell_blocked)
    if result:
        db.insert_trade(**result, source="manual")
        if is_new_symbol and allocation_type != "Others":
            db.set_symbol_type(symbol, allocation_type)
        st.success(f"Saved: {result['side']} {result['quantity']:g} {result['symbol']} @ ${result['price']:,.4f}")
        st.rerun()

st.divider()
st.subheader("Recent Trades")
st.caption("Trades logged through this page (manual or slip upload). To fix a mistake, delete the row and re-enter it.")

# Seed rows (the imported xlsx history) are deliberately not shown or deletable
# here -- this list is only for entries made through this page.
recent = db_trades[db_trades["source"].isin(["manual", "slip"])].sort_values("Trade Date", ascending=False)

# Joined on id (compute_fifo_realized_pl threads the originating trade's id
# through) so each row shows the Realized P/L it actually produced -- blank
# for buys, matching the same "reason" shown in the Dashboard's Since Last
# Statement panel.
if not recent.empty:
    realized = calculations.compute_fifo_realized_pl(db_trades).dropna(subset=["id"])[["id", "Realized P/L"]]
    recent = recent.copy()
    recent["id"] = recent["id"].astype(float)
    recent = recent.merge(realized, on="id", how="left")

if recent.empty:
    st.caption("No manual or slip-uploaded trades logged yet.")
else:
    header_cols = st.columns([1.2, 0.9, 0.7, 1, 1, 1, 1, 0.8])
    for col, label in zip(header_cols, ["Date", "Symbol", "Side", "Quantity", "Price", "Amount", "Realized P/L", ""]):
        col.markdown(f"**{label}**")
    for _, row in recent.iterrows():
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.2, 0.9, 0.7, 1, 1, 1, 1, 0.8])
        c1.write(row["Trade Date"].strftime("%Y-%m-%d"))
        c2.write(row["Symbol"])
        c3.write(row["Side"])
        c4.write(f"{row['Quantity']:g}")
        c5.write(f"${row['Price']:,.4f}" if pd.notna(row["Price"]) else "—")
        c6.write(f"${row['Amount']:,.2f}" if pd.notna(row["Amount"]) else "—")
        c7.write(f"${row['Realized P/L']:,.2f}" if pd.notna(row["Realized P/L"]) else "—")
        with c8.popover("Delete"):
            st.write(
                f"Delete this {row['Side']} of {row['Quantity']:g} {row['Symbol']} "
                f"on {row['Trade Date'].strftime('%Y-%m-%d')}?"
            )
            if st.button("Yes, delete", key=f"confirm_delete_{row['id']}"):
                db.delete_trade(row["id"])
                st.rerun()

if not recent.empty and recent["Realized P/L"].notna().any():
    st.caption("Realized P/L reflects FIFO matching against your oldest open lot for that symbol.")
