import pandas as pd
import streamlit as st

import cached_db
from core import db, option_pl, option_slip_parser

PRESET_MONTHS_BACK = {
    "All": None, "This Month": 0, "Past Week": 0, "Past Month": 0,
    "Past 3 Months": 2, "Past 6 Months": 5, "Past Year": 11, "Past 2 Years": 23,
}


def _contract_label(row) -> str:
    return f"{row['underlying']} ${row['strike']:,.2f} {row['option_type']}"


def render_option_form(prefill=None, form_key="manual_option"):
    """Renders position_effect + the six fee fields inside a form (Underlying/Type/
    Strike/Expiration/Side/Contracts/Price/Trade Date are OUTER widgets, shared
    between Manual Entry and Upload Screenshot -- see the module-level widgets below
    and record_trade.py's render_trade_form for why: a widget inside st.form can't be
    programmatically pre-filled from the Parse button's callback after it's already
    been instantiated). Returns the collected field dict on a valid submit, else
    None -- caller is responsible for validating/building the row via
    core.option_pl.validate_option_rows before writing."""
    prefill = prefill or {}
    has_prefilled_fee = any(prefill.get(k) for k in ("commission_fee", "vat", "occ_fee", "orf_fee", "taf_fee", "sec_fee"))
    with st.form(form_key, clear_on_submit=True, border=False):
        position_effect = st.radio(
            "This trade", ["Open", "Close"], horizontal=True,
            help="Open: this is the first leg of a new position (buying or selling to "
                 "start it). Close: this closes some or all of an existing open position.",
        )
        with st.expander("Fees (optional)", expanded=has_prefilled_fee):
            col1, col2, col3 = st.columns(3)
            with col1:
                commission_fee = st.number_input(
                    "Commission Fee", min_value=0.0, value=prefill.get("commission_fee") or None,
                    placeholder="0.00", format="%.2f",
                ) or 0.0
                vat = st.number_input(
                    "VAT", min_value=0.0, value=prefill.get("vat") or None, placeholder="0.00", format="%.2f",
                ) or 0.0
            with col2:
                occ_fee = st.number_input(
                    "OCC Fee", min_value=0.0, value=prefill.get("occ_fee") or None, placeholder="0.00", format="%.2f",
                ) or 0.0
                orf_fee = st.number_input(
                    "ORF Fee", min_value=0.0, value=prefill.get("orf_fee") or None, placeholder="0.00", format="%.2f",
                ) or 0.0
            with col3:
                taf_fee = st.number_input(
                    "TAF Fee", min_value=0.0, value=prefill.get("taf_fee") or None, placeholder="0.00", format="%.2f",
                ) or 0.0
                sec_fee = st.number_input(
                    "SEC Fee", min_value=0.0, value=prefill.get("sec_fee") or None, placeholder="0.00", format="%.2f",
                ) or 0.0
        submitted = st.form_submit_button("Save option trade")

    if not submitted:
        return None
    return {
        "position_effect": position_effect.lower(),
        "commission": commission_fee, "vat": vat, "occ_fee": occ_fee,
        "orf_fee": orf_fee, "taf_fee": taf_fee, "sec_fee": sec_fee,
    }


def slip_to_prefill(parsed: option_slip_parser.ParsedOptionSlip) -> dict:
    """Maps a ParsedOptionSlip's fee fields onto render_option_form's prefill keys --
    Underlying/Type/Strike/Expiration/Side/Contracts/Price/Trade Date are set into
    the shared OUTER widgets' session_state directly (see _handle_parse_option_slip),
    not through this dict. position_effect is never in here -- the slip never shows
    it (see core/option_slip_parser.py's own module docstring), so the user always
    picks Open/Close themselves via the form's radio button."""
    return {
        "commission_fee": parsed.commission_fee, "vat": parsed.vat,
        "occ_fee": parsed.occ_fee, "orf_fee": parsed.orf_fee,
        "taf_fee": parsed.taf_fee, "sec_fee": parsed.sec_fee,
    }


