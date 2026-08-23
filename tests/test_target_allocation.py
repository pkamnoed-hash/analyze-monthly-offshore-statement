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


class TestTagHoldingsCategory:
    """v4.9 -- Category-tagging was extracted out of compute_stock_target_status()
    into its own function so it can run before compute_category_target_status()
    (see core/target_allocation.py's own docstring for why). These two tests used
    to live under TestComputeStockTargetStatus, back when tagging happened inside
    that same function."""

    def test_untagged_symbol_defaults_category_to_others(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 5.0)])
        symbol_types = pd.DataFrame(columns=["Symbol", "Allocation Type"])
        result = target_allocation.tag_holdings_category(holdings, symbol_types).set_index("Symbol")
        assert result.loc["AAA", "Category"] == "Others"

    def test_tagged_symbol_gets_its_real_category(self):
        holdings = pd.DataFrame([_holdings_row("AAA", 5.0)])
        symbol_types = pd.DataFrame([{"Symbol": "AAA", "Allocation Type": "Growth"}])
        result = target_allocation.tag_holdings_category(holdings, symbol_types).set_index("Symbol")
        assert result.loc["AAA", "Category"] == "Growth"


def _tagged_holdings_row(
    symbol, actual_pct, category="Growth", classification="Technology", current_value=1000.0, latest_price=100.0,
):
    """Like _holdings_row, but includes Category -- the shape
    compute_stock_target_status() expects post-v4.9 (already tagged by
    tag_holdings_category(), not tagged internally anymore)."""
    return {
        "Symbol": symbol, "Category": category, "Classification": classification,
        "Current Value": current_value, "Latest Price": latest_price, "Actual %": actual_pct,
    }


def _sector_status_row(category, sector, effective_target_pct, untargeted=False):
    """A minimal compute_sector_target_status()-shaped row -- only the
    (Category, Sector, Target %, Untargeted) columns compute_stock_target_status()
    actually reads from `sector_status` to scale a stock's raw Target % of Parent
    into an effective one, and to propagate the v4.9.1 Untargeted flag."""
    return {"Category": category, "Sector": sector, "Target %": effective_target_pct, "Untargeted": untargeted}


