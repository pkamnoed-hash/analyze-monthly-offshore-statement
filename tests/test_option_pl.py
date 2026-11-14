import pandas as pd
import pytest

from core.option_pl import compute_option_summary, match_round_trips, resolved_events, validate_option_rows


def _row(
    symbol, underlying, option_type, strike, expiration_date, trade_date,
    side, position_effect, contracts, amount, id=None, source="manual",
):
    return {
        "id": id, "trade_date": pd.Timestamp(trade_date), "symbol": symbol,
        "underlying": underlying, "option_type": option_type, "strike": strike,
        "expiration_date": pd.Timestamp(expiration_date), "side": side,
        "position_effect": position_effect, "contracts": contracts, "amount": amount,
        "source": source,
    }


def _df(rows):
    return pd.DataFrame(rows)


class TestValidateOptionRows:
    def test_valid_row_passes(self):
        valid, errors = validate_option_rows([{
            "underlying": "mnst", "option_type": "call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "BUY", "position_effect": "Open",
            "contracts": 1, "price": 1.80, "trade_date": "2026-11-12",
        }])
        assert errors == []
        assert valid[0]["symbol"] == "MNST261218C00045000"
        assert valid[0]["side"] == "buy"
        assert valid[0]["position_effect"] == "open"

    def test_invalid_option_type_rejected(self):
        valid, errors = validate_option_rows([{
            "underlying": "MNST", "option_type": "Straddle", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
            "contracts": 1, "price": 1.80, "trade_date": "2026-11-12",
        }])
        assert valid == []
        assert "option_type" in errors[0]

    def test_invalid_position_effect_rejected(self):
        valid, errors = validate_option_rows([{
            "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "buy", "position_effect": "opened",
            "contracts": 1, "price": 1.80, "trade_date": "2026-11-12",
        }])
        assert valid == []
        assert "position_effect" in errors[0]

    def test_non_positive_contracts_rejected(self):
        valid, errors = validate_option_rows([{
            "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
            "contracts": 0, "price": 1.80, "trade_date": "2026-11-12",
        }])
        assert valid == []
        assert "contracts" in errors[0]

    def test_one_bad_row_does_not_drop_a_good_row(self):
        good = {
            "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
            "contracts": 1, "price": 1.80, "trade_date": "2026-11-12",
        }
        bad = {**good, "side": "hold"}
        valid, errors = validate_option_rows([good, bad])
        assert len(valid) == 1
        assert len(errors) == 1
        assert "row 1" in errors[0]


