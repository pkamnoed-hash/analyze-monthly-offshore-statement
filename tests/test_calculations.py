import pandas as pd
import pytest

from calculations import (
    blended_dividends,
    blended_realized_pl,
    compute_current_positions,
    compute_fifo_realized_pl,
    compute_realized_pl,
    compute_roi,
    estimate_sell_realized_pl,
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

    def test_id_passes_through_from_the_originating_row(self):
        tx = make_transactions([
            buy("AAA", "2023-01-01", 10, 10.0),
            sell("AAA", "2023-02-01", 10, 15.0),
        ])
        tx["id"] = [101, 102]
        result = compute_fifo_realized_pl(tx)
        assert result.iloc[0]["id"] == 102  # the sell row's id, not the buy's

    def test_id_is_none_when_column_absent(self):
        tx = make_transactions([buy("AAA", "2023-01-01", 10, 10.0), sell("AAA", "2023-02-01", 10, 15.0)])
        result = compute_fifo_realized_pl(tx)
        assert result.iloc[0]["id"] is None

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
