import pandas as pd
import pytest

from calculations import compute_realized_pl, compute_roi


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