class TestMatchRoundTripsLong:
    def test_real_historical_long_round_trip_matches_known_pl(self):
        # ONDS $12.00 Put: bought 13 Jan 2026 @ 0.79 (amount -79.00), sold 27 Jan 2026
        # @ 1.06 (amount +106.00) -- known real P/L: +$27.00 / +34.2%.
        trades = _df([
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-13", "buy", "open", 1, -79.00),
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-27", "sell", "close", 1, 106.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert len(result["closed"]) == 1
        row = result["closed"][0]
        assert row["contracts"] == 1
        assert round(row["pl"], 2) == 27.00
        assert round(row["pl_pct"], 1) == 34.2
        assert result["open"] == []
        assert result["expired"] == []
        assert result["errors"] == []

    def test_real_historical_loss_round_trip(self):
        # GDX $88.00 Put: bought @ 4.30 (-430.00), sold @ 0.35 (+35.00) -> -$395.00 / -91.9%.
        trades = _df([
            _row("GDX260320P00088000", "GDX", "Put", 88.0, "2026-03-20", "2026-02-02", "buy", "open", 1, -430.00),
            _row("GDX260320P00088000", "GDX", "Put", 88.0, "2026-03-20", "2026-03-17", "sell", "close", 1, 35.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        row = result["closed"][0]
        assert round(row["pl"], 2) == -395.00
        assert round(row["pl_pct"], 1) == -91.9


class TestMatchRoundTripsShort:
    def test_sell_to_open_buy_to_close_computes_correctly(self):
        # This is the exact case that's broken in the stock FIFO pipeline: sell-to-open
        # collects premium (+300), buy-to-close costs less (-100) -> net gain +$200,
        # not an instant-100%-profit-on-open bug and no phantom long lot afterward.
        trades = _df([
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-01-05", "sell", "open", 1, 300.00),
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-02-10", "buy", "close", 1, -100.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert len(result["closed"]) == 1
        row = result["closed"][0]
        assert row["side_open"] == "sell"
        assert row["side_close"] == "buy"
        assert round(row["pl"], 2) == 200.00
        assert round(row["pl_pct"], 2) == round(200 / 300 * 100, 2)
        assert result["open"] == []


class TestMatchRoundTripsPartialFills:
    def test_partial_close_leaves_correct_remainder(self):
        # Open 10 contracts, close 4 -- exactly 4 contracts' worth matched/closed,
        # exactly 6 remain open with proportional cost.
        trades = _df([
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-10-20", "buy", "open", 10, -8000.00),
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-11-10", "sell", "close", 4, 4600.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert len(result["closed"]) == 1
        closed = result["closed"][0]
        assert closed["contracts"] == 4
        assert round(closed["open_amount"], 2) == -3200.00  # 4/10 of -8000
        assert round(closed["close_amount"], 2) == 4600.00
        assert round(closed["pl"], 2) == 1400.00

        assert len(result["open"]) == 1
        remaining = result["open"][0]
        assert remaining["contracts"] == 6
        assert remaining["total_contracts"] == 10
        assert round(remaining["open_amount"], 2) == -4800.00  # 6/10 of -8000

    def test_lot_closed_across_two_separate_partial_sells(self):
        # Open 10, close 4, close the other 6 later -- two distinct closed round
        # trips, each with its own correct matched quantity and P/L, summing back
        # to the whole lot; nothing left in "open".
        trades = _df([
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-10-20", "buy", "open", 10, -8000.00),
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-11-10", "sell", "close", 4, 4600.00),
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-11-20", "sell", "close", 6, 7200.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert len(result["closed"]) == 2
        assert result["closed"][0]["contracts"] == 4
        assert result["closed"][1]["contracts"] == 6
        assert round(result["closed"][0]["open_amount"], 2) == -3200.00
        assert round(result["closed"][1]["open_amount"], 2) == -4800.00
        total_pl = sum(r["pl"] for r in result["closed"])
        # Whole-lot P/L: total close proceeds (4600+7200=11800) minus total open cost (8000)
        assert round(total_pl, 2) == round(4600 + 7200 - 8000, 2)
        assert result["open"] == []

    def test_close_exceeding_open_is_reported_as_an_error_not_silently_absorbed(self):
        trades = _df([
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-01-05", "buy", "open", 2, -200.00),
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-02-10", "sell", "close", 5, 500.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert len(result["closed"]) == 1
        assert result["closed"][0]["contracts"] == 2
        assert len(result["errors"]) == 1
        assert "XYZ260601P00050000" in result["errors"][0]


class TestMatchRoundTripsOpenAndExpired:
    def test_open_position_with_future_expiration_shows_as_open(self):
        trades = _df([
            _row("XYZ270601P00050000", "XYZ", "Put", 50.0, "2027-06-01", "2026-01-05", "buy", "open", 1, -200.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert len(result["open"]) == 1
        assert result["expired"] == []

    def test_long_open_with_past_expiration_resolves_as_full_loss(self):
        trades = _df([
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-01-05", "buy", "open", 1, -200.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert result["open"] == []
        assert len(result["expired"]) == 1
        assert round(result["expired"][0]["pl"], 2) == -200.00
        assert round(result["expired"][0]["pl_pct"], 2) == -100.00

    def test_short_open_with_past_expiration_resolves_as_full_gain(self):
        trades = _df([
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-01-05", "sell", "open", 1, 200.00),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert result["open"] == []
        assert len(result["expired"]) == 1
        assert round(result["expired"][0]["pl"], 2) == 200.00
        assert round(result["expired"][0]["pl_pct"], 2) == 100.00


class TestSourceTracking:
    """open_source/close_source is what Option Monitor uses to decide whether to show
    a Delete button (manual/slip) or a locked "History" indicator (migrated-seed) --
    same restriction delete_trade/delete_dividend's own pages already enforce."""

    def test_closed_row_carries_both_legs_source(self):
        trades = _df([
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-13", "buy", "open", 1, -79.00, id=1, source="migrated-seed"),
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-27", "sell", "close", 1, 106.00, id=2, source="migrated-seed"),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        row = result["closed"][0]
        assert row["open_source"] == "migrated-seed"
        assert row["close_source"] == "migrated-seed"

    def test_manually_entered_round_trip_is_deletable(self):
        trades = _df([
            _row("MNST261218C00045000", "MNST", "Call", 45.0, "2026-12-18", "2026-11-12", "buy", "open", 1, -180.64, id=10, source="manual"),
            _row("MNST261218C00045000", "MNST", "Call", 45.0, "2026-12-18", "2026-11-13", "sell", "close", 1, 234.35, id=11, source="slip"),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        row = result["closed"][0]
        assert row["open_source"] == "manual"
        assert row["close_source"] == "slip"
        assert row["open_id"] == 10
        assert row["close_id"] == 11

    def test_open_and_expired_rows_carry_open_source(self):
        trades = _df([
            _row("XYZ270601P00050000", "XYZ", "Put", 50.0, "2027-06-01", "2026-01-05", "buy", "open", 1, -200.00, id=20, source="manual"),
            _row("ABC260601P00050000", "ABC", "Put", 50.0, "2026-06-01", "2026-01-05", "sell", "open", 1, 200.00, id=21, source="migrated-seed"),
        ])
        result = match_round_trips(trades, as_of="2026-11-14")
        assert result["open"][0]["open_source"] == "manual"
        assert result["expired"][0]["open_source"] == "migrated-seed"


class TestResolvedEvents:
    """Feeds core/calculations.py::compute_investment_gain's new option_realized
    term -- shaped like its own realized-events DataFrame (Trade Date/Realized
    P/L) so Dashboard and Rich's get_lifetime_pl can filter/sum it identically."""

    def test_closed_round_trip_uses_the_close_date(self):
        trades = _df([
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-13", "buy", "open", 1, -79.00),
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-27", "sell", "close", 1, 106.00),
        ])
        events = resolved_events(trades, as_of="2026-11-14")
        assert len(events) == 1
        assert events.iloc[0]["Trade Date"] == pd.Timestamp("2026-01-27")  # the CLOSE date, not opened
        assert events.iloc[0]["Realized P/L"] == pytest.approx(27.0)

    def test_expired_position_uses_the_expiration_date_not_opened_date(self):
        trades = _df([
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-01-05", "sell", "open", 1, 200.00),
        ])
        events = resolved_events(trades, as_of="2026-11-14")
        assert len(events) == 1
        assert events.iloc[0]["Trade Date"] == pd.Timestamp("2026-06-01")  # the EXPIRATION date, not opened
        assert events.iloc[0]["Realized P/L"] == pytest.approx(200.0)

    def test_still_open_position_is_excluded(self):
        trades = _df([
            _row("XYZ270601P00050000", "XYZ", "Put", 50.0, "2027-06-01", "2026-01-05", "buy", "open", 1, -200.00),
        ])
        events = resolved_events(trades, as_of="2026-11-14")
        assert events.empty

    def test_empty_input_returns_empty_frame_with_right_columns(self):
        events = resolved_events(pd.DataFrame(columns=[
            "trade_date", "symbol", "underlying", "option_type", "strike",
            "expiration_date", "side", "position_effect", "contracts", "amount",
        ]))
        assert events.empty
        assert list(events.columns) == ["Trade Date", "Realized P/L"]


class TestComputeOptionSummary:
    def test_win_loss_and_total_sum_across_a_mixed_batch_including_partials(self):
        trades = _df([
            # Closed win: ONDS +27
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-13", "buy", "open", 1, -79.00),
            _row("ONDS260130P00012000", "ONDS", "Put", 12.0, "2026-01-30", "2026-01-27", "sell", "close", 1, 106.00),
            # Closed loss: GDX -395
            _row("GDX260320P00088000", "GDX", "Put", 88.0, "2026-03-20", "2026-02-02", "buy", "open", 1, -430.00),
            _row("GDX260320P00088000", "GDX", "Put", 88.0, "2026-03-20", "2026-03-17", "sell", "close", 1, 35.00),
            # Partial close win: NVDA 4 of 10, +1400 (remaining 6 stay open, not counted)
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-10-20", "buy", "open", 10, -8000.00),
            _row("NVDA270115C00150000", "NVDA", "Call", 150.0, "2027-01-15", "2026-11-10", "sell", "close", 4, 4600.00),
            # Expired win (short, past expiration): +200
            _row("XYZ260601P00050000", "XYZ", "Put", 50.0, "2026-06-01", "2026-01-05", "sell", "open", 1, 200.00),
        ])
        summary = compute_option_summary(trades, as_of="2026-11-14")
        assert summary["win_count"] == 3  # ONDS, NVDA partial, XYZ expired
        assert summary["loss_count"] == 1  # GDX
        assert round(summary["win_rate_pct"], 1) == 75.0
        assert round(summary["total_realized_pl"], 2) == round(27 - 395 + 1400 + 200, 2)
        assert len(summary["open"]) == 1  # the remaining 6 NVDA contracts
        # total_pl_pct weights by premium opened, not a plain average of each trip's
        # own pl_pct: (27-395+1400+200) / (79+430+3200+200) * 100
        assert round(summary["total_pl_pct"], 1) == 31.5

    def test_empty_input_returns_zeroed_summary(self):
        summary = compute_option_summary(pd.DataFrame(columns=[
            "trade_date", "symbol", "underlying", "option_type", "strike",
            "expiration_date", "side", "position_effect", "contracts", "amount",
        ]))
        assert summary["win_count"] == 0
        assert summary["loss_count"] == 0
        assert summary["win_rate_pct"] == 0.0
        assert summary["total_realized_pl"] == 0.0
        assert summary["total_pl_pct"] == 0.0