def _handle_parse_option_slip():
    """on_click callback for the Parse button -- same rationale as record_trade.py's
    _handle_parse_slip: runs before the script reruns, so it's safe to write into
    the shared outer widgets' session_state here (not safe to do inline in the tab
    body after those widgets are already instantiated)."""
    uploaded = st.session_state.get("option_slip_uploader")
    if uploaded is None:
        return
    api_key = st.secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.session_state["option_slip_parse_error"] = (
            "No ANTHROPIC_API_KEY configured in .streamlit/secrets.toml -- add one, or use Manual Entry instead."
        )
        return
    try:
        parsed = option_slip_parser.parse_option_slip_image(uploaded.getvalue(), uploaded.type, api_key=api_key)
    except Exception as e:
        st.session_state["option_slip_parse_error"] = f"Couldn't parse this slip: {e}. Try Manual Entry instead."
        return

    st.session_state["option_slip_parse_error"] = None
    st.session_state["option_underlying"] = parsed.underlying
    st.session_state["option_type"] = parsed.option_type
    st.session_state["option_strike"] = float(parsed.strike)
    st.session_state["option_side"] = "Buy" if parsed.side == "buy" else "Sell"
    st.session_state["option_contracts"] = float(parsed.contracts)
    st.session_state["option_price"] = float(parsed.price)
    try:
        st.session_state["option_expiration"] = pd.to_datetime(parsed.expiration_date).date()
    except (ValueError, TypeError):
        pass
    if parsed.trade_date:
        try:
            st.session_state["option_trade_date"] = pd.to_datetime(parsed.trade_date).date()
        except (ValueError, TypeError):
            pass
    st.session_state["parsed_option_slip"] = parsed


def _clear_outer_option_widgets():
    for key in (
        "option_trade_date", "option_underlying", "option_type", "option_strike",
        "option_expiration", "option_side", "option_contracts", "option_price",
    ):
        st.session_state.pop(key, None)


st.title("Option Monitor")
with st.expander("What does this page do?"):
    st.caption(
        "Record option trades (manually or from a trade-confirmation screenshot), see which "
        "contracts are still open, which have closed, and your overall win rate and realized "
        "P/L. Doesn't record a stock trade -- if a contract gets exercised/assigned, log that "
        "separately on Record Trade."
    )

option_trades = cached_db.cached_fetch_option_trades()
summary = option_pl.compute_option_summary(option_trades)
if summary["errors"]:
    for err in summary["errors"]:
        st.warning(err)

search = st.text_input(
    "Filter by contract or underlying", placeholder='e.g. "MNST" or "MNST $20 Call"',
    key="option_search",
).strip().lower()


def _matches_search(row) -> bool:
    return not search or search in _contract_label(row).lower() or search in row["underlying"].lower()


duration_col1, duration_col2 = st.columns([2, 2])
with duration_col1:
    preset = st.segmented_control("Duration", list(PRESET_MONTHS_BACK.keys()), default="All", key="option_duration_preset")
today = pd.Timestamp.today().normalize()
all_dates = pd.concat([
    pd.Series([r["opened"] for r in summary["closed"] + summary["expired"] + summary["open"]]),
    pd.Series([r["closed"] for r in summary["closed"]]),
]) if (summary["closed"] or summary["expired"] or summary["open"]) else pd.Series([today], dtype="datetime64[ns]")
earliest = pd.Timestamp(all_dates.min()) if not all_dates.empty else today
months_back = PRESET_MONTHS_BACK.get(preset)
if preset == "Past Week":
    preset_from = today - pd.Timedelta(days=7)
elif months_back is None:
    preset_from = earliest
else:
    preset_from = today - pd.DateOffset(months=months_back)

with duration_col2:
    from_col, to_col = st.columns(2)
    with from_col:
        date_from = pd.Timestamp(st.date_input("From", value=preset_from.date(), key=f"option_from_{preset}"))
    with to_col:
        date_to = pd.Timestamp(st.date_input("To", value=today.date(), key=f"option_to_{preset}"))


def _in_range(d) -> bool:
    d = pd.Timestamp(d)
    return date_from <= d <= date_to


closed_filtered = [r for r in summary["closed"] if _matches_search(r) and _in_range(r["closed"])]
expired_filtered = [r for r in summary["expired"] if _matches_search(r) and _in_range(r["opened"])]
open_filtered = [r for r in summary["open"] if _matches_search(r) and _in_range(r["opened"])]
resolved_filtered = closed_filtered + expired_filtered

