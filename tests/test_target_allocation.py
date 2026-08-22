import numpy as np
import pandas as pd
import pytest

from core import target_allocation


def make_transactions(rows):
    """Same shape/convention as tests/test_calculations.py's own helper --
    kept file-local per this repo's existing per-test-file duplication
    convention rather than shared across test files."""
    columns = ["Month", "Trade Date", "Entry Type", "Side", "Symbol", "Quantity", "Price", "Amount", "Commission"]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    df["Month"] = pd.to_datetime(df["Month"])
    return df[columns]


def buy(symbol, date, qty, price):
    return {
        "Month": date, "Trade Date": date, "Entry Type": "Trade Entry", "Side": "buy",
        "Symbol": symbol, "Quantity": qty, "Price": price, "Amount": -qty * price,
    }


def profile_row(symbol, latest_price, *, sector=None, industry=None, quote_type="EQUITY"):
    return {
        "Symbol": symbol, "Latest Price": latest_price, "Sector": sector,
        "Industry": industry, "Quote Type": quote_type,
    }


class TestComputeActualWeights:
    def test_actual_pct_sums_to_100_across_holdings(self):
        trades = make_transactions([buy("AAA", "2026-01-01", 10, 10.0), buy("BBB", "2026-01-01", 5, 50.0)])
        profile = pd.DataFrame([profile_row("AAA", 12.0), profile_row("BBB", 40.0)])
        result = target_allocation.compute_actual_weights(trades, profile).set_index("Symbol")
        # AAA value = 10*12=120, BBB value = 5*40=200, total=320
        assert result.loc["AAA", "Actual %"] == pytest.approx(120 / 320 * 100)
        assert result.loc["BBB", "Actual %"] == pytest.approx(200 / 320 * 100)
        assert result["Actual %"].sum() == pytest.approx(100.0)

    def test_current_value_is_quantity_times_latest_price(self):
        trades = make_transactions([buy("AAA", "2026-01-01", 10, 10.0)])
        profile = pd.DataFrame([profile_row("AAA", 12.5)])
        result = target_allocation.compute_actual_weights(trades, profile).set_index("Symbol")
        assert result.loc["AAA", "Current Value"] == pytest.approx(125.0)

    def test_classification_uses_sector_for_equity(self):
        trades = make_transactions([buy("AAA", "2026-01-01", 10, 10.0)])
        profile = pd.DataFrame([profile_row("AAA", 12.0, sector="Technology", industry="Software", quote_type="EQUITY")])
        result = target_allocation.compute_actual_weights(trades, profile).set_index("Symbol")
        assert result.loc["AAA", "Classification"] == "Technology"

    def test_classification_falls_back_to_industry_for_non_equity(self):
        trades = make_transactions([buy("SHV", "2026-01-01", 10, 100.0)])
        profile = pd.DataFrame([profile_row("SHV", 100.0, sector=None, industry="Ultrashort Bond", quote_type="ETF")])
        result = target_allocation.compute_actual_weights(trades, profile).set_index("Symbol")
        assert result.loc["SHV", "Classification"] == "Ultrashort Bond"

    def test_symbol_with_nan_latest_price_is_excluded_from_total(self):
        trades = make_transactions([buy("AAA", "2026-01-01", 10, 10.0), buy("BBB", "2026-01-01", 5, 50.0)])
        profile = pd.DataFrame([profile_row("AAA", 12.0), profile_row("BBB", np.nan)])
        result = target_allocation.compute_actual_weights(trades, profile).set_index("Symbol")
        assert pd.isna(result.loc["BBB", "Current Value"])
        assert pd.isna(result.loc["BBB", "Actual %"])
        # BBB's NaN is excluded from the total (pandas .sum() treats NaN as 0), so AAA
        # reads a full 100% even though BBB is also genuinely held -- documented overstatement.
        assert result.loc["AAA", "Actual %"] == pytest.approx(100.0)

    def test_empty_trades_returns_empty_frame_with_expected_columns(self):
        trades = make_transactions([])
        profile = pd.DataFrame(columns=["Symbol", "Latest Price", "Sector", "Industry", "Quote Type"])
        result = target_allocation.compute_actual_weights(trades, profile)
        assert result.empty
        assert {"Symbol", "Classification", "Current Value", "Actual %"} <= set(result.columns)


