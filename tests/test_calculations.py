import pandas as pd
import pytest

from core.calculations import (
    apply_market_profile_fallback,
    blended_dividends,
    blended_realized_pl,
    cluster_price_levels,
    compute_current_positions,
    compute_fifo_realized_pl,
    compute_holding_period_start,
    compute_horizontal_sr_zones,
    compute_moving_average,
    compute_pivot_points,
    compute_realized_pl,
    compute_reference_lines,
    compute_roi,
    compute_stochastic_oscillator,
    compute_swing_trend_lines,
    count_touches,
    describe_market_profile_freshness,
    estimate_sell_realized_pl,
    find_nearest_levels,
    find_swing_points,
    nearest_reference_cell,
    resample_ohlc,
    to_heikin_ashi,
)


def make_transactions(rows):
    """Build a transactions DataFrame with the columns compute_realized_pl expects.
    Each row is a dict; missing fields default to None so tests can stay terse."""
    columns = ["Month", "Trade Date", "Entry Type", "Side", "Symbol", "Quantity", "Price", "Amount", "Commission"]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    df["Month"] = pd.to_datetime(df["Month"])
    return df[columns]


def buy(symbol, date, qty, price, commission=None):
    return {
        "Month": date, "Trade Date": date, "Entry Type": "Trade Entry", "Side": "buy",
        "Symbol": symbol, "Quantity": qty, "Price": price, "Amount": -qty * price, "Commission": commission,
    }


def sell(symbol, date, qty, price, commission=None):
    return {
        "Month": date, "Trade Date": date, "Entry Type": "Trade Entry", "Side": "sell",
        "Symbol": symbol, "Quantity": -qty, "Price": price, "Amount": qty * price, "Commission": commission,
    }


class TestComputeRealizedPl:
    def test_no_sells_produces_empty_result(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        result = compute_realized_pl(tx)
        assert result.empty
        assert list(result.columns) == ["Symbol", "Trade Date", "Month", "Realized P/L"]
        # Regression: pd.DataFrame([], columns=[...]) defaults every column to dtype=object,
        # which downstream (blended_realized_pl's concat, then dashboard.py's live_trades
        # merge) turned a missing Realized P/L into a literal "None" cell instead of NaN.
        assert result["Realized P/L"].dtype == "float64"

    def test_simple_profit(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        result = compute_realized_pl(tx)
        assert len(result) == 1
        assert result.iloc[0]["Realized P/L"] == pytest.approx(50.0)

    def test_simple_loss(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 20.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        result = compute_realized_pl(tx)
        assert result.iloc[0]["Realized P/L"] == pytest.approx(-50.0)

    def test_average_cost_across_multiple_buys(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),   # avg cost -> 10
            buy("AAA", "2023-01-15", 10, 20.0),   # avg cost -> 15
            sell("AAA", "2023-02-01", 5, 30.0),   # realized = (30-15)*5 = 75
        ])
        result = compute_realized_pl(tx)
        assert result.iloc[0]["Realized P/L"] == pytest.approx(75.0)

    def test_commission_reduces_realized_gain(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 20.0, commission=5.0),
        ])
        result = compute_realized_pl(tx)
        assert result.iloc[0]["Realized P/L"] == pytest.approx(95.0)

    def test_stock_split_rescales_cost_without_realizing_pl(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 100.0),  # qty=10, avg_cost=100
            {  # REMOVE row of a 2-for-1 split
                "Month": "2023-03-01", "Trade Date": "2023-03-01", "Entry Type": "Stock Split",
                "Side": None, "Symbol": "AAA", "Quantity": -10, "Price": 100.0, "Amount": None, "Commission": None,
            },
            {  # ADD row of the same split
                "Month": "2023-03-01", "Trade Date": "2023-03-01", "Entry Type": "Stock Split",
                "Side": None, "Symbol": "AAA", "Quantity": 20, "Price": 50.0, "Amount": None, "Commission": None,
            },
            sell("AAA", "2023-04-01", 20, 60.0),  # avg_cost now 50 -> realized = (60-50)*20 = 200
        ])
        result = compute_realized_pl(tx)
        assert len(result) == 1  # the split itself creates no realized P/L row
        assert result.iloc[0]["Realized P/L"] == pytest.approx(200.0)

    def test_worthless_removal_realizes_full_loss(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 50.0),  # cost basis = 500
            {
                "Month": "2023-06-01", "Trade Date": "2023-06-01", "Entry Type": "ReOrg CA",
                "Side": None, "Symbol": "AAA", "Quantity": -10, "Price": None, "Amount": None, "Commission": None,
            },
        ])
        result = compute_realized_pl(tx)
        assert result.iloc[0]["Realized P/L"] == pytest.approx(-500.0)

    def test_rows_without_symbol_are_excluded(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
            {  # e.g. a margin-interest-style row with no symbol
                "Month": "2023-02-15", "Trade Date": "2023-02-15", "Entry Type": "Journal Entry(Cash)",
                "Side": None, "Symbol": None, "Quantity": None, "Price": None, "Amount": 5.0, "Commission": None,
            },
        ])
        result = compute_realized_pl(tx)
        assert len(result) == 1
        assert set(result["Symbol"]) == {"AAA"}

    def test_symbols_tracked_independently(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("BBB", "2023-01-01", 10, 50.0),
            sell("AAA", "2023-02-01", 10, 12.0),   # realized = (12-10)*10 = 20
            sell("BBB", "2023-02-01", 10, 40.0),   # realized = (40-50)*10 = -100
        ])
        result = compute_realized_pl(tx).set_index("Symbol")["Realized P/L"]
        assert result["AAA"] == pytest.approx(20.0)
        assert result["BBB"] == pytest.approx(-100.0)


