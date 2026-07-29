import sqlite3

import pandas as pd
import pytest

from core import db, rebalance


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


class FakeTicker:
    def __init__(self, info=None, history_df=None, dividends=None):
        self.info = info or {}
        self._history_df = history_df
        self.dividends = dividends if dividends is not None else pd.Series([], dtype=float)

    def history(self, start=None, end=None):
        return self._history_df


class FakeYfModule:
    def __init__(self, tickers: dict):
        self._tickers = tickers

    def Ticker(self, symbol):
        return self._tickers[symbol]


def _history(closes):
    return pd.DataFrame({"Close": closes})


def _dividends(payouts: dict):
    """payouts: {days_ago: amount} -- matches yfinance's real Ticker.dividends shape."""
    today = pd.Timestamp.today().normalize()
    index = [today - pd.Timedelta(days=d) for d in payouts]
    return pd.Series(list(payouts.values()), index=pd.DatetimeIndex(index))


class TestGetDividendHoldings:
    def test_includes_only_dividend_classified_symbols_currently_held(self, conn):
        # AAA: Dividend, still held. BBB: Growth, still held. CCC: Dividend, fully sold.
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="BBB", quantity=5, price=50.0, conn=conn)
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="CCC", quantity=1, price=10.0, conn=conn)
        db.insert_trade(trade_date="2026-01-06", side="sell", symbol="CCC", quantity=1, price=10.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        db.set_symbol_type("BBB", "Growth", conn=conn)
        db.set_symbol_type("CCC", "Dividend", conn=conn)

        yf_module = FakeYfModule({
            "AAA": FakeTicker(info={"quoteType": "EQUITY", "sector": "Tech"}, history_df=_history([100.0])),
        })
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=yf_module)

        assert list(result["Symbol"]) == ["AAA"]

    def test_computes_current_value_and_cat_weight_pct(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="BBB", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        db.set_symbol_type("BBB", "Dividend", conn=conn)

        yf_module = FakeYfModule({
            "AAA": FakeTicker(info={"quoteType": "EQUITY", "sector": "Tech"}, history_df=_history([150.0])),  # value 1500
            "BBB": FakeTicker(info={"quoteType": "EQUITY", "sector": "Health"}, history_df=_history([50.0])),  # value 500
        })
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=yf_module).set_index("Symbol")

        assert result.loc["AAA", "Current Value"] == pytest.approx(1500.0)
        assert result.loc["BBB", "Current Value"] == pytest.approx(500.0)
        # total value 2000 -- AAA is 75%, BBB is 25%
        assert result.loc["AAA", "Current Cat Weight %"] == pytest.approx(75.0)
        assert result.loc["BBB", "Current Cat Weight %"] == pytest.approx(25.0)

    def test_computes_unrealized_dollar_and_pct(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        yf_module = FakeYfModule({
            "AAA": FakeTicker(info={"quoteType": "EQUITY", "sector": "Tech"}, history_df=_history([120.0])),
        })
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=yf_module).iloc[0]

        # Cost Basis 1000, Current Value 1200 -> unrealized $200, 20%
        assert result["Current Unrealized $"] == pytest.approx(200.0)
        assert result["Current Unrealized %"] == pytest.approx(20.0)

    def test_computes_expected_dividend_net_of_withholding(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        yf_module = FakeYfModule({
            "AAA": FakeTicker(
                info={"quoteType": "EQUITY", "sector": "Tech"},
                history_df=_history([100.0]),
                dividends=_dividends({10: 4.0}),  # $4/share trailing 12mo -> yield 4%
            ),
        })
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=yf_module).iloc[0]

        # Current Value 1000, yield 4% gross -> $40 gross/yr, net of 15% withholding -> $34/yr
        assert result["Current Expected Div/Yr"] == pytest.approx(34.0)
        assert result["Current Expected Div/Mo"] == pytest.approx(34.0 / 12)

    def test_classification_uses_sector_for_equity_and_industry_for_non_equity(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=100.0, conn=conn)
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="BBB", quantity=1, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        db.set_symbol_type("BBB", "Dividend", conn=conn)
        yf_module = FakeYfModule({
            "AAA": FakeTicker(info={"quoteType": "EQUITY", "sector": "Tech", "industry": "Software"}, history_df=_history([100.0])),
            "BBB": FakeTicker(info={"quoteType": "ETF", "sector": None, "category": "Bond"}, history_df=_history([100.0])),
        })
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=yf_module).set_index("Symbol")

        assert result.loc["AAA", "Classification"] == "Tech"
        assert result.loc["BBB", "Classification"] == "Bond"

    def test_returns_empty_frame_with_no_dividend_holdings(self, conn):
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=FakeYfModule({}))
        assert result.empty

    def test_computes_div_contrib_pct(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        yf_module = FakeYfModule({
            "AAA": FakeTicker(
                info={"quoteType": "EQUITY", "sector": "Tech"},
                history_df=_history([100.0]),
                dividends=_dividends({10: 4.0}),  # yield 4%
            ),
        })
        result = rebalance.get_dividend_holdings(conn=conn, yf_module=yf_module).iloc[0]

        # Sole holding -> Cat Weight % = 100 -> Contrib % = 100/100 * 4.0 * 0.85 = 3.4
        assert result["Current Div Contrib %"] == pytest.approx(3.4)


class TestApplyAllocation:
    def _holdings(self):
        return pd.DataFrame([
            {"Symbol": "AAA", "Quantity": 10.0, "Cost Basis": 1000.0, "Latest Price": 120.0,
             "Dividend Yield %": 4.0, "Classification": "Tech"},
            {"Symbol": "BBB", "Quantity": 10.0, "Cost Basis": 500.0, "Latest Price": 50.0,
             "Dividend Yield %": 2.0, "Classification": "Health"},
        ])

    def test_invest_amount_split_by_pct(self):
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 70, "BBB": 30}).set_index("Symbol")
        assert result.loc["AAA", "Invest $"] == pytest.approx(700.0)
        assert result.loc["BBB", "Invest $"] == pytest.approx(300.0)
        assert result.loc["AAA", "New Quantity"] == pytest.approx(10.0 + 700.0 / 120.0)

    def test_missing_symbol_in_pct_defaults_to_zero(self):
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 100}).set_index("Symbol")
        assert result.loc["BBB", "Invest $"] == pytest.approx(0.0)
        assert result.loc["BBB", "New Value"] == pytest.approx(result.loc["BBB", "Quantity"] * 50.0)

    def test_new_unrealized_dollar_equals_current_unrealized_dollar(self):
        # Buying more at the market price contributes zero unrealized gain/loss --
        # New Unrealized $ should be numerically unchanged from before the buy.
        holdings = self._holdings()
        result = rebalance.apply_allocation(holdings, 1000.0, {"AAA": 100}).set_index("Symbol")
        current_unrealized = 10.0 * 120.0 - 1000.0  # Current Value - Cost Basis = 200
        assert result.loc["AAA", "New Unrealized $"] == pytest.approx(current_unrealized)

    def test_new_unrealized_pct_moves_toward_zero_as_cost_basis_grows(self):
        holdings = self._holdings()
        result = rebalance.apply_allocation(holdings, 1000.0, {"AAA": 100}).set_index("Symbol")
        current_pct = 200.0 / 1000.0 * 100  # 20%
        assert result.loc["AAA", "New Unrealized %"] < current_pct

    def test_zero_amount_leaves_value_and_dividend_columns_equal_to_current(self):
        result = rebalance.apply_allocation(self._holdings(), 0.0, {"AAA": 100}).set_index("Symbol")
        assert result.loc["AAA", "New Value"] == pytest.approx(10.0 * 120.0)
        assert result.loc["AAA", "New Expected Div/Yr"] == pytest.approx(
            10.0 * 120.0 * 0.04 * (1 - rebalance.WITHHOLDING_TAX_RATE)
        )

    def test_new_cat_weight_pct_sums_to_100(self):
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 50, "BBB": 50})
        assert result["New Cat Weight %"].sum() == pytest.approx(100.0)

    def test_new_div_contrib_pct_matches_formula(self):
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 100}).set_index("Symbol")
        expected = result.loc["AAA", "New Cat Weight %"] / 100 * 4.0 * (1 - rebalance.WITHHOLDING_TAX_RATE)
        assert result.loc["AAA", "New Div Contrib %"] == pytest.approx(expected)

    def test_div_contrib_pct_sums_to_basket_blended_yield(self):
        # Summing New Div Contrib % across every row should reproduce the whole basket's
        # blended yield: Total New Expected Div/Yr / Total New Value x 100 -- the property
        # the "under the table" summary metric relies on.
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 60, "BBB": 40})
        blended_yield = result["New Expected Div/Yr"].sum() / result["New Value"].sum() * 100
        assert result["New Div Contrib %"].sum() == pytest.approx(blended_yield)


class TestSectorBreakdown:
    def test_groups_by_classification_and_sums_to_100(self):
        holdings = pd.DataFrame([
            {"Classification": "Tech", "Current Value": 700.0},
            {"Classification": "Tech", "Current Value": 300.0},
            {"Classification": "Health", "Current Value": 1000.0},
        ])
        result = rebalance.sector_breakdown(holdings, "Current Value")
        assert result["Tech"] == pytest.approx(50.0)
        assert result["Health"] == pytest.approx(50.0)
        assert result.sum() == pytest.approx(100.0)

    def test_empty_holdings_returns_empty_series(self):
        holdings = pd.DataFrame({"Classification": [], "Current Value": []})
        result = rebalance.sector_breakdown(holdings, "Current Value")
        assert result.empty