class TestComputeStockTargetStatus:
    """v4.9 -- every test here scales a raw 'Target % of Parent' through a
    sector's own effective %. Most tests set the sector's effective % to 100.0
    specifically so raw-% and effective-% coincide numerically (100/100 x raw =
    raw) -- this keeps the ±2pp boundary/Trade $ math identical to the pre-v4.9
    numbers while still exercising the real scaling code path, rather than
    inventing new expected values for every single test."""

    _FULL_PASSTHROUGH_SECTOR = pd.DataFrame([_sector_status_row("Growth", "Technology", 100.0, untargeted=False)])

    def test_untargeted_symbol_defaults_target_of_parent_to_zero(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 5.0)])
        targets = pd.DataFrame(columns=["Symbol", "Target %"])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Target % of Parent"] == 0.0
        assert result.loc["AAA", "Target %"] == 0.0
        # v4.9.1 -- no row in `targets` for AAA at all -> genuinely Untargeted, not a
        # silent "explicitly targeted at 0%".
        assert result.loc["AAA", "Untargeted"] == True  # noqa: E712

    def test_delta_exactly_positive_2_reads_hit(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 7.0)])  # effective target 5.0 -> delta +2.0
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Hit Target"

    def test_delta_just_over_positive_2_reads_over(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 7.01)])  # effective target 5.0 -> delta +2.01
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Over Target"

    def test_delta_exactly_negative_2_reads_hit(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 3.0)])  # effective target 5.0 -> delta -2.0
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Hit Target"

    def test_delta_just_under_negative_2_reads_short(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 2.99)])  # effective target 5.0 -> delta -2.01
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Status"] == "Short Target"

    def test_action_mapping_for_all_three_statuses(self):
        holdings = pd.DataFrame([
            _tagged_holdings_row("OVER", 10.0), _tagged_holdings_row("HIT", 5.0), _tagged_holdings_row("SHORT", 1.0),
        ])
        targets = pd.DataFrame([
            {"Symbol": "OVER", "Target %": 5.0}, {"Symbol": "HIT", "Target %": 5.0}, {"Symbol": "SHORT", "Target %": 5.0},
        ])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["OVER", "Action"] == "Sell"
        assert result.loc["HIT", "Action"] == "Hold"
        assert result.loc["SHORT", "Action"] == "Buy More"

    def test_trade_dollars_and_shares_positive_for_short_target(self):
        # AAA: 2% actual, 5% effective target, $200k total -> $6,000 short -> buy 300 shares @ $20.
        holdings = pd.DataFrame([
            {"Symbol": "AAA", "Category": "Growth", "Classification": "Technology", "Current Value": 4000.0, "Latest Price": 20.0, "Actual %": 2.0},
            {"Symbol": "BBB", "Category": "Growth", "Classification": "Technology", "Current Value": 196000.0, "Latest Price": 50.0, "Actual %": 98.0},
        ])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Trade $"] == pytest.approx(6000.0)
        assert result.loc["AAA", "Trade Shares"] == pytest.approx(300.0)

    def test_trade_dollars_and_shares_negative_for_over_target(self):
        # AAA: 8% actual, 5% effective target, $200k total -> $6,000 excess -> sell 150 shares @ $40.
        holdings = pd.DataFrame([
            {"Symbol": "AAA", "Category": "Growth", "Classification": "Technology", "Current Value": 16000.0, "Latest Price": 40.0, "Actual %": 8.0},
            {"Symbol": "BBB", "Category": "Growth", "Classification": "Technology", "Current Value": 184000.0, "Latest Price": 50.0, "Actual %": 92.0},
        ])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Trade $"] == pytest.approx(-6000.0)
        assert result.loc["AAA", "Trade Shares"] == pytest.approx(-150.0)

    def test_trade_dollars_near_zero_for_hit_target(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 5.0, current_value=10000.0, latest_price=100.0)])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 5.0}])
        result = target_allocation.compute_stock_target_status(
            holdings, targets, self._FULL_PASSTHROUGH_SECTOR,
        ).set_index("Symbol")
        assert result.loc["AAA", "Trade $"] == pytest.approx(0.0)
        assert result.loc["AAA", "Trade Shares"] == pytest.approx(0.0)

    def test_target_pct_of_parent_is_raw_unscaled_value(self):
        # Sector's own effective % is 40.0 here (not the 100.0 passthrough used above),
        # so "Target % of Parent" (raw, 25.0) and "Target %" (effective, 10.0) must
        # genuinely differ -- the core assertion this whole v4.9 rework is about.
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 12.0)])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 25.0}])
        sector_status = pd.DataFrame([_sector_status_row("Growth", "Technology", 40.0)])
        result = target_allocation.compute_stock_target_status(holdings, targets, sector_status).set_index("Symbol")
        assert result.loc["AAA", "Target % of Parent"] == pytest.approx(25.0)
        assert result.loc["AAA", "Target %"] == pytest.approx(10.0)  # 25/100 * 40
        assert result.loc["AAA", "Delta %"] == pytest.approx(2.0)  # 12 - 10

    def test_zero_effective_sector_target_makes_stock_effective_zero(self):
        # A sector with no effective target of its own (e.g. its Category is
        # untargeted) makes every stock under it effectively 0% too, regardless
        # of how the stock's own raw "% of Sector" is set -- the top-down
        # consequence documented in core/target_allocation.py's own docstring.
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 3.0)])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 80.0}])
        sector_status = pd.DataFrame([_sector_status_row("Growth", "Technology", 0.0, untargeted=True)])
        result = target_allocation.compute_stock_target_status(holdings, targets, sector_status).set_index("Symbol")
        assert result.loc["AAA", "Target % of Parent"] == pytest.approx(80.0)
        assert result.loc["AAA", "Target %"] == pytest.approx(0.0)