win_count = sum(1 for r in resolved_filtered if r["pl"] > 0)
loss_count = len(resolved_filtered) - win_count
win_rate_pct = (win_count / len(resolved_filtered) * 100) if resolved_filtered else 0.0
total_realized_pl = sum(r["pl"] for r in resolved_filtered)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Win rate", f"{win_rate_pct:.1f}%", delta=f"{win_count}W / {loss_count}L", delta_color="off")
k2.metric("Total realized P/L", f"${total_realized_pl:,.2f}")
k3.metric("Round trips closed", str(len(resolved_filtered)))
k4.metric("Open positions", str(len(open_filtered)))

tab_record, tab_open, tab_closed = st.tabs([
    "Record trade",
    f"Open positions ({len(open_filtered) + len(expired_filtered)})",
    f"Closed trades ({len(closed_filtered)})",
])

with tab_record:
    oc1, oc2, oc3, oc4 = st.columns(4)
    with oc1:
        underlying = st.text_input("Underlying", placeholder="e.g. MNST", key="option_underlying").strip().upper()
    with oc2:
        option_type = st.selectbox("Type", ["Call", "Put"], key="option_type")
    with oc3:
        strike = st.number_input("Strike (USD)", min_value=0.0, value=None, placeholder="0.00", format="%.2f", key="option_strike") or 0.0
    with oc4:
        expiration = st.date_input("Expiration date", value=None, key="option_expiration")

    oc5, oc6, oc7, oc8 = st.columns(4)
    with oc5:
        side = st.selectbox("Side", ["Buy", "Sell"], key="option_side")
    with oc6:
        contracts = st.number_input("Contracts", min_value=0.0, value=None, placeholder="1", format="%.0f", key="option_contracts") or 0.0
    with oc7:
        price = st.number_input("Price / contract (USD)", min_value=0.0, value=None, placeholder="0.00", format="%.2f", key="option_price") or 0.0
    with oc8:
        trade_date = st.date_input("Trade date", value=pd.Timestamp.today().date(), key="option_trade_date")
    st.caption(f"{contracts:g} contract(s) = {contracts * 100:g} shares" if contracts else "1 contract = 100 shares")

    record_mode = st.radio("How", ["Manual entry", "Upload screenshot"], horizontal=True, key="option_record_mode")

    def _save_option_trade(form_result, source):
        rows, errors = option_pl.validate_option_rows([{
            "underlying": underlying, "option_type": option_type, "strike": strike,
            "expiration_date": expiration, "side": side.lower(),
            "position_effect": form_result["position_effect"], "contracts": contracts,
            "price": price, "trade_date": trade_date,
            "commission": form_result["commission"], "vat": form_result["vat"],
            "occ_fee": form_result["occ_fee"], "orf_fee": form_result["orf_fee"],
            "taf_fee": form_result["taf_fee"], "sec_fee": form_result["sec_fee"],
        }])
        if errors:
            for e in errors:
                st.error(e)
            return
        row = rows[0]
        # pd.Timestamp can't be bound directly as a SQL parameter by the DB driver --
        # same fix V4.19's record_trade_batch/record_dividend_batch already needed,
        # applied here at the same point: right before the insert call, not inside
        # validate_option_rows (whose pd.Timestamp return value other callers, e.g.
        # tests, rely on).
        row = {
            **row,
            "trade_date": pd.Timestamp(row["trade_date"]).strftime("%Y-%m-%d"),
            "expiration_date": pd.Timestamp(row["expiration_date"]).strftime("%Y-%m-%d"),
        }
        db.insert_option_trade(**row, source=source)
        cached_db.invalidate_option_trades()
        st.success(
            f"Saved: {row['side'].title()} to {row['position_effect']} "
            f"{row['contracts']:g} {_contract_label(row)} @ ${row['price']:,.2f}"
        )
        _clear_outer_option_widgets()
        st.rerun()

    if record_mode == "Manual entry":
        result = render_option_form(form_key="manual_option")
        if result:
            _save_option_trade(result, source="manual")
    else:
        uploaded = st.file_uploader("Order Information screenshot", type=["png", "jpg", "jpeg"], key="option_slip_uploader")
        if uploaded is not None:
            st.image(uploaded, width=350)
            st.button("Parse screenshot", on_click=_handle_parse_option_slip)

        if st.session_state.get("option_slip_parse_error"):
            st.error(st.session_state["option_slip_parse_error"])

        parsed_slip = st.session_state.get("parsed_option_slip")
        if parsed_slip:
            st.success(
                "Screenshot parsed -- review the fields above (and pick Open/Close below), then confirm."
            )
            if not parsed_slip.trade_date:
                st.warning("Trade date wasn't visible on this slip -- defaulted to today. Please set it manually above.")
            result = render_option_form(prefill=slip_to_prefill(parsed_slip), form_key="slip_option")
            if result:
                _save_option_trade(result, source="slip")
                del st.session_state["parsed_option_slip"]
        else:
            st.caption("Upload a screenshot and click Parse screenshot to get started.")