class TestComputeFifoRealizedPl:
    def test_no_sells_produces_empty_result(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        result = compute_fifo_realized_pl(tx)
        assert result.empty
        assert list(result.columns) == ["Symbol", "Trade Date", "Month", "Realized P/L", "id"]
        # Regression: pd.DataFrame([], columns=[...]) defaults every column to dtype=object,
        # which downstream (blended_realized_pl's concat, then dashboard.py's live_trades
        # merge) turned a missing Realized P/L into a literal "None" cell instead of NaN --
        # real symptom: a fresh buy with no sells yet showed "None" in the Realized P/L
        # column of the "Since Last Statement" table instead of a blank cell.
        assert result["Realized P/L"].dtype == "float64"

    def test_id_passes_through_from_the_originating_row(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        tx["id"] = [101, 102]
        result = compute_fifo_realized_pl(tx)
        assert result.iloc[0]["id"] == 102  # the sell row's id, not the buy's

    def test_id_is_nan_when_column_absent(self):
        # id is float64 (see compute_fifo_realized_pl's docstring -- needed so it concats
        # cleanly with historical rows that have no id at all), so "missing" is NaN, not None.
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0), sell("AAA", "2023-02-01", 10, 15.0)])
        result = compute_fifo_realized_pl(tx)
        assert pd.isna(result.iloc[0]["id"])

    def test_sell_consumes_oldest_lot_only_not_blended_average(self):
        # buy 10@10, buy 10@20, sell 5 -- FIFO uses the FIRST lot's cost (10),
        # not the blended average (15) compute_realized_pl would use.
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
            sell("AAA", "2023-02-01", 5, 30.0),
        ])
        fifo_result = compute_fifo_realized_pl(tx)
        avg_cost_result = compute_realized_pl(tx)
        assert fifo_result.iloc[0]["Realized P/L"] == pytest.approx((30 - 10) * 5)  # 100
        assert avg_cost_result.iloc[0]["Realized P/L"] == pytest.approx((30 - 15) * 5)  # 75
        assert fifo_result.iloc[0]["Realized P/L"] != avg_cost_result.iloc[0]["Realized P/L"]

    def test_sell_spanning_two_lots_sums_cost_across_them(self):
        # buy 10@10, buy 10@20, sell 15 -- 10 shares @ cost 10, 5 shares @ cost 20
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
            sell("AAA", "2023-02-01", 15, 30.0),
        ])
        result = compute_fifo_realized_pl(tx)
        expected = (30 - 10) * 10 + (30 - 20) * 5
        assert result.iloc[0]["Realized P/L"] == pytest.approx(expected)  # 250

    def test_stock_split_rescales_every_lot_proportionally(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 5, 10.0),   # lot A: 5@10
            buy("AAA", "2023-01-15", 5, 30.0),   # lot B: 5@30
            {  # REMOVE row of a 2-for-1 split
                "Month": "2023-03-01", "Trade Date": "2023-03-01", "Entry Type": "Stock Split",
                "Side": None, "Symbol": "AAA", "Quantity": -10, "Price": None, "Amount": None, "Commission": None,
            },
            {  # ADD row of the same split
                "Month": "2023-03-01", "Trade Date": "2023-03-01", "Entry Type": "Stock Split",
                "Side": None, "Symbol": "AAA", "Quantity": 20, "Price": None, "Amount": None, "Commission": None,
            },
            # post-split: lot A -> 10@5, lot B -> 10@15
            sell("AAA", "2023-04-01", 15, 8.0),  # 10@5 + 5@15 = 50+75=125 cost; proceeds 15*8=120 -> realized -5
        ])
        result = compute_fifo_realized_pl(tx)
        assert len(result) == 1  # the split itself creates no realized P/L row
        assert result.iloc[0]["Realized P/L"] == pytest.approx(120 - (10 * 5 + 5 * 15))  # -5

    def test_reorg_ca_closes_all_lots_as_a_loss(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 5, 10.0),
            buy("AAA", "2023-01-15", 5, 30.0),
            {
                "Month": "2023-06-01", "Trade Date": "2023-06-01", "Entry Type": "ReOrg CA",
                "Side": None, "Symbol": "AAA", "Quantity": -10, "Price": None, "Amount": None, "Commission": None,
            },
        ])
        result = compute_fifo_realized_pl(tx)
        assert result.iloc[0]["Realized P/L"] == pytest.approx(-(5 * 10 + 5 * 30))  # -200

    def test_commission_netted_on_sell_leg_only(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0, commission=2.0),
            sell("AAA", "2023-02-01", 10, 20.0, commission=5.0),
        ])
        result = compute_fifo_realized_pl(tx)
        # buy-side commission folded into lot cost: (10*10+2)/10 = 10.2/share
        # realized = amount(200) - cost_removed(102) - sell_commission(5) = 93
        assert result.iloc[0]["Realized P/L"] == pytest.approx(93.0)

    def test_seed_sourced_lot_consumed_by_a_later_manual_sell(self):
        # 'source' is inert metadata the function never looks at -- only the
        # xlsx-shaped columns matter, so a lot from one origin can be closed
        # out by a trade recorded through a different entry path.
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        tx["source"] = ["seed", "manual"]
        result = compute_fifo_realized_pl(tx)
        assert result.iloc[0]["Realized P/L"] == pytest.approx(50.0)


class TestComputeCurrentPositions:
    def test_no_trades_produces_empty_result(self):
        tx = make_transactions([])
        result = compute_current_positions(tx)
        assert result.empty
        assert list(result.columns) == ["Symbol", "Quantity", "Avg Cost", "Cost Basis"]

    def test_single_open_lot(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        result = compute_current_positions(tx).set_index("Symbol")
        assert result.loc["AAA", "Quantity"] == pytest.approx(10.0)
        assert result.loc["AAA", "Avg Cost"] == pytest.approx(10.0)
        assert result.loc["AAA", "Cost Basis"] == pytest.approx(100.0)

    def test_multiple_open_lots_weighted_average(self):
        # buy 10@10, buy 10@20 -- both lots still open -> weighted avg = (100+200)/20 = 15
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
        ])
        result = compute_current_positions(tx).set_index("Symbol")
        assert result.loc["AAA", "Quantity"] == pytest.approx(20.0)
        assert result.loc["AAA", "Avg Cost"] == pytest.approx(15.0)

    def test_partial_sell_leaves_remaining_lot_at_its_own_cost(self):
        # buy 10@10, buy 10@20, sell 10 -- FIFO consumes the 10@10 lot fully,
        # leaving only the 10@20 lot open -> avg cost is 20, not a blended 15.
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
            sell("AAA", "2023-02-01", 10, 30.0),
        ])
        result = compute_current_positions(tx).set_index("Symbol")
        assert result.loc["AAA", "Quantity"] == pytest.approx(10.0)
        assert result.loc["AAA", "Avg Cost"] == pytest.approx(20.0)

    def test_fully_sold_symbol_is_absent(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        result = compute_current_positions(tx)
        assert result.empty

    def test_mixed_open_and_closed_symbols(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),   # AAA fully closed
            buy("BBB", "2023-01-01", 5, 50.0),      # BBB still open
        ])
        result = compute_current_positions(tx)
        assert list(result["Symbol"]) == ["BBB"]


class TestComputeHoldingPeriodStart:
    def test_never_sold_starts_at_first_buy(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-06-01", 5, 12.0),
        ])
        result = compute_holding_period_start(tx)
        assert result["AAA"] == pd.Timestamp("2023-01-01")

    def test_fully_sold_symbol_is_absent(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        result = compute_holding_period_start(tx)
        assert "AAA" not in result.index

    def test_sold_then_rebought_resets_to_rebuy_date(self):
        # Fully exited on 02-01, rebought on 06-01 -- holding period should start
        # at the rebuy, not the original 01-01 purchase.
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
            buy("AAA", "2023-06-01", 5, 12.0),
        ])
        result = compute_holding_period_start(tx)
        assert result["AAA"] == pd.Timestamp("2023-06-01")

    def test_partial_sell_does_not_reset_start(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 4, 15.0),   # partial sell, still holding 6
        ])
        result = compute_holding_period_start(tx)
        assert result["AAA"] == pd.Timestamp("2023-01-01")

    def test_no_trades_produces_empty_result(self):
        tx = make_transactions([])
        result = compute_holding_period_start(tx)
        assert result.empty


class TestEstimateSellRealizedPl:
    def test_no_position_returns_none(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        assert estimate_sell_realized_pl(tx, "ZZZ", 5, 20.0) is None

    def test_oversell_returns_none(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        assert estimate_sell_realized_pl(tx, "AAA", 11, 20.0) is None

    def test_zero_or_negative_quantity_returns_none(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        assert estimate_sell_realized_pl(tx, "AAA", 0, 20.0) is None
        assert estimate_sell_realized_pl(tx, "AAA", -1, 20.0) is None

    def test_matches_a_real_sell_drawing_from_the_pricier_oldest_lot(self):
        # Mirrors the real NVDY scenario: an old expensive lot plus a cheap
        # recent one push the blended average down, but FIFO still draws the
        # expensive lot first -- the preview should reflect that, not the
        # blended average.
        tx = make_transactions([
            buy("AAA", "2023-01-01", 5, 30.0),   # oldest, expensive
            buy("AAA", "2024-01-01", 5, 10.0),   # newer, cheap -- blended avg = 20
        ])
        preview = estimate_sell_realized_pl(tx, "AAA", 5, 25.0)
        assert preview == pytest.approx((25 - 30) * 5)  # -25, using the oldest lot's cost
        assert preview != pytest.approx((25 - 20) * 5)  # not the blended-average answer

    def test_sell_spanning_two_lots(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
        ])
        preview = estimate_sell_realized_pl(tx, "AAA", 15, 30.0)
        assert preview == pytest.approx((30 - 10) * 10 + (30 - 20) * 5)  # 250

    def test_does_not_match_the_exact_recorded_result_ignoring_commission(self):
        # The preview intentionally ignores commission (not yet known at
        # preview time); compute_fifo_realized_pl's actual recorded result
        # nets it, so the two only agree when commission is zero.
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0)])
        preview = estimate_sell_realized_pl(tx, "AAA", 10, 15.0)
        assert preview == pytest.approx(50.0)