class TestStockLevelUntargeted:
    """v4.9.1 -- a genuinely never-configured stock/sector should read
    Status="Untargeted", not silently compute Over/Short/Hit against an
    implicit 0% target. See core/target_allocation.py's top docstring for the
    real-data incident that prompted this (a Rebalance & Reallocate screenshot
    where nearly every row read "Over Target -> Sell" simply because no
    sector/stock targets had ever been saved)."""

    def test_no_own_row_is_untargeted_even_if_sector_is_targeted(self):
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 10.0)])
        targets = pd.DataFrame(columns=["Symbol", "Target %"])  # AAA never saved
        sector_status = pd.DataFrame([_sector_status_row("Growth", "Technology", 50.0, untargeted=False)])
        result = target_allocation.compute_stock_target_status(holdings, targets, sector_status).set_index("Symbol")
        assert result.loc["AAA", "Untargeted"] == True  # noqa: E712
        assert result.loc["AAA", "Status"] == "Untargeted"
        assert result.loc["AAA", "Action"] == "—"
        assert pd.isna(result.loc["AAA", "Trade $"])
        assert pd.isna(result.loc["AAA", "Trade Shares"])

    def test_own_row_present_but_sector_untargeted_is_still_untargeted(self):
        # AAA has its own explicit "50% of Technology" saved, but Technology itself was
        # never given a sector target -- the whole chain still has no real destination,
        # so this should read Untargeted, not a false Over/Short/Hit against 0%.
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 10.0)])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 50.0}])
        sector_status = pd.DataFrame([_sector_status_row("Growth", "Technology", 0.0, untargeted=True)])
        result = target_allocation.compute_stock_target_status(holdings, targets, sector_status).set_index("Symbol")
        assert result.loc["AAA", "Untargeted"] == True  # noqa: E712
        assert result.loc["AAA", "Status"] == "Untargeted"

    def test_explicit_zero_target_is_not_untargeted(self):
        # AAA was explicitly saved at 0% -- a real "I want none of this" decision, not an
        # absence of one -- so it should classify normally (Over, since 10% actual vs an
        # explicit 0% effective target exceeds the +2pp band), not read Untargeted.
        holdings = pd.DataFrame([_tagged_holdings_row("AAA", 10.0)])
        targets = pd.DataFrame([{"Symbol": "AAA", "Target %": 0.0}])
        sector_status = pd.DataFrame([_sector_status_row("Growth", "Technology", 100.0, untargeted=False)])
        result = target_allocation.compute_stock_target_status(holdings, targets, sector_status).set_index("Symbol")
        assert result.loc["AAA", "Untargeted"] == False  # noqa: E712
        assert result.loc["AAA", "Status"] == "Over Target"
        assert result.loc["AAA", "Action"] == "Sell"
        assert pd.notna(result.loc["AAA", "Trade $"])