with tab_open:
    combined_open = sorted(
        open_filtered + expired_filtered, key=lambda r: pd.Timestamp(r["opened"]), reverse=True,
    )
    if not combined_open:
        st.caption("No open option positions in this range.")
    else:
        header_cols = st.columns([1.6, 0.7, 1, 1, 0.8, 1, 0.9, 0.8, 0.8, 0.6])
        for col, label in zip(
            header_cols,
            ["Contract", "Position", "Opened", "Expiration", "Contracts", "Cost/Credit", "Status", "Est. P/L", "Est. % P/L", ""],
        ):
            col.markdown(f"**{label}**")
        for r in combined_open:
            is_expired = "pl" in r
            c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1.6, 0.7, 1, 1, 0.8, 1, 0.9, 0.8, 0.8, 0.6])
            c1.write(_contract_label(r))
            c2.write("Long" if r["side_open"] == "buy" else "Short")
            c3.write(pd.Timestamp(r["opened"]).strftime("%Y-%m-%d"))
            c4.write(pd.Timestamp(r["expiration_date"]).strftime("%Y-%m-%d"))
            total = r.get("total_contracts")
            c5.write(f"{r['contracts']:g} of {total:g}" if total and total != r["contracts"] else f"{r['contracts']:g}")
            c6.write(f"${r['open_amount']:,.2f}")
            c7.write("Expired" if is_expired else "Open")
            c8.write(f"${r['pl']:,.2f}" if is_expired else "—")
            c9.write(f"{r['pl_pct']:+.1f}%" if is_expired else "—")
            if r["open_source"] == "migrated-seed":
                c10.caption("History")
            else:
                with c10.popover("Delete"):
                    st.write(f"Delete this {_contract_label(r)} open trade ({pd.Timestamp(r['opened']).strftime('%Y-%m-%d')})?")
                    if st.button("Yes, delete", key=f"confirm_delete_open_{r['open_id']}"):
                        db.delete_option_trade(r["open_id"])
                        cached_db.invalidate_option_trades()
                        st.rerun()
        st.caption(
            "Cost/Credit is the known cash flow from opening (paid for a Long, collected for a "
            "Short). Est. P/L/% P/L stay blank for a genuinely still-open position -- no live "
            "option-pricing feed to value it against -- and only appear once it resolves (closed "
            "or expired)."
        )

with tab_closed:
    if not closed_filtered:
        st.caption("No closed option round trips in this range.")
    else:
        header_cols = st.columns([1.6, 1, 1, 0.8, 1, 0.9, 0.8, 0.6])
        for col, label in zip(header_cols, ["Contract", "Opened", "Closed", "Contracts", "P/L", "% P/L", "Result", ""]):
            col.markdown(f"**{label}**")
        for r in sorted(closed_filtered, key=lambda r: pd.Timestamp(r["closed"]), reverse=True):
            c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.6, 1, 1, 0.8, 1, 0.9, 0.8, 0.6])
            c1.write(_contract_label(r))
            c2.write(pd.Timestamp(r["opened"]).strftime("%Y-%m-%d"))
            c3.write(pd.Timestamp(r["closed"]).strftime("%Y-%m-%d"))
            c4.write(f"{r['contracts']:g}")
            c5.write(f"${r['pl']:,.2f}")
            c6.write(f"{r['pl_pct']:+.1f}%")
            c7.write("Win" if r["pl"] > 0 else "Loss")
            if r["close_source"] == "migrated-seed":
                c8.caption("History")
            else:
                with c8.popover("Delete"):
                    st.write(
                        f"Delete this {_contract_label(r)} closing trade "
                        f"({pd.Timestamp(r['closed']).strftime('%Y-%m-%d')})? The matched contracts "
                        "will return to Open Positions."
                    )
                    if st.button("Yes, delete", key=f"confirm_delete_close_{r['close_id']}"):
                        db.delete_option_trade(r["close_id"])
                        cached_db.invalidate_option_trades()
                        st.rerun()