class TestBlendedRealizedPl:
    def make_db_trades(self, rows):
        columns = ["Month", "Trade Date", "Entry Type", "Side", "Symbol", "Quantity", "Price", "Amount", "Commission"]
        df = pd.DataFrame(rows)
        for col in columns:
            if col not in df.columns:
                df[col] = None
        df["Trade Date"] = pd.to_datetime(df["Trade Date"])
        df["Month"] = pd.to_datetime(df["Month"])
        return df[columns]

    def test_historical_side_keeps_average_cost_result_unchanged(self):
        xlsx_tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
            sell("AAA", "2023-02-01", 5, 30.0),
        ])
        xlsx_realized = compute_realized_pl(xlsx_tx)
        cutoff = pd.Timestamp("2023-06-01")
        db_trades = self.make_db_trades([])  # nothing logged live yet
        result = blended_realized_pl(xlsx_realized, db_trades, cutoff)
        # result gains an "id" column (NaN here, since the historical side has no
        # ids) -- compare only xlsx_realized's original columns to isolate the
        # thing this test actually checks: historical values are untouched.
        # check_dtype=False: db_trades is empty here, so pandas infers a "string"
        # dtype for its (unused) Symbol column rather than xlsx_realized's "object"
        # -- a harmless artifact of the empty fixture, not a real behavior difference.
        pd.testing.assert_frame_equal(
            result[xlsx_realized.columns].reset_index(drop=True),
            xlsx_realized.reset_index(drop=True),
            check_dtype=False,
        )
        assert result["id"].isna().all()

    def test_live_side_uses_fifo_after_cutoff(self):
        cutoff = pd.Timestamp("2023-06-01")
        xlsx_realized = compute_realized_pl(make_transactions([]))  # empty historical result
        db_trades = self.make_db_trades([
            buy("AAA", "2023-01-01", 10, 10.0),
            buy("AAA", "2023-01-15", 10, 20.0),
            sell("AAA", "2023-07-01", 5, 30.0),  # after cutoff
        ])
        result = blended_realized_pl(xlsx_realized, db_trades, cutoff)
        assert len(result) == 1
        assert result.iloc[0]["Realized P/L"] == pytest.approx((30 - 10) * 5)  # FIFO, not avg-cost

    def test_cutoff_boundary_is_exact(self):
        # a sell dated exactly on cutoff belongs to the xlsx (historical) side
        cutoff = pd.Timestamp("2023-02-01")
        xlsx_tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0), sell("AAA", "2023-02-01", 10, 15.0)])
        xlsx_realized = compute_realized_pl(xlsx_tx)
        db_trades = self.make_db_trades([buy("AAA", "2023-01-01", 10, 10.0), sell("AAA", "2023-02-01", 10, 15.0)])
        result = blended_realized_pl(xlsx_realized, db_trades, cutoff)
        assert len(result) == 1  # not double-counted from both sides


class TestBlendedDividends:
    def make_income(self, rows):
        df = pd.DataFrame(rows)
        df["Trade Date"] = pd.to_datetime(df["Trade Date"])
        return df[["Symbol", "Trade Date", "Entry Type", "Net Amt"]]

    def test_splits_by_cutoff_with_no_duplication(self):
        cutoff = pd.Timestamp("2023-06-01")
        xlsx_income = self.make_income([
            {"Symbol": "HDV", "Trade Date": "2023-05-01", "Entry Type": "Dividends", "Net Amt": 2.0},
        ])
        db_dividends = self.make_income([
            {"Symbol": "HDV", "Trade Date": "2023-05-01", "Entry Type": "Dividend", "Net Amt": 2.0},  # seeded dup, <= cutoff
            {"Symbol": "HDV", "Trade Date": "2023-07-01", "Entry Type": "Dividend", "Net Amt": 3.0},  # live, > cutoff
        ])
        result = blended_dividends(xlsx_income, db_dividends, cutoff)
        assert len(result) == 2  # the seeded May row is excluded on the db side, not double-counted
        assert set(result["Net Amt"]) == {2.0, 3.0}


class TestComputeRoi:
    def test_basic_roi_over_roughly_one_year(self):
        roi_pct, annualized = compute_roi(investment_gain=1000, capital_base=10000, period_days=365)
        assert roi_pct == pytest.approx(10.0)
        assert annualized == pytest.approx(10.0, abs=0.1)

    def test_doubling_over_two_years_annualizes_to_sqrt(self):
        roi_pct, annualized = compute_roi(investment_gain=10000, capital_base=10000, period_days=730.5)
        assert roi_pct == pytest.approx(100.0)
        assert annualized == pytest.approx(41.42, abs=0.01)

    def test_zero_capital_base_returns_none(self):
        roi_pct, annualized = compute_roi(investment_gain=500, capital_base=0, period_days=100)
        assert roi_pct is None
        assert annualized is None

    def test_negative_capital_base_returns_none(self):
        roi_pct, annualized = compute_roi(investment_gain=500, capital_base=-100, period_days=100)
        assert roi_pct is None
        assert annualized is None

    def test_zero_period_days_skips_annualization_only(self):
        roi_pct, annualized = compute_roi(investment_gain=500, capital_base=1000, period_days=0)
        assert roi_pct == pytest.approx(50.0)
        assert annualized is None

    def test_loss_exceeding_capital_base_skips_annualization(self):
        # roi_pct = -150% -> (1 + roi/100) = -0.5, a negative base can't be
        # raised to a fractional power without going complex, so we bail out.
        roi_pct, annualized = compute_roi(investment_gain=-15000, capital_base=10000, period_days=365)
        assert roi_pct == pytest.approx(-150.0)
        assert annualized is None

    def test_negative_but_valid_roi_still_annualizes(self):
        roi_pct, annualized = compute_roi(investment_gain=-500, capital_base=10000, period_days=365)
        assert roi_pct == pytest.approx(-5.0)
        assert annualized == pytest.approx(-5.0, abs=0.1)