class TestComputeSectorTargetStatus:
    """v4.9 -- every test now also supplies `category_status` (this level's
    parent). Tests that only assert Actual %/Current Value use a category
    effective target of 100.0 so raw sector % and effective sector % coincide
    (same 'passthrough' convention as TestComputeStockTargetStatus)."""

    _FULL_PASSTHROUGH_CATEGORY = pd.DataFrame([
        {"Category": "Growth", "Target %": 100.0, "Untargeted": False},
        {"Category": "Dividend", "Target %": 100.0, "Untargeted": False},
    ])

    def test_actual_pct_computed_from_value_share_of_total_portfolio(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 30.0},
            {"Category": "Growth", "Classification": "Financial Services", "Current Value": 70.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, self._FULL_PASSTHROUGH_CATEGORY,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Actual %"] == pytest.approx(30.0)
        assert result.loc[("Growth", "Financial Services"), "Actual %"] == pytest.approx(70.0)

    def test_pair_with_target_but_no_holdings_still_appears_at_zero_actual(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Healthcare", "Target %": 10.0}])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, self._FULL_PASSTHROUGH_CATEGORY,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Healthcare"), "Actual %"] == 0.0
        assert result.loc[("Growth", "Healthcare"), "Target % of Parent"] == 10.0
        assert result.loc[("Growth", "Healthcare"), "Target %"] == 10.0  # 10/100 * 100 passthrough

    def test_pair_held_but_no_stored_target_defaults_target_to_zero(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, self._FULL_PASSTHROUGH_CATEGORY,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target % of Parent"] == 0.0
        assert result.loc[("Growth", "Technology"), "Target %"] == 0.0

    def test_sums_multiple_stocks_within_same_sector(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 30.0},
            {"Category": "Growth", "Classification": "Technology", "Current Value": 20.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, self._FULL_PASSTHROUGH_CATEGORY,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Current Value"] == pytest.approx(50.0)

    def test_delta_status_action_correct_for_over_case(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 80.0},
            {"Category": "Dividend", "Classification": "Healthcare", "Current Value": 20.0},
        ])
        # Raw 50% of Growth, Growth's own effective target is 60% -> effective 30%.
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 50.0}])
        category_status = pd.DataFrame([
            {"Category": "Growth", "Target %": 60.0, "Untargeted": False},
            {"Category": "Dividend", "Target %": 40.0, "Untargeted": False},
        ])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, category_status,
        ).set_index(["Category", "Sector"])
        row = result.loc[("Growth", "Technology")]
        assert row["Target % of Parent"] == pytest.approx(50.0)
        assert row["Target %"] == pytest.approx(30.0)  # 50/100 * 60
        assert row["Delta %"] == pytest.approx(50.0)  # Actual 80 - effective 30
        assert row["Status"] == "Over Target"
        assert row["Action"] == "Sell"

    def test_actual_pct_sums_to_total_across_all_sector_rows(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 30.0},
            {"Category": "Dividend", "Classification": "Healthcare", "Current Value": 70.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(tagged_holdings, targets, self._FULL_PASSTHROUGH_CATEGORY)
        assert result["Actual %"].sum() == pytest.approx(100.0)

    def test_same_sector_name_under_different_categories_stays_separate(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Current Value": 60.0},
            {"Category": "Dividend", "Classification": "Technology", "Current Value": 40.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, self._FULL_PASSTHROUGH_CATEGORY,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Current Value"] == pytest.approx(60.0)
        assert result.loc[("Dividend", "Technology"), "Current Value"] == pytest.approx(40.0)

    def test_effective_target_scales_by_category_effective(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 40.0}])
        category_status = pd.DataFrame([{"Category": "Growth", "Target %": 50.0, "Untargeted": False}])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, category_status,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target %"] == pytest.approx(20.0)  # 40/100 * 50

    def test_zero_effective_category_target_makes_sector_effective_zero_regardless_of_raw(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 80.0}])
        # Growth explicitly saved at 0% -- a real decision, so Untargeted=False here.
        category_status = pd.DataFrame([{"Category": "Growth", "Target %": 0.0, "Untargeted": False}])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, category_status,
        ).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target % of Parent"] == pytest.approx(80.0)
        assert result.loc[("Growth", "Technology"), "Target %"] == pytest.approx(0.0)
        assert result.loc[("Growth", "Technology"), "Untargeted"] == False  # noqa: E712


class TestSectorLevelUntargeted:
    """v4.9.1 -- same propagation rule one level up: a sector with no row of
    its own is Untargeted, and a sector inherits Untargeted from its Category
    even if it has its own explicit row."""

    def test_no_own_row_is_untargeted_even_if_category_is_targeted(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Sector", "Target %"])  # Technology never saved
        category_status = pd.DataFrame([{"Category": "Growth", "Target %": 60.0, "Untargeted": False}])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, category_status,
        ).set_index(["Category", "Sector"])
        row = result.loc[("Growth", "Technology")]
        assert row["Untargeted"] == True  # noqa: E712
        assert row["Status"] == "Untargeted"
        assert row["Action"] == "—"

    def test_own_row_present_but_category_untargeted_is_still_untargeted(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 30.0}])
        category_status = pd.DataFrame([{"Category": "Growth", "Target %": 0.0, "Untargeted": True}])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, category_status,
        ).set_index(["Category", "Sector"])
        row = result.loc[("Growth", "Technology")]
        assert row["Untargeted"] == True  # noqa: E712
        assert row["Status"] == "Untargeted"

    def test_explicit_zero_target_under_a_targeted_category_is_not_untargeted(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 0.0}])
        category_status = pd.DataFrame([{"Category": "Growth", "Target %": 60.0, "Untargeted": False}])
        result = target_allocation.compute_sector_target_status(
            tagged_holdings, targets, category_status,
        ).set_index(["Category", "Sector"])
        row = result.loc[("Growth", "Technology")]
        assert row["Untargeted"] == False  # noqa: E712
        # Actual 100% vs an explicit 0% effective target -> genuinely Over, not Untargeted.
        assert row["Status"] == "Over Target"


