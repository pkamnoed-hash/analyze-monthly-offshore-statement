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


def _profile(rows: list[dict]) -> pd.DataFrame:
    """Builds a minimal profile DataFrame in the shape get_dividend_holdings() expects
    (same columns market_data.fetch_stock_profile()/db.fetch_market_profile_cache()
    return) -- v4.9.1, get_dividend_holdings() takes this as a parameter instead of
    fetching live itself, so tests build it directly instead of faking yfinance.
    dtype="object" for an empty `rows` -- same reasoning core/db.py's own empty-frame
    builders use: a plain [] otherwise defaults to float64, and get_dividend_holdings()'s
    .merge(profile, on="Symbol", ...) needs a real "Symbol" column to merge on even with
    zero rows."""
    if not rows:
        return pd.DataFrame({
            "Symbol": pd.Series([], dtype="object"),
            "Sector": pd.Series([], dtype="object"),
            "Industry": pd.Series([], dtype="object"),
            "Quote Type": pd.Series([], dtype="object"),
            "Latest Price": pd.Series([], dtype="float64"),
            "Dividend Yield %": pd.Series([], dtype="float64"),
        })
    defaults = {"Sector": None, "Industry": None, "Quote Type": "EQUITY", "Dividend Yield %": 0.0}
    return pd.DataFrame([{**defaults, **row} for row in rows])


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

        profile = _profile([{"Symbol": "AAA", "Sector": "Tech", "Latest Price": 100.0}])
        result = rebalance.get_dividend_holdings(profile, conn=conn)

        assert list(result["Symbol"]) == ["AAA"]

    def test_computes_current_value_and_cat_weight_pct(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="BBB", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        db.set_symbol_type("BBB", "Dividend", conn=conn)

        profile = _profile([
            {"Symbol": "AAA", "Sector": "Tech", "Latest Price": 150.0},  # value 1500
            {"Symbol": "BBB", "Sector": "Health", "Latest Price": 50.0},  # value 500
        ])
        result = rebalance.get_dividend_holdings(profile, conn=conn).set_index("Symbol")

        assert result.loc["AAA", "Current Value"] == pytest.approx(1500.0)
        assert result.loc["BBB", "Current Value"] == pytest.approx(500.0)
        # total value 2000 -- AAA is 75%, BBB is 25%
        assert result.loc["AAA", "Current Cat Weight %"] == pytest.approx(75.0)
        assert result.loc["BBB", "Current Cat Weight %"] == pytest.approx(25.0)

    def test_computes_unrealized_dollar_and_pct(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        profile = _profile([{"Symbol": "AAA", "Sector": "Tech", "Latest Price": 120.0}])
        result = rebalance.get_dividend_holdings(profile, conn=conn).iloc[0]

        # Cost Basis 1000, Current Value 1200 -> unrealized $200, 20%
        assert result["Current Unrealized $"] == pytest.approx(200.0)
        assert result["Current Unrealized %"] == pytest.approx(20.0)

    def test_computes_expected_dividend_net_of_withholding(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        profile = _profile([
            {"Symbol": "AAA", "Sector": "Tech", "Latest Price": 100.0, "Dividend Yield %": 4.0},
        ])
        result = rebalance.get_dividend_holdings(profile, conn=conn).iloc[0]

        # Current Value 1000, yield 4% gross -> $40 gross/yr, net of 15% withholding -> $34/yr
        assert result["Current Expected Div/Yr"] == pytest.approx(34.0)
        assert result["Current Expected Div/Mo"] == pytest.approx(34.0 / 12)

    def test_computes_expected_dividend_pct_net_of_withholding(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        profile = _profile([
            {"Symbol": "AAA", "Sector": "Tech", "Latest Price": 100.0, "Dividend Yield %": 4.0},
        ])
        result = rebalance.get_dividend_holdings(profile, conn=conn).iloc[0]

        # 4% gross yield, net of 15% withholding -> 3.4% -- quantity-independent, unlike
        # Current Div Contrib % (which also factors in Cat Weight %).
        assert result["Current Expected Div/Yr %"] == pytest.approx(3.4)

    def test_classification_uses_sector_for_equity_and_industry_for_non_equity(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=100.0, conn=conn)
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="BBB", quantity=1, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        db.set_symbol_type("BBB", "Dividend", conn=conn)
        profile = _profile([
            {"Symbol": "AAA", "Quote Type": "EQUITY", "Sector": "Tech", "Industry": "Software", "Latest Price": 100.0},
            {"Symbol": "BBB", "Quote Type": "ETF", "Sector": None, "Industry": "Bond", "Latest Price": 100.0},
        ])
        result = rebalance.get_dividend_holdings(profile, conn=conn).set_index("Symbol")

        assert result.loc["AAA", "Classification"] == "Tech"
        assert result.loc["BBB", "Classification"] == "Bond"

    def test_returns_empty_frame_with_no_dividend_holdings(self, conn):
        result = rebalance.get_dividend_holdings(_profile([]), conn=conn)
        assert result.empty

    def test_computes_div_contrib_pct(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        profile = _profile([
            {"Symbol": "AAA", "Sector": "Tech", "Latest Price": 100.0, "Dividend Yield %": 4.0},
        ])
        result = rebalance.get_dividend_holdings(profile, conn=conn).iloc[0]

        # Sole holding -> Cat Weight % = 100 -> Contrib % = 100/100 * 4.0 * 0.85 = 3.4
        assert result["Current Div Contrib %"] == pytest.approx(3.4)


class TestApplyAllocation:
    def _holdings(self):
        return pd.DataFrame([
            {"Symbol": "AAA", "Quantity": 10.0, "Cost Basis": 1000.0, "Latest Price": 120.0,
             "Dividend Yield %": 4.0, "Classification": "Tech", "Dividends Received": 50.0},
            {"Symbol": "BBB", "Quantity": 10.0, "Cost Basis": 500.0, "Latest Price": 50.0,
             "Dividend Yield %": 2.0, "Classification": "Health", "Dividends Received": 0.0},
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

    def test_new_expected_div_yr_pct_equals_current_regardless_of_investment(self):
        # A stock's own yield rate doesn't change just because you bought more of it at
        # market price -- New Expected Div/Yr % should be numerically unchanged from
        # Current (4% gross x 0.85 = 3.4%, same for AAA whether or not it's invested in).
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 100}).set_index("Symbol")
        assert result.loc["AAA", "New Expected Div/Yr %"] == pytest.approx(3.4)
        assert result.loc["BBB", "New Expected Div/Yr %"] == pytest.approx(1.7)  # 2% x 0.85

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

    def test_new_total_pl_equals_new_unrealized_plus_dividends_received(self):
        result = rebalance.apply_allocation(self._holdings(), 1000.0, {"AAA": 100}).set_index("Symbol")
        assert result.loc["AAA", "New Total P/L"] == pytest.approx(
            result.loc["AAA", "New Unrealized $"] + 50.0
        )

    def test_new_total_pl_dollar_equals_current_total_pl_dollar(self):
        # Buying more at market price doesn't change Unrealized $ or Dividends Received --
        # New Total P/L should be numerically unchanged from Current Total P/L.
        holdings = self._holdings()
        result = rebalance.apply_allocation(holdings, 1000.0, {"AAA": 100}).set_index("Symbol")
        current_total_pl = (10.0 * 120.0 - 1000.0) + 50.0  # Current Unrealized $ + Dividends Received
        assert result.loc["AAA", "New Total P/L"] == pytest.approx(current_total_pl)

    def test_new_total_pl_pct_moves_toward_zero_as_cost_basis_grows(self):
        holdings = self._holdings()
        result = rebalance.apply_allocation(holdings, 1000.0, {"AAA": 100}).set_index("Symbol")
        current_pct = 250.0 / 1000.0 * 100  # (200 unrealized + 50 dividends) / cost basis
        assert result.loc["AAA", "New Total P/L %"] < current_pct

    def test_zero_cost_basis_gives_nan_total_pl_pct_not_a_crash(self):
        holdings = pd.DataFrame([
            {"Symbol": "AAA", "Quantity": 0.0, "Cost Basis": 0.0, "Latest Price": 100.0,
             "Dividend Yield %": 0.0, "Classification": "Tech", "Dividends Received": 0.0},
        ])
        result = rebalance.apply_allocation(holdings, 0.0, {"AAA": 100}).iloc[0]
        assert pd.isna(result["New Total P/L %"])


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