class TestComputePivotPoints:
    def test_matches_hand_computed_levels(self):
        # High 110, Low 90, Pivot 100 -> Pivot passed straight through, clean round numbers.
        result = compute_pivot_points(high=110.0, low=90.0, pivot=100.0)
        assert result["Pivot"] == pytest.approx(100.0)
        assert result["R1"] == pytest.approx(110.0)
        assert result["S1"] == pytest.approx(90.0)
        assert result["R2"] == pytest.approx(120.0)
        assert result["S2"] == pytest.approx(80.0)
        assert result["R3"] == pytest.approx(130.0)
        assert result["S3"] == pytest.approx(70.0)

    def test_wide_range_relative_to_price_gives_negative_s3(self):
        # Real shape from a volatile small-cap (RKLB-like): 90-day High far
        # above Low relative to Pivot -- S3 going negative is a genuine
        # output of the formula, not a bug, so this locks that behavior in
        # rather than treating it as a regression to "fix" later.
        result = compute_pivot_points(high=151.0, low=58.2, pivot=81.75)
        assert result["S3"] < 0
        assert result["S3"] == pytest.approx(58.2 - 2 * (151.0 - 81.75))

    def test_missing_high_low_leaves_pivot_valid_but_derived_levels_nan(self):
        # Matches the real unresolved-symbol shape: market_data.fetch_stock_profile
        # sets High90D/Low90D to NaN together on a fetch failure, but Avg Cost (the
        # pivot basis passed in here) comes from trade history, not the market
        # fetch -- unaffected. Pivot stays a real number (your own cost is still
        # known even when the market data isn't); every level that needs High/Low
        # goes NaN since there's no range to build from. A real improvement over
        # the old (High+Low+Close)/3 average, which would have poisoned Pivot too.
        result = compute_pivot_points(high=float("nan"), low=float("nan"), pivot=100.0)
        assert result["Pivot"] == pytest.approx(100.0)
        assert pd.isna(result["R1"])
        assert pd.isna(result["S1"])
        assert pd.isna(result["R2"])
        assert pd.isna(result["S2"])
        assert pd.isna(result["R3"])
        assert pd.isna(result["S3"])

    def test_nan_pivot_propagates_to_every_derived_level(self):
        result = compute_pivot_points(high=110.0, low=90.0, pivot=float("nan"))
        assert all(pd.isna(v) for v in result.values())

    def test_cost_basis_below_entire_trading_range_gets_clamped_and_resorted(self):
        # Real shape confirmed on RKLB: Pivot (Avg Cost) decoupled from High/Low means
        # it can sit entirely below (or above) the 90-day range. Raw formula: R1 = 2*50
        # - 100 = 0, which is BELOW Pivot (50) -- nonsensical as "resistance." Clamping
        # floors it at Pivot, then resorting (R1<=R2<=R3, S1>=S2>=S3) fixes the internal
        # ordering too, which clamping alone wouldn't -- the 3 raw R candidates here
        # (0, 150, 100) are out of order even before considering the clamp.
        result = compute_pivot_points(high=200.0, low=100.0, pivot=50.0)
        assert result["Pivot"] == pytest.approx(50.0)
        assert result["R1"] == pytest.approx(50.0)  # clamped to Pivot exactly
        assert result["R2"] == pytest.approx(100.0)
        assert result["R3"] == pytest.approx(150.0)
        assert result["S1"] == pytest.approx(-50.0)
        assert result["S2"] == pytest.approx(-100.0)
        assert result["S3"] == pytest.approx(-200.0)

    def test_ordering_invariant_always_holds(self):
        # S3 <= S2 <= S1 <= Pivot <= R1 <= R2 <= R3, regardless of how Pivot relates to
        # High/Low -- the guarantee this clamp+resort step exists to provide.
        for high, low, pivot in [(110.0, 90.0, 100.0), (151.0, 58.2, 81.75), (200.0, 100.0, 50.0), (100.0, 90.0, 500.0)]:
            result = compute_pivot_points(high=high, low=low, pivot=pivot)
            ordered = [result["S3"], result["S2"], result["S1"], result["Pivot"], result["R1"], result["R2"], result["R3"]]
            assert ordered == sorted(ordered)

    def test_well_behaved_case_is_unaffected_by_clamping(self):
        # Pivot already between Low and High -- clamping/resorting should be a no-op,
        # producing the exact same values as the raw classic formula.
        result = compute_pivot_points(high=110.0, low=90.0, pivot=100.0)
        assert result["R1"] == pytest.approx(110.0)
        assert result["R2"] == pytest.approx(120.0)
        assert result["R3"] == pytest.approx(130.0)
        assert result["S1"] == pytest.approx(90.0)
        assert result["S2"] == pytest.approx(80.0)
        assert result["S3"] == pytest.approx(70.0)

    def test_pivot_is_passed_through_unchanged_not_averaged_with_high_low(self):
        # Deliberately NOT the classic floor-trader (High+Low+Close)/3 average --
        # Monitor Stocks relies on Pivot being exactly whatever basis it passed in
        # (Avg Cost), not something recomputed from High/Low.
        result = compute_pivot_points(high=200.0, low=50.0, pivot=90.0)
        assert result["Pivot"] == pytest.approx(90.0)

    def test_vectorizes_over_pandas_series(self):
        high = pd.Series([110.0, 220.0])
        low = pd.Series([90.0, 180.0])
        pivot = pd.Series([100.0, 200.0])
        result = compute_pivot_points(high, low, pivot)
        assert isinstance(result["Pivot"], pd.Series)
        assert result["Pivot"].tolist() == pytest.approx([100.0, 200.0])
        assert result["R1"].tolist() == pytest.approx([110.0, 220.0])

    def test_two_clamped_candidates_produce_an_exactly_identical_mid_not_a_1ulp_drift(self):
        # Regression: real AIQ data (High/Low over a real 1M Timeline window, Avg Cost as
        # Pivot) produced R1 == 47.43141522814266 (== Pivot, correctly clamped) and R2 (the
        # "mid") == 47.43141522814265 -- one ULP below Pivot, breaking the S3<=...<=R3
        # ordering invariant by a sub-cent amount. Root cause: the old sum(candidates) -
        # min - max formula for "mid" doesn't reconstruct an exact float when two of the
        # three raw candidates clamp to precisely the same `pivot` value. These are the
        # exact real values that reproduced it -- caught by a real-data smoke test, not a
        # synthetic case, since round test numbers don't expose this floating-point collision.
        high, low, pivot = 64.16999816894531, 55.91999816894531, 47.43141522814266
        result = compute_pivot_points(high=high, low=low, pivot=pivot)
        ordered = [result["S3"], result["S2"], result["S1"], result["Pivot"], result["R1"], result["R2"], result["R3"]]
        assert ordered == sorted(ordered)
        # Both R1 and R2 clamp to Pivot here -- should be bit-exactly Pivot, not "close to" it.
        assert result["R1"] == pivot
        assert result["R2"] == pivot


def make_ohlc(closes, *, start="2026-01-01"):
    """Build a Date/Open/High/Low/Close DataFrame (fetch_price_history's own shape) from
    a plain list of closes -- Open=prev close (or same on day 1), High/Low = Open/Close
    +/- 1, so every bar has a real (non-degenerate) range without needing to spell out
    all four fields by hand for every test."""
    dates = pd.date_range(start, periods=len(closes), freq="D")
    opens = [closes[0]] + closes[:-1]
    df = pd.DataFrame({
        "Date": dates,
        "Open": opens,
        "Close": closes,
    })
    df["High"] = df[["Open", "Close"]].max(axis=1) + 1
    df["Low"] = df[["Open", "Close"]].min(axis=1) - 1
    return df[["Date", "Open", "High", "Low", "Close"]]


class TestComputeStochasticOscillator:
    def test_first_k_period_minus_one_rows_are_nan(self):
        close = pd.Series([float(i) for i in range(1, 21)])
        result = compute_stochastic_oscillator(close + 1, close - 1, close, k_period=14, d_period=3)
        assert result["%K"].iloc[:13].isna().all()
        assert result["%K"].iloc[13:].notna().all()

    def test_close_at_top_of_range_gives_k_near_100(self):
        high = pd.Series([100.0] * 14)
        low = pd.Series([90.0] * 14)
        close = pd.Series([90.0] * 13 + [100.0])
        result = compute_stochastic_oscillator(high, low, close, k_period=14, d_period=3)
        assert result["%K"].iloc[-1] == pytest.approx(100.0)

    def test_close_at_bottom_of_range_gives_k_near_0(self):
        high = pd.Series([100.0] * 14)
        low = pd.Series([90.0] * 14)
        close = pd.Series([100.0] * 13 + [90.0])
        result = compute_stochastic_oscillator(high, low, close, k_period=14, d_period=3)
        assert result["%K"].iloc[-1] == pytest.approx(0.0)

    def test_flat_window_reads_as_midpoint_not_a_divide_by_zero_error(self):
        high = pd.Series([100.0] * 14)
        low = pd.Series([100.0] * 14)
        close = pd.Series([100.0] * 14)
        result = compute_stochastic_oscillator(high, low, close, k_period=14, d_period=3)
        assert result["%K"].iloc[-1] == pytest.approx(50.0)

    def test_percent_d_is_a_moving_average_of_percent_k(self):
        close = pd.Series([float(i) for i in range(1, 21)])
        result = compute_stochastic_oscillator(close + 1, close - 1, close, k_period=14, d_period=3)
        k, d = result["%K"], result["%D"]
        assert d.iloc[15] == pytest.approx(k.iloc[13:16].mean())