class TestComputeCategoryTargetStatus:
    """Unaffected formula-wise by v4.9 -- Category has no parent, so its
    stored Target % already is the effective, whole-portfolio value. Only the
    input variable is renamed (tagged_holdings, from tag_holdings_category(),
    instead of the old stock_status) to match the new pipeline order."""

    def test_actual_pct_computed_from_value_share_of_total(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Current Value": 65.0},
            {"Category": "Dividend", "Current Value": 35.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        assert result.loc["Growth", "Actual %"] == pytest.approx(65.0)
        assert result.loc["Dividend", "Actual %"] == pytest.approx(35.0)

    def test_category_with_target_but_zero_holdings_still_appears(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Current Value": 100.0}])
        targets = pd.DataFrame([{"Category": "Others", "Target %": 0.0}])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        assert "Others" in result.index
        assert result.loc["Others", "Actual %"] == 0.0

    def test_category_with_holdings_but_no_stored_target_defaults_to_zero(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        assert result.loc["Growth", "Target %"] == 0.0

    def test_delta_status_action_for_short_case(self):
        tagged_holdings = pd.DataFrame([{"Category": "Dividend", "Current Value": 20.0}, {"Category": "Growth", "Current Value": 80.0}])
        targets = pd.DataFrame([{"Category": "Dividend", "Target %": 35.0}])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        row = result.loc["Dividend"]
        assert row["Delta %"] == pytest.approx(-15.0)
        assert row["Status"] == "Short Target"
        assert row["Action"] == "Buy More"
        assert row["Untargeted"] == False  # noqa: E712 -- Dividend has an explicit row

    def test_multiple_categories_summed_correctly(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Current Value": 40.0},
            {"Category": "Growth", "Current Value": 25.0},
            {"Category": "Dividend", "Current Value": 35.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        assert result.loc["Growth", "Current Value"] == pytest.approx(65.0)

    def test_actual_pct_sums_to_100_across_categories(self):
        tagged_holdings = pd.DataFrame([
            {"Category": "Growth", "Current Value": 65.0},
            {"Category": "Dividend", "Current Value": 33.0},
            {"Category": "Others", "Current Value": 2.0},
        ])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets)
        assert result["Actual %"].sum() == pytest.approx(100.0)

    def test_category_universe_is_not_fixed_to_three(self):
        # A 4th category (e.g. from an open-ended symbol_types tag) is picked up with no
        # code change -- confirms the union-of-held-and-targeted derivation, not a
        # hardcoded Growth/Dividend/Others list.
        tagged_holdings = pd.DataFrame([{"Category": "Crypto", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets)
        assert "Crypto" in set(result["Category"])


class TestCategoryLevelUntargeted:
    """v4.9.1 -- Category has no parent, so its Untargeted flag is simply
    "was there ever a row saved for it" -- no propagation needed."""

    def test_no_row_saved_is_untargeted(self):
        tagged_holdings = pd.DataFrame([{"Category": "Growth", "Current Value": 100.0}])
        targets = pd.DataFrame(columns=["Category", "Target %"])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        assert result.loc["Growth", "Untargeted"] == True  # noqa: E712
        assert result.loc["Growth", "Status"] == "Untargeted"
        assert result.loc["Growth", "Action"] == "—"

    def test_explicit_zero_is_not_untargeted(self):
        tagged_holdings = pd.DataFrame([{"Category": "Others", "Current Value": 0.0}])
        targets = pd.DataFrame([{"Category": "Others", "Target %": 0.0}])
        result = target_allocation.compute_category_target_status(tagged_holdings, targets).set_index("Category")
        assert result.loc["Others", "Untargeted"] == False  # noqa: E712
        assert result.loc["Others", "Status"] == "Hit Target"


class TestComputeFullTargetStatus:
    """Integration-style: a small synthetic 2-category portfolio, verifying the
    whole tag -> category -> sector -> stock chain multiplies out correctly
    end-to-end, not just each function in isolation."""

    def _build(self):
        trades = make_transactions([
            buy("TECH1", "2026-01-01", 10, 10.0),   # Growth/Technology, value 100
            buy("TECH2", "2026-01-01", 10, 10.0),   # Growth/Technology, value 100
            buy("BOND1", "2026-01-01", 10, 10.0),   # Dividend/Bonds, value 100
        ])
        profile = pd.DataFrame([
            profile_row("TECH1", 10.0, sector="Technology", quote_type="EQUITY"),
            profile_row("TECH2", 10.0, sector="Technology", quote_type="EQUITY"),
            profile_row("BOND1", 10.0, industry="Bonds", quote_type="ETF"),
        ])
        symbol_types = pd.DataFrame([
            {"Symbol": "TECH1", "Allocation Type": "Growth"},
            {"Symbol": "TECH2", "Allocation Type": "Growth"},
            {"Symbol": "BOND1", "Allocation Type": "Dividend"},
        ])
        # Growth = 60% of portfolio, Dividend = 40% (both explicit, so neither reads
        # Untargeted); within Growth, Technology = 50% of Growth; within Technology,
        # TECH1 = 70% of Technology, TECH2 = 30%.
        category_targets = pd.DataFrame([
            {"Category": "Growth", "Target %": 60.0}, {"Category": "Dividend", "Target %": 40.0},
        ])
        target_sectors = pd.DataFrame([{"Category": "Growth", "Sector": "Technology", "Target %": 50.0}])
        target_allocations = pd.DataFrame([
            {"Symbol": "TECH1", "Target %": 70.0}, {"Symbol": "TECH2", "Target %": 30.0},
        ])
        return trades, profile, symbol_types, category_targets, target_sectors, target_allocations

    def test_effective_chain_multiplies_out_correctly(self):
        trades, profile, symbol_types, category_targets, target_sectors, target_allocations = self._build()
        category_status, sector_status, stock_status = target_allocation.compute_full_target_status(
            trades, profile, symbol_types, category_targets, target_sectors, target_allocations,
        )
        assert category_status.set_index("Category").loc["Growth", "Target %"] == pytest.approx(60.0)
        # Technology: 50% of Growth's 60% = 30%.
        sector_row = sector_status.set_index(["Category", "Sector"]).loc[("Growth", "Technology")]
        assert sector_row["Target % of Parent"] == pytest.approx(50.0)
        assert sector_row["Target %"] == pytest.approx(30.0)
        # TECH1: 70% of Technology's effective 30% = 21%. TECH2: 30% of 30% = 9%.
        stocks = stock_status.set_index("Symbol")
        assert stocks.loc["TECH1", "Target % of Parent"] == pytest.approx(70.0)
        assert stocks.loc["TECH1", "Target %"] == pytest.approx(21.0)
        assert stocks.loc["TECH2", "Target %"] == pytest.approx(9.0)
        # Every row exercised above has a real, explicit target somewhere in its chain --
        # none of them should read Untargeted (Dividend/Bonds/BOND1 are deliberately left
        # out of target_sectors/target_allocations in this fixture and DO read Untargeted,
        # which is correct -- see test_only_category_set_reads_untargeted_not_over_target
        # for that scenario tested directly).
        assert not category_status.set_index("Category").loc["Growth", "Untargeted"]
        assert not sector_row["Untargeted"]
        assert not stocks.loc["TECH1", "Untargeted"]
        assert not stocks.loc["TECH2", "Untargeted"]

    def test_untargeted_category_zeroes_out_everything_beneath_it(self):
        trades, profile, symbol_types, category_targets, target_sectors, target_allocations = self._build()
        category_targets = pd.DataFrame(columns=["Category", "Target %"])  # Growth left untargeted
        category_status, sector_status, stock_status = target_allocation.compute_full_target_status(
            trades, profile, symbol_types, category_targets, target_sectors, target_allocations,
        )
        assert category_status.set_index("Category").loc["Growth", "Target %"] == pytest.approx(0.0)
        sector_row = sector_status.set_index(["Category", "Sector"]).loc[("Growth", "Technology")]
        assert sector_row["Target % of Parent"] == pytest.approx(50.0)  # raw ratio unaffected
        assert sector_row["Target %"] == pytest.approx(0.0)  # effective zeroed out
        stocks = stock_status.set_index("Symbol")
        assert stocks.loc["TECH1", "Target % of Parent"] == pytest.approx(70.0)  # raw ratio unaffected
        assert stocks.loc["TECH1", "Target %"] == pytest.approx(0.0)  # effective zeroed out

    def test_actual_pct_still_sums_to_100_across_stock_level(self):
        trades, profile, symbol_types, category_targets, target_sectors, target_allocations = self._build()
        _, _, stock_status = target_allocation.compute_full_target_status(
            trades, profile, symbol_types, category_targets, target_sectors, target_allocations,
        )
        assert stock_status["Actual %"].sum() == pytest.approx(100.0)

    def test_only_category_set_reads_untargeted_not_over_target(self):
        # v4.9.1 -- the exact real-data scenario that prompted the Untargeted status:
        # Category targets are real (Growth 60%), but Sector/Stock were never touched at
        # all. Every held stock/sector should read Untargeted, not a misleading
        # Over/Short/Hit computed against an implicit 0% target.
        trades, profile, symbol_types, category_targets, _, _ = self._build()
        empty_sectors = pd.DataFrame(columns=["Category", "Sector", "Target %"])
        empty_stocks = pd.DataFrame(columns=["Symbol", "Target %"])
        category_status, sector_status, stock_status = target_allocation.compute_full_target_status(
            trades, profile, symbol_types, category_targets, empty_sectors, empty_stocks,
        )
        assert not category_status.set_index("Category").loc["Growth", "Untargeted"]
        sector_row = sector_status.set_index(["Category", "Sector"]).loc[("Growth", "Technology")]
        assert sector_row["Untargeted"] == True  # noqa: E712
        assert sector_row["Status"] == "Untargeted"
        stocks = stock_status.set_index("Symbol")
        assert stocks.loc["TECH1", "Untargeted"] == True  # noqa: E712
        assert stocks.loc["TECH1", "Status"] == "Untargeted"
        assert stocks.loc["TECH1", "Action"] == "—"
        assert pd.isna(stocks.loc["TECH1", "Trade $"])


class TestSumStockTargetsBySector:
    def test_sums_target_pct_of_parent_within_each_category_sector_pair(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Target % of Parent": 50.0},
            {"Category": "Growth", "Classification": "Technology", "Target % of Parent": 50.0},
        ])
        result = target_allocation.sum_stock_targets_by_sector(stock_status).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target % of Parent"] == pytest.approx(100.0)

    def test_pair_with_no_holdings_does_not_appear(self):
        stock_status = pd.DataFrame([{"Category": "Growth", "Classification": "Technology", "Target % of Parent": 50.0}])
        result = target_allocation.sum_stock_targets_by_sector(stock_status)
        assert not ((result["Category"] == "Growth") & (result["Sector"] == "Healthcare")).any()

    def test_multiple_sectors_summed_independently(self):
        stock_status = pd.DataFrame([
            {"Category": "Growth", "Classification": "Technology", "Target % of Parent": 50.0},
            {"Category": "Growth", "Classification": "Healthcare", "Target % of Parent": 100.0},
        ])
        result = target_allocation.sum_stock_targets_by_sector(stock_status).set_index(["Category", "Sector"])
        assert result.loc[("Growth", "Technology"), "Target % of Parent"] == pytest.approx(50.0)
        assert result.loc[("Growth", "Healthcare"), "Target % of Parent"] == pytest.approx(100.0)


class TestSumSectorTargetsByCategory:
    def test_sums_target_pct_of_parent_within_each_category(self):
        sector_status = pd.DataFrame([
            {"Category": "Growth", "Sector": "Technology", "Target % of Parent": 60.0},
            {"Category": "Growth", "Sector": "Healthcare", "Target % of Parent": 40.0},
        ])
        result = target_allocation.sum_sector_targets_by_category(sector_status).set_index("Category")
        assert result.loc["Growth", "Target % of Parent"] == pytest.approx(100.0)

    def test_single_row_category_returns_its_own_value(self):
        sector_status = pd.DataFrame([{"Category": "Dividend", "Sector": "Technology", "Target % of Parent": 100.0}])
        result = target_allocation.sum_sector_targets_by_category(sector_status).set_index("Category")
        assert result.loc["Dividend", "Target % of Parent"] == pytest.approx(100.0)

    def test_multiple_categories_summed_independently(self):
        sector_status = pd.DataFrame([
            {"Category": "Growth", "Sector": "Technology", "Target % of Parent": 60.0},
            {"Category": "Dividend", "Sector": "Technology", "Target % of Parent": 40.0},
        ])
        result = target_allocation.sum_sector_targets_by_category(sector_status).set_index("Category")
        assert result.loc["Growth", "Target % of Parent"] == pytest.approx(60.0)
        assert result.loc["Dividend", "Target % of Parent"] == pytest.approx(40.0)