def _holdings_row(symbol, actual_pct, classification="Technology", current_value=1000.0, latest_price=100.0):
    return {
        "Symbol": symbol, "Classification": classification, "Current Value": current_value,
        "Latest Price": latest_price, "Actual %": actual_pct,
    }


class TestComputeStockTargetStatus:
    def test_untagged_symbol_defaults_category_to_others(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 5.0)])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame(columns=["Symbol", "Target %"])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Category"] == "Others"

    def test_tagged_symbol_gets_its_real_category(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 5.0)])
        symbol_types = pd.DataFrame([{"Symbol": "AAA", "Allocation Type": "Growth"}])
        targets = pd.DataFrame(columns=["Symbol", "Target %"])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Category"] == "Growth"

    def test_untargeted_symbol_defaults_target_to_zero(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 5.0)])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame(columns=["Symbol", "Target %"])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Target %"] == 0.0

    def test_delta_exactly_positive_2_reads_hit(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 7.0)])  # target 5.0 -> delta +2.0
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Hit Target"

    def test_delta_just_over_positive_2_reads_over(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 7.01)])  # target 5.0 -> delta +2.01
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Over Target"

    def test_delta_exactly_negative_2_reads_hit(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 3.0)])  # target 5.0 -> delta -2.0
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Hit Target"

    def test_delta_just_under_negative_2_reads_short(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 2.99)])  # target 5.0 -> delta -2.01
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Short Target"

    def test_action_mapping_for_all_three_statuses(self):
        holdings = pd.DataFrame([_holdings_row("OVER", 10.0), _holdings_row("HIT", 5.0), _holdings_row("SHORT", 1.0)])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([
            {"Symbol": "OVER", "Target %": 5.0}, {"Symbol": "HIT", "Target %": 5.0}, {"Symbol": "SHORT", "Target %": 5.0},
        ])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["OVER", "Action"] == "Sell"
        assert result.loc["HIT", "Action"] == "Hold"
        assert result.loc["SHORT", "Action"] == "Buy More"

    def test_small_untargeted_holding_still_reads_hit_not_over(self):
        # The nuance worth its own named test: a small, never-targeted position defaults
        # to Target % = 0, but only reads Over Target if its OWN Actual % exceeds +2pp --
        # 1.5% actual vs 0% default target is still within the +/-2pp band.
        holdings = pd.DataFrame([_holdings_row("AAA", 1.5)])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame(columns=["Symbol", "Target %"])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Target %"] == 0.0
        assert result.loc["AAA", "Status"] == "Hit Target"

    def test_trade_dollars_and_shares_positive_for_short_target(self):
        # AAA: 2% actual, 5% target, $200k total -> $6,000 short -> buy 300 shares @ $20.
        holdings = pd.DataFrame([
            {"Symbol": "AAA", "Classification": "Technology", "Current Value": 4000.0, "Latest Price": 20.0, "Actual %": 2.0},
            {"Symbol": "BBB", "Classification": "Technology", "Current Value": 196000.0, "Latest Price": 50.0, "Actual %": 98.0},
        ])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Trade $"] == pytest.approx(6000.0)
        assert result.loc["AAA", "Trade Shares"] == pytest.approx(300.0)

    def test_trade_dollars_and_shares_negative_for_over_target(self):
        # AAA: 8% actual, 5% target, $200k total -> $6,000 excess -> sell 150 shares @ $40.
        holdings = pd.DataFrame([
            {"Symbol": "AAA", "Classification": "Technology", "Current Value": 16000.0, "Latest Price": 40.0, "Actual %": 8.0},
            {"Symbol": "BBB", "Classification": "Technology", "Current Value": 184000.0, "Latest Price": 50.0, "Actual %": 92.0},
        ])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Trade $"] == pytest.approx(-6000.0)
        assert result.loc["AAA", "Trade Shares"] == pytest.approx(-150.0)

    def test_trade_dollars_near_zero_for_hit_target(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 5.0, current_value=10000.0, latest_price=100.0)])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(holdings, symbol_types, targets).set_index("Symbol")
        assert result.loc["AAA", "Trade $"] == pytest.approx(0.0)
        assert result.loc["AAA", "Trade Shares"] == pytest.approx(0.0)