class TestCountTouches:
    def test_bars_crossing_through_the_level_count(self):
        high = pd.Series([105.0, 106.0, 95.0])
        low = pd.Series([95.0, 104.0, 90.0])
        # Bar 0's range (95-105) crosses 100; bar 1 (104-106) doesn't; bar 2 (90-95) doesn't.
        assert count_touches(high, low, 100.0) == 2  # floored at 2 even though only 1 real touch

    def test_near_misses_within_tolerance_count_as_touches(self):
        high = pd.Series([101.1, 101.1, 101.1])
        low = pd.Series([100.5, 100.5, 100.5])
        # 101.1 is within 1.2% of 100 (tolerance = 1.2), so every bar's High counts.
        assert count_touches(high, low, 100.0) == 3

    def test_floors_at_two_when_nothing_real_is_close(self):
        high = pd.Series([200.0, 205.0])
        low = pd.Series([190.0, 195.0])
        assert count_touches(high, low, 100.0) == 2

    def test_larger_tolerance_widens_the_count(self):
        # 4 bars, each 8-10% away from the level -- a tight tolerance floors at 2 (nothing
        # real qualifies), a loose tolerance picks up the real near-misses instead.
        high = pd.Series([108.0, 109.0, 110.0, 108.5])
        low = pd.Series([107.0, 108.0, 109.0, 107.5])
        assert count_touches(high, low, 100.0, tolerance_pct=0.01) == 2  # floored, nothing within 1%
        assert count_touches(high, low, 100.0, tolerance_pct=0.15) == 4  # all 4 within 15%


class TestComputeMovingAverage:
    def test_matches_hand_computed_average(self):
        close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = compute_moving_average(close, period=3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_period_longer_than_series_is_all_nan_not_an_error(self):
        close = pd.Series([1.0, 2.0, 3.0])
        result = compute_moving_average(close, period=200)
        assert result.isna().all()


class TestResampleOhlc:
    def test_day_interval_returns_an_unmutated_copy(self):
        daily = make_ohlc([10.0, 11.0, 12.0])
        result = resample_ohlc(daily, "D")
        pd.testing.assert_frame_equal(result, daily)
        result.loc[0, "Close"] = 999.0
        assert daily.loc[0, "Close"] == pytest.approx(10.0)  # caller's frame untouched

    def test_week_interval_aggregates_ohlc_correctly(self):
        # 14 daily closes -> should collapse into weekly buckets whose Open/High/Low/Close
        # are the standard first/max/min/last aggregation over each bucket's member days.
        daily = make_ohlc([float(i) for i in range(1, 15)], start="2026-01-05")  # a Monday
        weekly = resample_ohlc(daily, "W")
        assert len(weekly) < len(daily)
        assert weekly["Close"].iloc[-1] == pytest.approx(daily["Close"].iloc[-1])
        assert weekly["Open"].iloc[0] == pytest.approx(daily["Open"].iloc[0])
        assert weekly["High"].sum() <= daily["High"].sum()  # fewer, wider bars

    def test_month_interval_aggregates_ohlc_correctly(self):
        daily = make_ohlc([float(i) for i in range(1, 61)], start="2026-01-01")  # ~2 months
        monthly = resample_ohlc(daily, "M")
        assert len(monthly) in (2, 3)
        assert monthly["Close"].iloc[-1] == pytest.approx(daily["Close"].iloc[-1])

    def test_unknown_interval_raises(self):
        daily = make_ohlc([10.0, 11.0])
        with pytest.raises(ValueError):
            resample_ohlc(daily, "min")


class TestToHeikinAshi:
    def test_first_bar_open_is_seeded_from_real_open_close_midpoint(self):
        daily = make_ohlc([10.0, 11.0, 12.0])
        ha = to_heikin_ashi(daily)
        expected_open = (daily["Open"].iloc[0] + daily["Close"].iloc[0]) / 2
        assert ha["Open"].iloc[0] == pytest.approx(expected_open)

    def test_close_is_ohlc_average_of_the_real_bar(self):
        daily = make_ohlc([10.0, 11.0, 12.0])
        ha = to_heikin_ashi(daily)
        row = daily.iloc[1]
        expected_close = (row.Open + row.High + row.Low + row.Close) / 4
        assert ha["Close"].iloc[1] == pytest.approx(expected_close)

    def test_second_bar_open_is_midpoint_of_first_ha_bar(self):
        daily = make_ohlc([10.0, 11.0, 12.0])
        ha = to_heikin_ashi(daily)
        expected_open = (ha["Open"].iloc[0] + ha["Close"].iloc[0]) / 2
        assert ha["Open"].iloc[1] == pytest.approx(expected_open)

    def test_high_low_always_contain_the_ha_body(self):
        daily = make_ohlc([10.0, 14.0, 9.0, 20.0, 5.0])
        ha = to_heikin_ashi(daily)
        for i in range(len(ha)):
            assert ha["Low"].iloc[i] <= min(ha["Open"].iloc[i], ha["Close"].iloc[i])
            assert ha["High"].iloc[i] >= max(ha["Open"].iloc[i], ha["Close"].iloc[i])

    def test_preserves_row_count_and_date_column(self):
        daily = make_ohlc([10.0, 11.0, 12.0, 13.0])
        ha = to_heikin_ashi(daily)
        assert len(ha) == len(daily)
        assert list(ha["Date"]) == list(daily["Date"])


class TestFindSwingPoints:
    def test_confirms_a_swing_high_at_the_peak_of_a_symmetric_v(self):
        high = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0])
        low = pd.Series([0.0] * 9)  # irrelevant to this assertion
        is_high, _ = find_swing_points(high, low, window=3)
        assert list(is_high[is_high]) == [True]
        assert is_high.idxmax() == 4  # the peak, value 5.0

    def test_confirms_a_swing_low_at_the_bottom_of_a_symmetric_v(self):
        high = pd.Series([0.0] * 9)  # irrelevant to this assertion
        low = pd.Series([10.0, 9.0, 8.0, 7.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        _, is_low = find_swing_points(high, low, window=3)
        confirmed = low.index[is_low]
        assert list(confirmed) == [4]  # the trough, value 6.0

    def test_strictly_monotonic_series_has_no_confirmed_swing_highs(self):
        # Every interior bar's local max is always the bar just ahead of it, never itself --
        # a strictly increasing run never produces a confirmed local extreme.
        high = pd.Series([float(i) for i in range(1, 10)])
        low = pd.Series([0.0] * 9)
        is_high, _ = find_swing_points(high, low, window=1)
        assert not is_high.any()

    def test_most_recent_window_bars_are_never_confirmed(self):
        # Even a genuine peak right at the tail end can't be confirmed -- not enough
        # lookahead yet. Real trend lines don't chase the last candle.
        high = pd.Series([1.0, 2.0, 3.0, 10.0, 3.0, 2.0])  # peak at index 3, only 2 bars after it
        low = pd.Series([0.0] * 6)
        is_high, _ = find_swing_points(high, low, window=3)
        assert not is_high.any()


class TestComputeSwingTrendLines:
    def test_connects_the_two_most_recent_swing_highs_and_lows(self):
        # window=1 zigzags -- highs peak at indices 1/3/5/7 (values 2/3/4/2), lows trough at
        # the same indices (values 8/7/6/8). The two MOST RECENT of each: highs at
        # index5 (Jan6, 4.0) and index7 (Jan8, 2.0); lows at index5 (Jan6, 6.0) and index7
        # (Jan8, 8.0).
        dates = pd.Series(pd.date_range("2026-01-01", periods=9, freq="D"))
        high = pd.Series([1.0, 2.0, 1.0, 3.0, 1.0, 4.0, 1.0, 2.0, 1.0])
        low = pd.Series([9.0, 8.0, 9.0, 7.0, 9.0, 6.0, 9.0, 8.0, 9.0])

        result = compute_swing_trend_lines(dates, high, low, window=1)

        assert result["resistance"] == [
            {"time": "2026-01-06", "value": 4.0},
            {"time": "2026-01-08", "value": 2.0},
            {"time": "2026-01-09", "value": 1.0},  # extended: slope -1/day from the last 2 points
        ]
        assert result["support"] == [
            {"time": "2026-01-06", "value": 6.0},
            {"time": "2026-01-08", "value": 8.0},
            {"time": "2026-01-09", "value": 9.0},  # extended: slope +1/day
        ]

    def test_fewer_than_two_swing_points_gives_none_not_an_error(self):
        # Strictly monotonic -- zero confirmed swing highs.
        dates = pd.Series(pd.date_range("2026-01-01", periods=9, freq="D"))
        high = pd.Series([float(i) for i in range(1, 10)])
        low = pd.Series([float(i) for i in range(1, 10)])
        result = compute_swing_trend_lines(dates, high, low, window=1)
        assert result["resistance"] is None
        assert result["support"] is None

    def test_exactly_one_swing_point_still_gives_none(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=7, freq="D"))
        high = pd.Series([1.0, 2.0, 3.0, 10.0, 3.0, 2.0, 1.0])  # single peak at index 3
        low = pd.Series([0.0] * 7)
        result = compute_swing_trend_lines(dates, high, low, window=1)
        assert result["resistance"] is None

    def test_search_from_restricts_eligible_swings_but_detects_with_full_context(self):
        # Same zigzag as the main test above -- swing highs at index 1/3/5/7 (values
        # 2/3/4/2, dates Jan2/Jan4/Jan6/Jan8).
        dates = pd.Series(pd.date_range("2026-01-01", periods=9, freq="D"))
        high = pd.Series([1.0, 2.0, 1.0, 3.0, 1.0, 4.0, 1.0, 2.0, 1.0])
        low = pd.Series([0.0] * 9)

        unrestricted = compute_swing_trend_lines(dates, high, low, window=1)
        assert unrestricted["resistance"][1] == {"time": "2026-01-08", "value": 2.0}

        # search_from=Jan7 excludes the swing at index5 (Jan6) -- only index7 (Jan8)
        # remains eligible, fewer than 2, so the line drops to None even though swing
        # DETECTION still ran with full context (index5 is still a real, confirmed swing
        # point -- find_swing_points would report it True -- it's just not eligible to be
        # picked as one of the "two most recent" for this search_from).
        restricted = compute_swing_trend_lines(dates, high, low, window=1, search_from=dates.iloc[6])
        assert restricted["resistance"] is None

        # A cutoff after every real swing leaves zero eligible.
        too_late = compute_swing_trend_lines(dates, high, low, window=1, search_from=dates.iloc[8])
        assert too_late["resistance"] is None


class TestClusterPriceLevels:
    def test_groups_prices_within_tolerance_and_sorts_by_count_descending(self):
        prices = [100.0, 100.5, 99.8, 200.0, 201.0]
        result = cluster_price_levels(prices, tolerance_pct=0.02)
        assert len(result) == 2
        assert result[0]["count"] == 3
        assert result[0]["price"] == pytest.approx(100.1)
        assert result[1]["count"] == 2
        assert result[1]["price"] == pytest.approx(200.5)

    def test_price_just_outside_tolerance_starts_a_new_cluster(self):
        prices = [100.0, 103.0]  # 3% apart
        result = cluster_price_levels(prices, tolerance_pct=0.01)  # 1% tolerance
        assert len(result) == 2
        assert all(c["count"] == 1 for c in result)

    def test_empty_input_returns_empty_list(self):
        assert cluster_price_levels([]) == []


class TestComputeHorizontalSrZones:
    def test_finds_resistance_clusters_from_repeated_swing_highs(self):
        # Swing highs (window=1) at index 1/4/7 (~100/101/99 -- one cluster) and index
        # 10/13 (~150/151 -- a second, weaker cluster). Low is strictly increasing, so
        # (per TestFindSwingPoints) it produces zero confirmed swing lows.
        dates = pd.Series(pd.date_range("2026-01-01", periods=15, freq="D"))
        high = pd.Series([1.0, 100.0, 1.0, 1.0, 101.0, 1.0, 1.0, 99.0, 1.0, 1.0, 150.0, 1.0, 1.0, 151.0, 1.0])
        low = pd.Series([float(i) for i in range(15)])

        result = compute_horizontal_sr_zones(dates, high, low, window=1, tolerance_pct=0.02, max_per_side=2)

        assert len(result["resistance"]) == 2
        assert result["resistance"][0]["count"] == 3
        assert result["resistance"][0]["price"] == pytest.approx(100.0)
        assert result["resistance"][1]["count"] == 2
        assert result["resistance"][1]["price"] == pytest.approx(150.5)
        assert result["support"] == []

    def test_max_per_side_trims_to_the_strongest_zones(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=15, freq="D"))
        high = pd.Series([1.0, 100.0, 1.0, 1.0, 101.0, 1.0, 1.0, 99.0, 1.0, 1.0, 150.0, 1.0, 1.0, 151.0, 1.0])
        low = pd.Series([float(i) for i in range(15)])

        result = compute_horizontal_sr_zones(dates, high, low, window=1, tolerance_pct=0.02, max_per_side=1)

        assert len(result["resistance"]) == 1
        assert result["resistance"][0]["count"] == 3  # the stronger cluster wins

    def test_singleton_swings_are_not_returned_as_zones(self):
        # Only ONE confirmed swing high anywhere -- never clusters with anything, so it
        # shouldn't be returned as a "zone" (a zone means the same price was tested more
        # than once; a lone swing is already covered by compute_swing_trend_lines).
        dates = pd.Series(pd.date_range("2026-01-01", periods=7, freq="D"))
        high = pd.Series([1.0, 2.0, 3.0, 100.0, 3.0, 2.0, 1.0])
        low = pd.Series([float(i) for i in range(7)])
        result = compute_horizontal_sr_zones(dates, high, low, window=1)
        assert result["resistance"] == []

    def test_search_from_restricts_eligible_swings(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=15, freq="D"))
        high = pd.Series([1.0, 100.0, 1.0, 1.0, 101.0, 1.0, 1.0, 99.0, 1.0, 1.0, 150.0, 1.0, 1.0, 151.0, 1.0])
        low = pd.Series([float(i) for i in range(15)])

        # Restrict to Jan10 onward -- excludes the ~100 cluster (index1/4/7, all before
        # Jan10), leaving only the ~150 cluster (index10/13) eligible.
        result = compute_horizontal_sr_zones(
            dates, high, low, window=1, tolerance_pct=0.02, search_from=dates.iloc[9],
        )
        assert len(result["resistance"]) == 1
        assert result["resistance"][0]["count"] == 2
        assert result["resistance"][0]["price"] == pytest.approx(150.5)