class TestComputeSectorTargetStatus:
    def test_actual_pct_computed_from_value_share_of_total_portfolio(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 30.0},
            {"Category": "Growth", "Classification": "Financial Services", "Current Value": 70.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(stock_status, targets).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Actual %"] == pytest.approx(30.0)
        assert result.loc[("Growth", "Financial Services"), "Actual %"] == pytest.approx(70.0)

    def test_pair_with_target_but_no_holdings_still_appears_at_zero_actual(self):
        stock_status = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Healthcare", "Target %": 10.0}])
        result = target_allocation.compute_sector_target_status(stock_status, targets).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Healthcare"), "Actual %"] == 0.0
        assert result.loc[("Growth", "Healthcare"), "Target %"] == 10.0

    def test_pair_held_but_no_stored_target_defaults_target_to_zero(self):
        stock_status = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(stock_status, targets).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target %"] == 0.0

    def test_sums_multiple_stocks_within_same_sector(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 30.0},
            {"Category": "Growth", "Classification": "Technology", "Current Value": 20.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(stock_status, targets).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Current Value"] == pytest.approx(50.0)

    def test_delta_status_action_correct_for_over_case(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 80.0},
            {"Category": "Dividend", "Classification": "Healthcare", "Current Value": 20.0},
        ])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 50.0}])
        result = target_allocation.compute_sector_target_status(stock_status, targets).set_index(["Category", "Sector"])
        row = result.loc[("Growth", "Technology")]
        assert row["Delta %"] == pytest.approx(30.0)
        assert row["Status"] == "Over Target"
        assert row["Action"] == "Sell"

    def test_actual_pct_sums_to_total_across_all_sector_rows(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 30.0},
            {"Category": "Dividend", "Classification": "Healthcare", "Current Value": 70.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(stock_status, targets)
        assert result["Actual %"].sum() == pytest.approx(100.0)

    def test_same_sector_name_under_different_categories_stays_separate(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 60.0},
            {"Category": "Dividend", "Classification": "Technology", "Current Value": 40.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(stock_status, targets).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Current Value"] == pytest.approx(60.0)
        assert result.loc[("Dividend", "Technology"), "Current Value"] == pytest.approx(40.0)


class TestComputeCategoryTargetStatus:
    def test_actual_pct_computed_from_value_share_of_total(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Current Value": 65.0},
            {"Category": "Dividend", "Current Value": 35.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(stock_status, targets).set_index("Category")
        assert result.loc["Growth", "Actual %"] == pytest.approx(65.0)
        assert result.loc["Dividend", "Actual %"] == pytest.approx(35.0)

    def test_category_with_target_but_zero_holdings_still_appears(self):
        stock_status = pd.DataFrame([{"Category": "Growth", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Others", "Target %": 0.0}])
        result = target_allocation.compute_category_target_status(stock_status, targets).set_index("Category")
        assert "Others" in result.index
        assert result.loc["Others", "Actual %"] == 0.0

    def test_category_with_holdings_but_no_stored_target_defaults_to_zero(self):
        stock_status = pd.DataFrame([{"Category": "Growth", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(stock_status, targets).set_index("Category")
        assert result.loc["Growth", "Target %"] == 0.0

    def test_delta_status_action_for_short_case(self):
        stock_status = pd.DataFrame([{"Category": "Dividend", "Current Value": 20.0}, {"Category": "Growth", "Current Value": 80.0}])
        targets = pd.DataFrame([{"Category": "Dividend", "Target %": 35.0}])
        result = target_allocation.compute_category_target_status(stock_status, targets).set_index("Category")
        row = result.loc["Dividend"]
        assert row["Delta %"] == pytest.approx(-15.0)
        assert row["Status"] == "Short Target"
        assert row["Action"] == "Buy More"

    def test_multiple_categories_summed_correctly(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Current Value": 40.0},
            {"Category": "Growth", "Current Value": 25.0},
            {"Category": "Dividend", "Current Value": 35.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(stock_status, targets).set_index("Category")
        assert result.loc["Growth", "Current Value"] == pytest.approx(65.0)

    def test_actual_pct_sums_to_100_across_categories(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Current Value": 65.0},
            {"Category": "Dividend", "Current Value": 33.0},
            {"Category": "Others", "Current Value": 2.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(stock_status, targets)
        assert result["Actual %"].sum() == pytest.approx(100.0)

    def test_category_universe_is_not_fixed_to_three(self):
        # A 4th category (e.g. from an open-ended symbol_types tag) is picked up with no
        # code change -- confirms the union-of-held-and-targeted derivation, not a
        # hardcoded Growth/Dividend/Others list.
        stock_status = pd.DataFrame([{"Category": "Crypto", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(stock_status, targets)
        assert "Crypto" in set(result["Category"])


class TestSumStockTargetsBySector:
    def test_sums_target_pct_within_each_category_sector_pair(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Target %": 5.0},
            {"Category": "Growth", "Classification": "Technology", "Target %": 5.0},
        ])
        result = target_allocation.sum_stock_targets_by_sector(stock_status).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target %"] == pytest.approx(10.0)

    def test_pair_with_no_holdings_does_not_appear(self):
        stock_status = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Target %": 5.0}])
        result = target_allocation.sum_stock_targets_by_sector(stock_status)
        assert not ((result["Category"] == "Growth") & (result["Sector"] == "Healthcare")).any()

    def test_multiple_sectors_summed_independently(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Target %": 5.0},
            {"Category": "Growth", "Classification": "Healthcare", "Target %": 10.0},
        ])
        result = target_allocation.sum_stock_targets_by_sector(stock_status).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target %"] == pytest.approx(5.0)
        assert result.loc[("Growth", "Healthcare"), "Target %"] == pytest.approx(10.0)


class TestSumSectorTargetsByCategory:
    def test_sums_target_pct_within_each_category(self):
        sector_status = pd.DataFrame([
            {"Category": "Growth", "Sector": "Technology", "Target %": 15.0},
            {"Category": "Growth", "Sector": "Healthcare", "Target %": 10.0},
        ])
        result = target_allocation.sum_sector_targets_by_category(sector_status).set_index("Category")
        assert result.loc["Growth", "Target %"] == pytest.approx(25.0)

    def test_single_row_category_returns_its_own_value(self):
        sector_status = pd.DataFrame([{"Category": "Dividend", "Sector": "Technology", "Target %": 10.0}])
        result = target_allocation.sum_sector_targets_by_category(sector_status).set_index("Category")
        assert result.loc["Dividend", "Target %"] == pytest.approx(10.0)

    def test_multiple_categories_summed_independently(self):
        sector_status = pd.DataFrame([
            {"Category": "Growth", "Sector": "Technology", "Target %": 15.0},
            {"Category": "Dividend", "Sector": "Technology", "Target %": 10.0},
        ])
        result = target_allocation.sum_sector_targets_by_category(sector_status).set_index("Category")
        assert result.loc["Growth", "Target %"] == pytest.approx(15.0)
        assert result.loc["Dividend", "Target %"] == pytest.approx(10.0)