class TestFindNearestLevels:
    def test_picks_the_closest_candidate_on_each_side(self):
        candidates = [90.0, 95.0, 105.0, 110.0]
        resistance, support = find_nearest_levels(100.0, candidates)
        assert resistance == pytest.approx(105.0)
        assert support == pytest.approx(95.0)

    def test_empty_pool_gives_none_on_both_sides(self):
        assert find_nearest_levels(100.0, []) == (None, None)

    def test_only_candidates_above_leaves_support_none(self):
        resistance, support = find_nearest_levels(100.0, [105.0, 110.0])
        assert resistance == pytest.approx(105.0)
        assert support is None

    def test_only_candidates_below_leaves_resistance_none(self):
        resistance, support = find_nearest_levels(100.0, [90.0, 95.0])
        assert resistance is None
        assert support == pytest.approx(95.0)

    def test_candidate_exactly_at_latest_price_counts_as_neither(self):
        # Already reached -- not "ahead to watch for" on either side.
        resistance, support = find_nearest_levels(100.0, [100.0, 105.0, 95.0])
        assert resistance == pytest.approx(105.0)
        assert support == pytest.approx(95.0)


class TestComputeReferenceLines:
    def test_picks_swings_above_and_below_latest_price(self):
        # One clear confirmed swing high (a V-shaped peak at index 3) above latest_price,
        # one clear confirmed swing low (a V-shaped trough at index 3) below it.
        dates = pd.Series(pd.date_range("2026-01-01", periods=7, freq="D"))
        high = pd.Series([1.0, 2.0, 3.0, 130.0, 3.0, 2.0, 1.0])
        low = pd.Series([50.0, 40.0, 30.0, 20.0, 30.0, 40.0, 50.0])

        result = compute_reference_lines(dates, high, low, latest_price=100.0, window=1)

        assert result["resistance"] == pytest.approx([130.0])
        assert result["support"] == pytest.approx([20.0])

    def test_new_high_gives_no_resistance_candidates(self):
        # latest_price above every confirmed swing high -- nothing overhead to reference.
        dates = pd.Series(pd.date_range("2026-01-01", periods=7, freq="D"))
        high = pd.Series([1.0, 2.0, 3.0, 100.0, 3.0, 2.0, 1.0])
        low = pd.Series([1.0, 0.5, 0.2, 0.1, 0.2, 0.5, 1.0])

        result = compute_reference_lines(dates, high, low, latest_price=200.0, window=1)

        assert result["resistance"] == []

    def test_new_low_gives_no_support_candidates(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=7, freq="D"))
        high = pd.Series([10.0, 10.5, 11.0, 12.0, 11.0, 10.5, 10.0])
        low = pd.Series([9.0, 8.0, 7.0, 1.0, 7.0, 8.0, 9.0])

        result = compute_reference_lines(dates, high, low, latest_price=0.5, window=1)

        assert result["support"] == []

    def test_max_per_side_trims_to_the_closest_candidates(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=15, freq="D"))
        high = pd.Series([1.0, 100.0, 1.0, 1.0, 101.0, 1.0, 1.0, 99.0, 1.0, 1.0, 150.0, 1.0, 1.0, 151.0, 1.0])
        low = pd.Series([float(i) for i in range(15)])

        result = compute_reference_lines(dates, high, low, latest_price=95.0, window=1, max_per_side=1)

        assert result["resistance"] == pytest.approx([99.0])  # closest of 99/100/101/150/151

    def test_fewer_than_max_per_side_swings_returns_what_exists(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=7, freq="D"))
        high = pd.Series([1.0, 2.0, 3.0, 100.0, 3.0, 2.0, 1.0])
        low = pd.Series([1.0, 0.5, 0.2, 0.1, 0.2, 0.5, 1.0])

        result = compute_reference_lines(dates, high, low, latest_price=0.0, window=1, max_per_side=2)

        assert result["resistance"] == pytest.approx([100.0])

    def test_search_from_restricts_eligible_swings(self):
        dates = pd.Series(pd.date_range("2026-01-01", periods=15, freq="D"))
        high = pd.Series([1.0, 100.0, 1.0, 1.0, 101.0, 1.0, 1.0, 99.0, 1.0, 1.0, 150.0, 1.0, 1.0, 151.0, 1.0])
        low = pd.Series([float(i) for i in range(15)])

        # Restrict to Jan10 onward -- excludes the ~100 cluster (index1/4/7, all before
        # Jan10), leaving only the 150/151 highs eligible.
        result = compute_reference_lines(
            dates, high, low, latest_price=95.0, window=1, search_from=dates.iloc[9],
        )
        assert result["resistance"] == pytest.approx([150.0, 151.0])


class TestNearestReferenceCell:
    def test_empty_lines_shows_a_dash(self):
        assert nearest_reference_cell([], "resistance", 100.0) == {"text": "—", "passed": False, "passed_at": None}

    def test_live_resistance_reading_shows_price_and_positive_pct(self):
        lines = [{"price": 105.0, "passed_at": None}]
        result = nearest_reference_cell(lines, "resistance", 100.0)
        assert result == {"text": "$105.00 (+5.0%)", "passed": False, "passed_at": None}

    def test_live_support_reading_shows_price_and_negative_pct(self):
        lines = [{"price": 95.0, "passed_at": None}]
        result = nearest_reference_cell(lines, "support", 100.0)
        assert result == {"text": "$95.00 (-5.0%)", "passed": False, "passed_at": None}

    def test_picks_the_nearest_of_two_resistance_candidates(self):
        lines = [{"price": 110.0, "passed_at": None}, {"price": 105.0, "passed_at": None}]
        result = nearest_reference_cell(lines, "resistance", 100.0)
        assert result["text"] == "$105.00 (+5.0%)"

    def test_picks_the_nearest_of_two_support_candidates(self):
        lines = [{"price": 90.0, "passed_at": None}, {"price": 95.0, "passed_at": None}]
        result = nearest_reference_cell(lines, "support", 100.0)
        assert result["text"] == "$95.00 (-5.0%)"

    def test_passed_line_still_updates_its_live_pct_and_returns_a_real_timestamp(self):
        lines = [{"price": 105.0, "passed_at": "2026-08-20"}]
        # latest_price has moved past 105 -- the % keeps updating live (now negative,
        # since a passed resistance's price is now below current price). passed_at comes
        # back as a real pd.Timestamp (not baked into text), for the caller's own
        # sortable date column.
        result = nearest_reference_cell(lines, "resistance", 130.0)
        assert result["text"] == "$105.00 (-19.2%)"
        assert result["passed"] is True
        assert result["passed_at"] == pd.Timestamp("2026-08-20")

    def test_nan_passed_at_is_treated_as_not_passed(self):
        # fetch_reference_lines() round-trips a SQL NULL through pandas as NaN, not None.
        lines = [{"price": 105.0, "passed_at": float("nan")}]
        result = nearest_reference_cell(lines, "resistance", 100.0)
        assert result["passed"] is False


def _live_row(symbol, **overrides):
    row = {
        "Symbol": symbol, "Description": f"{symbol} Inc.", "Sector": "Technology",
        "Industry": "Software", "Quote Type": "EQUITY", "Beta": 1.1,
        "History90D": [10.0, 11.0, 12.0], "Latest Price": 12.0, "High90D": 13.0, "Low90D": 9.0,
        "Dividend Per Year": 0.5, "Dividend Yield %": 4.2, "Dividend Frequency": "Quarterly",
        "Ex-Date": pd.Timestamp("2026-06-01"),
    }
    row.update(overrides)
    return row


def _failed_live_row(symbol):
    # Exact shape fetch_stock_profile()'s own except branch produces.
    return {
        "Symbol": symbol, "Description": None, "Sector": None, "Industry": None,
        "Quote Type": None, "Beta": float("nan"), "History90D": [], "Latest Price": float("nan"),
        "High90D": float("nan"), "Low90D": float("nan"), "Dividend Per Year": float("nan"),
        "Dividend Yield %": float("nan"), "Dividend Frequency": None, "Ex-Date": pd.NaT,
    }


def _cached_row(symbol, **overrides):
    row = {
        "Symbol": symbol, "Description": f"{symbol} Inc. (cached)", "Sector": "Technology",
        "Industry": "Software", "Quote Type": "EQUITY", "Beta": 1.0,
        "Latest Price": 11.0, "High90D": 12.0, "Low90D": 8.0, "Dividend Per Year": 0.4,
        "Dividend Yield %": 3.6, "Dividend Frequency": "Quarterly", "Ex-Date": pd.Timestamp("2026-05-01"),
        "History90D": [9.0, 10.0, 11.0], "Fetched At": pd.Timestamp("2026-08-14 12:00:00"),
    }
    row.update(overrides)
    return row


class TestApplyMarketProfileFallback:
    def test_a_row_that_succeeded_live_is_returned_untouched_and_not_stale(self):
        live = pd.DataFrame([_live_row("AAPL")])
        cached = pd.DataFrame([_cached_row("AAPL")])
        result = apply_market_profile_fallback(live, cached)
        row = result.iloc[0]
        assert bool(row["Stale"]) is False
        assert row["Latest Price"] == 12.0  # the LIVE value, not the cached 11.0
        assert pd.isna(row["Fetched At"])

    def test_a_failed_row_with_a_cached_counterpart_falls_back_and_is_marked_stale(self):
        live = pd.DataFrame([_failed_live_row("AAPL")])
        cached = pd.DataFrame([_cached_row("AAPL")])
        result = apply_market_profile_fallback(live, cached)
        row = result.iloc[0]
        assert bool(row["Stale"]) is True
        assert row["Latest Price"] == 11.0
        assert row["Description"] == "AAPL Inc. (cached)"
        assert row["History90D"] == [9.0, 10.0, 11.0]
        assert row["Fetched At"] == pd.Timestamp("2026-08-14 12:00:00")

    def test_a_failed_row_with_nothing_ever_cached_stays_blank_not_a_regression(self):
        live = pd.DataFrame([_failed_live_row("NEWSYM")])
        cached = pd.DataFrame([_cached_row("AAPL")])  # cache has data, just not for this symbol
        result = apply_market_profile_fallback(live, cached)
        row = result.iloc[0]
        assert bool(row["Stale"]) is False
        assert pd.isna(row["Latest Price"])
        assert row["Description"] is None

    def test_empty_cache_never_crashes_and_never_falls_back(self):
        live = pd.DataFrame([_failed_live_row("AAPL")])
        cached = pd.DataFrame(columns=["Symbol", "Latest Price", "Fetched At"])
        result = apply_market_profile_fallback(live, cached)
        assert bool(result.iloc[0]["Stale"]) is False

    def test_mixed_batch_each_row_resolved_independently(self):
        live = pd.DataFrame([
            _live_row("GOOD"),
            _failed_live_row("HASFALLBACK"),
            _failed_live_row("NOTHINGCACHED"),
        ])
        cached = pd.DataFrame([_cached_row("HASFALLBACK")])
        result = apply_market_profile_fallback(live, cached).set_index("Symbol")
        assert bool(result.loc["GOOD", "Stale"]) is False
        assert bool(result.loc["HASFALLBACK", "Stale"]) is True
        assert result.loc["HASFALLBACK", "Latest Price"] == 11.0
        assert bool(result.loc["NOTHINGCACHED", "Stale"]) is False
        assert pd.isna(result.loc["NOTHINGCACHED", "Latest Price"])


class TestDescribeMarketProfileFreshness:
    def test_all_same_timestamp_has_no_variance(self):
        same = pd.Timestamp("2026-08-15 09:15:00")
        fetched_at = pd.Series([same, same, same])
        symbols = pd.Series(["AAPL", "BLK", "RKLB"])
        result = describe_market_profile_freshness(fetched_at, symbols)
        assert result["newest"] == same
        assert result["oldest"] == same
        assert result["has_variance"] is False

    def test_single_symbol_has_no_variance(self):
        result = describe_market_profile_freshness(
            pd.Series([pd.Timestamp("2026-08-15 09:15:00")]), pd.Series(["AAPL"]),
        )
        assert result["has_variance"] is False

    def test_a_same_batch_capture_a_few_seconds_apart_is_not_a_false_outlier(self):
        # Regression: caught live against a real 52-symbol batch -- a strict `!=`
        # comparison flagged a symbol captured mere seconds before its neighbors as
        # "the outlier", which reads as a meaningless warning to a human.
        fetched_at = pd.Series([
            pd.Timestamp("2026-08-15 09:15:47"),
            pd.Timestamp("2026-08-15 09:15:03"),  # 44 seconds earlier -- same batch
        ])
        symbols = pd.Series(["AAPL", "AIQ"])
        result = describe_market_profile_freshness(fetched_at, symbols)
        assert result["has_variance"] is False

    def test_a_gap_just_over_the_tolerance_does_count_as_variance(self):
        fetched_at = pd.Series([
            pd.Timestamp("2026-08-15 09:15:00"),
            pd.Timestamp("2026-08-15 09:09:00"),  # 6 minutes earlier -- past the 5-minute default
        ])
        result = describe_market_profile_freshness(fetched_at, pd.Series(["AAPL", "AIQ"]))
        assert result["has_variance"] is True

    def test_one_outlier_is_identified_by_symbol_and_date(self):
        fetched_at = pd.Series([
            pd.Timestamp("2026-08-15 09:15:00"),
            pd.Timestamp("2026-08-15 09:15:00"),
            pd.Timestamp("2026-08-10 14:22:00"),  # RKLB, the real outlier
        ])
        symbols = pd.Series(["AAPL", "BLK", "RKLB"])
        result = describe_market_profile_freshness(fetched_at, symbols)
        assert result["newest"] == pd.Timestamp("2026-08-15 09:15:00")
        assert result["oldest"] == pd.Timestamp("2026-08-10 14:22:00")
        assert result["oldest_symbol"] == "RKLB"
        assert result["has_variance"] is True

    def test_uniformly_stale_batch_still_reports_newest_even_with_no_outlier(self):
        # Nobody's clicked Refresh in days -- every symbol shares the same OLD
        # timestamp. No single outlier to name, but the caption's main line still
        # needs `newest` to read as clearly stale.
        old = pd.Timestamp("2026-08-10 14:22:00")
        result = describe_market_profile_freshness(
            pd.Series([old, old]), pd.Series(["AAPL", "BLK"]),
        )
        assert result["newest"] == old
        assert result["has_variance"] is False

    def test_nat_rows_are_ignored(self):
        fetched_at = pd.Series([
            pd.Timestamp("2026-08-15 09:15:00"), pd.NaT, pd.Timestamp("2026-08-15 09:15:00"),
        ])
        symbols = pd.Series(["AAPL", "NEVERCAPTURED", "BLK"])
        result = describe_market_profile_freshness(fetched_at, symbols)
        assert result["has_variance"] is False
        assert result["newest"] == pd.Timestamp("2026-08-15 09:15:00")

    def test_empty_input_returns_nat_and_no_variance(self):
        result = describe_market_profile_freshness(pd.Series([], dtype="datetime64[ns]"), pd.Series([], dtype=object))
        assert pd.isna(result["newest"])
        assert pd.isna(result["oldest"])
        assert result["oldest_symbol"] is None
        assert result["has_variance"] is False

    def test_all_nat_returns_nat_and_no_variance(self):
        result = describe_market_profile_freshness(pd.Series([pd.NaT, pd.NaT]), pd.Series(["AAPL", "BLK"]))
        assert pd.isna(result["newest"])
        assert result["has_variance"] is False
