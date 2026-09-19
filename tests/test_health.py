import pytest

from core import health
from core.health import DEBT, GREEN, GROWTH, PROFIT, RED, YELLOW

LATEST = "2024-12-31"
NAN = float("nan")


def series(values):
    """{"YYYY-12-31": value} ending in 2024, oldest first; a None value stays in as a gap."""
    first_year = 2024 - len(values) + 1
    return {f"{first_year + i}-12-31": v for i, v in enumerate(values)}


def build(*, revenue=(80, 86, 93, 100), net_income=(12, 14, 17, 20), gross_profit=50, operating_income=20,
          equity=100, total_debt=30, current_assets=200, current_liabilities=100, fcf=15, ocf=40):
    """(income, balance, cashflow) for a company that is green on every measure of the
    default profile. Latest revenue is 100 so a margin of N% is N in the argument. A
    keyword set to None leaves that statement row out."""
    income = {"Total Revenue": series(revenue), "Net Income": series(net_income)}
    balance, cashflow = {}, {}
    for statement, label, value in (
        (income, "Gross Profit", gross_profit),
        (income, "Operating Income", operating_income),
        (balance, "Stockholders Equity", equity),
        (balance, "Total Debt", total_debt),
        (balance, "Current Assets", current_assets),
        (balance, "Current Liabilities", current_liabilities),
        (cashflow, "Free Cash Flow", fcf),
        (cashflow, "Operating Cash Flow", ocf),
    ):
        if value is not None:
            statement[label] = {LATEST: value}
    return income, balance, cashflow


def assess(sector="Technology", industry=None, **overrides):
    income, balance, cashflow = build(**overrides)
    return health.assess_health(income, balance, cashflow, sector, industry)


def measure(result, name):
    for group in result["groups"].values():
        for m in group["measures"]:
            if m["name"] == name:
                return m
    return None


def light_of(result, name):
    found = measure(result, name)
    return found["light"] if found else None


class TestProfileFor:
    @pytest.mark.parametrize("sector, expected", [
        ("Technology", "default"), ("Healthcare", "default"), ("Communication Services", "default"),
        ("Financial Services", "default"),
        ("Consumer Defensive", "low_margin"), ("Consumer Cyclical", "low_margin"),
        ("Industrials", "cyclical"), ("Energy", "cyclical"), ("Basic Materials", "cyclical"),
        ("Utilities", "leveraged"), ("Real Estate", "leveraged"),
    ])
    def test_every_yahoo_sector_has_a_profile(self, sector, expected):
        assert health.profile_for(sector) == expected

    @pytest.mark.parametrize("sector", [None, "", "SomeNewSector", NAN])
    def test_a_missing_or_unknown_sector_falls_back_to_default(self, sector):
        assert health.profile_for(sector) == "default"

    @pytest.mark.parametrize("industry", [
        "Computer Hardware", "Grocery Stores", "Medical Distribution", "Discount Stores", "Auto Manufacturers",
        "Electronics & Computer Distribution",
    ])
    @pytest.mark.parametrize("sector", ["Technology", "Industrials", "Utilities", None])
    def test_a_low_margin_industry_overrides_any_sector(self, sector, industry):
        assert health.profile_for(sector, industry) == "low_margin"

    @pytest.mark.parametrize("industry", ["Software - Application", "Banks - Regional", "", None, NAN])
    def test_other_industries_leave_the_sector_profile_alone(self, industry):
        assert health.profile_for("Industrials", industry) == "cyclical"

    def test_the_sector_table_covers_exactly_the_eleven_yahoo_sectors_and_only_real_profiles(self):
        assert len(health.SECTOR_PROFILE) == 11
        assert set(health.SECTOR_PROFILE.values()) <= set(health.PROFILES)

    def test_every_profile_defines_every_measure_with_green_better_than_yellow(self):
        for name, profile in health.PROFILES.items():
            assert set(profile) == set(health._BASE_THRESHOLDS), name
            for key, pair in profile.items():
                if pair is None:
                    continue
                green_at, yellow_at = pair
                if key in health._LOWER_IS_BETTER:
                    assert green_at < yellow_at, (name, key)
                else:
                    assert green_at > yellow_at, (name, key)

    def test_every_profile_has_a_description(self):
        assert set(health.PROFILE_DESCRIPTIONS) == set(health.PROFILES)


# (sector, override, measure, expected light) -- each measure's green edge, just short of it,
# its yellow edge and just short of that, in every profile that changes the thresholds.
BOUNDARIES = [
    # default profile
    *[("Technology", {"gross_profit": v}, "Gross margin", e) for v, e in ((40, GREEN), (39, YELLOW), (20, YELLOW), (19, RED))],
    *[("Technology", {"operating_income": v}, "Operating margin", e) for v, e in ((15, GREEN), (14, YELLOW), (5, YELLOW), (4, RED))],
    *[("Technology", {"net_income": (12, 14, 17, v)}, "Return on equity", e) for v, e in ((15, GREEN), (14, YELLOW), (8, YELLOW), (7, RED))],
    *[("Technology", {"fcf": v}, "Free cash flow margin", e) for v, e in ((10, GREEN), (9, YELLOW), (0, YELLOW), (-1, RED))],
    *[("Technology", {"total_debt": v}, "Debt / equity", e) for v, e in ((100, GREEN), (101, YELLOW), (200, YELLOW), (201, RED))],
    *[("Technology", {"current_assets": v}, "Current ratio", e) for v, e in ((120, GREEN), (119, YELLOW), (70, YELLOW), (69, RED))],
    *[("Technology", {"total_debt": 100, "ocf": v}, "Debt / operating cash flow", e) for v, e in ((40, GREEN), (39, YELLOW), (20, YELLOW), (19, RED))],
    *[("Technology", {"revenue": v}, "Revenue growth (3y)", e) for v, e in (((100, 105), GREEN), ((100, 104), YELLOW), ((100, 99), YELLOW), ((100, 97), RED))],
    *[("Technology", {"net_income": v}, "Net income growth", e) for v, e in (((100, 105), GREEN), ((100, 104), YELLOW), ((100, 98), YELLOW), ((100, 97), RED))],
    # low margin (Consumer Defensive)
    *[("Consumer Defensive", {"gross_profit": v}, "Gross margin", e) for v, e in ((30, GREEN), (29, YELLOW), (15, YELLOW), (14, RED))],
    *[("Consumer Defensive", {"operating_income": v}, "Operating margin", e) for v, e in ((10, GREEN), (9, YELLOW), (4, YELLOW), (3, RED))],
    *[("Consumer Defensive", {"fcf": v}, "Free cash flow margin", e) for v, e in ((5, GREEN), (4, YELLOW), (0, YELLOW), (-1, RED))],
    *[("Consumer Defensive", {"current_assets": v}, "Current ratio", e) for v, e in ((100, GREEN), (99, YELLOW), (60, YELLOW), (59, RED))],
    # cyclical (Industrials)
    *[("Industrials", {"gross_profit": v}, "Gross margin", e) for v, e in ((25, GREEN), (24, YELLOW), (10, YELLOW), (9, RED))],
    *[("Industrials", {"operating_income": v}, "Operating margin", e) for v, e in ((10, GREEN), (9, YELLOW), (3, YELLOW), (2, RED))],
    *[("Industrials", {"net_income": (12, 14, 17, v)}, "Return on equity", e) for v, e in ((12, GREEN), (11, YELLOW), (6, YELLOW), (5, RED))],
    *[("Industrials", {"fcf": v}, "Free cash flow margin", e) for v, e in ((8, GREEN), (7, YELLOW), (0, YELLOW), (-1, RED))],
    # leveraged (Utilities)
    *[("Utilities", {"total_debt": v}, "Debt / equity", e) for v, e in ((200, GREEN), (201, YELLOW), (400, YELLOW), (401, RED))],
    *[("Utilities", {"net_income": (12, 14, 17, v)}, "Return on equity", e) for v, e in ((6, GREEN), (5, YELLOW), (2.5, YELLOW), (2, RED))],
    *[("Utilities", {"total_debt": v, "ocf": 100}, "Debt / operating cash flow", e) for v, e in ((500, GREEN), (501, YELLOW), (800, YELLOW), (801, RED))],
]


class TestMeasureBands:
    @pytest.mark.parametrize("sector, overrides, name, expected", BOUNDARIES)
    def test_threshold_edges(self, sector, overrides, name, expected):
        assert light_of(assess(sector, **overrides), name) == expected

    def test_the_same_operating_margin_rates_differently_by_profile(self):
        assert light_of(assess("Technology", operating_income=12), "Operating margin") == YELLOW
        assert light_of(assess("Consumer Defensive", operating_income=12), "Operating margin") == GREEN
        assert light_of(assess("Industrials", operating_income=12), "Operating margin") == GREEN
        assert light_of(assess("Utilities", operating_income=12), "Operating margin") == YELLOW

    def test_a_hardware_industry_is_rated_with_low_margin_thresholds_even_in_technology(self):
        result = assess("Technology", "Computer Hardware", gross_profit=30)
        assert result["profile"] == "low_margin"
        assert light_of(result, "Gross margin") == GREEN
        assert light_of(assess("Technology", gross_profit=30), "Gross margin") == YELLOW

    def test_leveraged_profile_skips_current_ratio_and_cash_flow_margin(self):
        result = assess("Utilities")
        assert measure(result, "Current ratio") is None
        assert measure(result, "Free cash flow margin") is None

    def test_a_utility_burning_cash_is_not_flagged_because_its_cash_flow_isnt_rated(self):
        assert assess("Utilities", fcf=-50)["flags"] == []

    def test_the_default_company_is_green_on_all_ten_measures(self):
        result = assess()
        assert result["n_measures"] == 10
        assert result["profile"] == "default"
        assert all(m["light"] == GREEN for g in result["groups"].values() for m in g["measures"])


class TestGrowthMeasures:
    def test_revenue_growth_uses_up_to_three_years(self):
        result = assess(revenue=(100, 105, 110, 121))  # 100 -> 121 over 3 years = 6.6%/yr
        assert measure(result, "Revenue growth (3y)")["value"] == "+6.6%/yr"
        assert light_of(result, "Revenue growth (3y)") == GREEN

    def test_revenue_growth_with_a_shorter_history_uses_what_there_is(self):
        result = assess(revenue=(100, 121))
        assert measure(result, "Revenue growth (3y)")["value"] == "+21.0%/yr"

    def test_revenue_growth_is_skipped_when_a_year_is_not_positive(self):
        assert measure(assess(revenue=(0, 50, 80, 100)), "Revenue growth (3y)") is None
        assert measure(assess(revenue=(80, 90, 100, 0)), "Revenue growth (3y)") is None

    def test_revenue_growth_is_skipped_with_a_single_year(self):
        assert measure(assess(revenue=(100,)), "Revenue growth (3y)") is None

    def test_a_gap_in_the_history_is_skipped_over(self):
        # oldest year missing (yfinance often leaves the oldest column empty)
        assert light_of(assess(revenue=(None, 80, 90, 100)), "Revenue growth (3y)") == GREEN

    def test_net_income_turning_from_a_loss_to_a_profit_is_yellow(self):
        result = assess(net_income=(-5, -2, -3, 8))
        assert measure(result, "Net income growth") == {"name": "Net income growth", "value": "turned profitable", "light": YELLOW}

    def test_net_income_still_negative_is_red(self):
        result = assess(net_income=(-5, -6, -3, -2))
        assert measure(result, "Net income growth") == {"name": "Net income growth", "value": "still negative", "light": RED}

    def test_net_income_growth_shows_the_rounded_percent(self):
        assert measure(assess(net_income=(100, 112)), "Net income growth")["value"] == "+12.0%"

    def test_net_income_growth_is_skipped_when_the_latest_year_is_missing(self):
        assert measure(assess(net_income=(10, 12, 14, None)), "Net income growth") is None

    @pytest.mark.parametrize("net_income, expected, text", [
        ((10, 11, 12, 13), GREEN, "4/4 profitable years"),
        ((-3, 11, 12, 13), YELLOW, "3/4 profitable years"),
        ((10, 11, 12, -1), RED, "3/4 profitable years"),
        ((-3, -1, 12, 13), RED, "2/4 profitable years"),
        ((10, 11, 12), GREEN, "3/3 profitable years"),
        ((0, 11, 12, 13), YELLOW, "3/4 profitable years"),
    ])
    def test_earnings_consistency(self, net_income, expected, text):
        found = measure(assess(net_income=net_income), "Earnings consistency")
        assert (found["light"], found["value"]) == (expected, text)

    def test_earnings_consistency_needs_at_least_three_years(self):
        assert measure(assess(net_income=(10, 12)), "Earnings consistency") is None


class TestDebtMeasures:
    def test_no_debt_is_green(self):
        result = assess(total_debt=0)
        assert measure(result, "Debt / operating cash flow") == {
            "name": "Debt / operating cash flow", "value": "no debt", "light": GREEN}
        assert light_of(result, "Debt / equity") == GREEN

    @pytest.mark.parametrize("ocf", [0, -10])
    def test_debt_with_no_positive_operating_cash_flow_is_red(self, ocf):
        assert measure(assess(ocf=ocf), "Debt / operating cash flow")["value"] == "no operating cash flow"
        assert light_of(assess(ocf=ocf), "Debt / operating cash flow") == RED

    def test_debt_over_cash_flow_is_shown_in_years(self):
        assert measure(assess(total_debt=100, ocf=40), "Debt / operating cash flow")["value"] == "2.5 yrs"

    def test_the_debt_cash_flow_test_is_skipped_without_a_cash_flow_statement_row(self):
        assert measure(assess(ocf=None), "Debt / operating cash flow") is None

    def test_negative_equity_is_a_red_debt_flag_and_roe_is_skipped(self):
        result = assess(equity=-50)
        assert measure(result, "Debt / equity") == {"name": "Debt / equity", "value": "negative equity", "light": RED}
        assert measure(result, "Return on equity") is None
        assert [m["name"] for m in result["groups"][DEBT]["measures"]].count("Debt / equity") == 1

    def test_zero_equity_counts_as_negative_equity(self):
        assert measure(assess(equity=0), "Debt / equity")["value"] == "negative equity"


class TestBankShapedStatements:
    def test_no_gross_profit_and_no_current_assets_is_partial_and_rated_on_roe_and_growth_only(self):
        result = assess("Financial Services", gross_profit=None, current_assets=None, current_liabilities=None)
        assert result["partial"] is True
        assert [m["name"] for g in result["groups"].values() for m in g["measures"]] == [
            "Return on equity", "Revenue growth (3y)", "Net income growth", "Earnings consistency"]
        assert result["groups"][DEBT] == {"light": None, "measures": []}
        assert result["overall"] == GREEN

    def test_a_partial_company_is_not_flagged_for_burning_cash(self):
        result = assess("Financial Services", gross_profit=None, current_assets=None, fcf=-500)
        assert result["flags"] == []

    def test_a_partial_company_that_loses_money_is_still_flagged(self):
        result = assess("Financial Services", gross_profit=None, current_assets=None, net_income=(12, 14, 17, -5))
        assert health.FLAG_LOSS in result["flags"]

    def test_only_one_of_the_two_missing_is_not_partial(self):
        result = assess(gross_profit=None)
        assert result["partial"] is False
        assert measure(result, "Gross margin") is None
        assert measure(result, "Current ratio") is not None

    def test_nan_line_items_count_as_missing_for_the_data_shape_rule(self):
        assert assess(gross_profit=NAN, current_assets=NAN)["partial"] is True


class TestCombiningRules:
    @pytest.mark.parametrize("lights, expected", [
        ([GREEN], GREEN), ([GREEN, YELLOW], GREEN), ([GREEN, GREEN, YELLOW], GREEN),
        ([GREEN, YELLOW, YELLOW], YELLOW), ([GREEN, RED], YELLOW), ([YELLOW, YELLOW], YELLOW),
        ([YELLOW, RED], RED), ([YELLOW, RED, RED], RED), ([RED], RED),
    ])
    def test_a_group_is_the_mean_of_its_measures(self, lights, expected):
        assert health._mean_light(lights)[0] == expected

    def test_lights_without_a_score_are_ignored_and_none_left_gives_none(self):
        assert health._mean_light([None, GREEN, None]) == (GREEN, 2)
        assert health._mean_light([None]) == (None, None)
        assert health._mean_light([]) == (None, None)

    def test_the_mean_cutoffs(self):
        assert health._light_from_mean(1.5) == GREEN
        assert health._light_from_mean(1.4999) == YELLOW
        assert health._light_from_mean(0.75) == YELLOW
        assert health._light_from_mean(0.7499) == RED

    def test_a_red_group_holds_an_otherwise_green_company_at_mixed(self):
        # Debt group: D/E red, current ratio red, debt/cash flow green -> mean 0.67 = red group,
        # yet the other two groups are so strong the overall mean (1.56) alone would be green.
        result = assess(total_debt=250, current_assets=50, ocf=200)
        assert result["groups"][DEBT]["light"] == RED
        assert result["groups"][PROFIT]["light"] == GREEN
        assert result["groups"][GROWTH]["light"] == GREEN
        assert result["overall"] == YELLOW

    def test_burning_cash_alone_holds_an_otherwise_green_company_at_mixed(self):
        assert assess()["overall"] == GREEN
        result = assess(fcf=-1)
        assert result["flags"] == [health.FLAG_BURN]
        assert result["overall"] == YELLOW

    def test_losing_money_alone_caps_at_mixed(self):
        result = assess(net_income=(12, 14, 17, -5))
        assert result["flags"] == [health.FLAG_LOSS]
        assert result["overall"] == YELLOW

    def test_losing_money_and_burning_cash_together_force_weak(self):
        result = assess(net_income=(12, 14, 17, -5), fcf=-1)
        assert result["flags"] == [health.FLAG_LOSS, health.FLAG_BURN]
        assert result["overall"] == RED

    def test_the_reds_are_listed_in_display_order(self):
        result = assess(net_income=(12, 14, 17, -5), fcf=-1)
        assert result["reds"] == ["Return on equity", "Free cash flow margin", "Net income growth", "Earnings consistency"]

    def test_a_weak_company_is_weak(self):
        result = assess(gross_profit=10, operating_income=-5, net_income=(5, 2, -3, -8), fcf=-10, total_debt=400,
                        current_assets=50)
        assert result["overall"] == RED


class TestNotRatedAndMissingData:
    def test_fewer_than_three_measures_is_not_rated(self):
        income = {"Total Revenue": series((90, 100)), "Net Income": series((9, 10))}
        result = health.assess_health(income, {}, {}, "Technology")
        assert result["n_measures"] == 2
        assert result["overall"] is None

    def test_exactly_three_measures_is_rated(self):
        income = {"Total Revenue": series((80, 86, 93, 100)), "Net Income": series((12, 14, 17, 20))}
        result = health.assess_health(income, {}, {}, "Technology")
        assert result["n_measures"] == 3  # revenue growth, net income growth, consistency
        assert result["overall"] == GREEN

    def test_a_loss_maker_with_too_little_data_stays_not_rated(self):
        income = {"Total Revenue": series((90, 100)), "Net Income": series((-9, -10))}
        result = health.assess_health(income, {}, {}, "Technology")
        assert result["overall"] is None
        assert result["flags"] == [health.FLAG_LOSS]

    def test_no_statements_at_all_gives_none(self):
        assert health.assess_health({}, {}, {}, "Financial Services") is None
        assert health.assess_health(None, None, None) is None

    def test_a_cash_flow_statement_alone_is_not_enough(self):
        assert health.assess_health({}, {}, {"Free Cash Flow": {LATEST: 10}}) is None

    def test_a_balance_sheet_alone_is_rated_on_what_it_has(self):
        _, balance, _ = build()
        result = health.assess_health({}, balance, {}, "Technology")
        assert [m["name"] for g in result["groups"].values() for m in g["measures"]] == ["Debt / equity", "Current ratio"]
        assert result["n_measures"] < health.MIN_MEASURES
        assert result["overall"] is None

    def test_nan_figures_are_treated_as_missing_not_as_a_rating(self):
        result = assess(operating_income=NAN, fcf=NAN, ocf=NAN, total_debt=NAN)
        for name in ("Operating margin", "Free cash flow margin", "Debt / equity", "Debt / operating cash flow"):
            assert measure(result, name) is None
        assert result["overall"] == GREEN

    def test_the_input_statements_are_not_modified(self):
        income, balance, cashflow = build()
        before = repr((income, balance, cashflow))
        health.assess_health(income, balance, cashflow, "Technology")
        assert repr((income, balance, cashflow)) == before

    def test_non_dict_statements_are_treated_as_empty(self):
        assert health.assess_health("bad", 3, [], "Technology") is None


class TestFormatting:
    def test_cell_text_per_verdict(self):
        assert health.format_health_cell(assess()) == "🟢 Healthy"
        assert health.format_health_cell(assess(fcf=-1)) == "🟡 Mixed"
        assert health.format_health_cell(assess(net_income=(12, 14, 17, -5), fcf=-1)) == "🔴 Weak"

    def test_cell_text_marks_partial(self):
        result = assess("Financial Services", gross_profit=None, current_assets=None)
        assert health.format_health_cell(result) == "🟢 Healthy (partial)"

    def test_cell_text_for_not_rated_and_for_no_statements(self):
        income = {"Total Revenue": series((90, 100)), "Net Income": series((9, 10))}
        assert health.format_health_cell(health.assess_health(income, {}, {})) == "⚪ Not rated"
        assert health.format_health_cell(None) == "—"

    def test_reasons_list_flags_first_then_each_red_measure_with_its_value(self):
        result = assess(net_income=(12, 14, 17, -5), fcf=-1)
        assert health.health_reasons(result) == [
            "Losing money", "Burning cash", "Return on equity: -5.0%", "Free cash flow margin: -1.0%",
            "Net income growth: -129.4%", "Earnings consistency: 3/4 profitable years"]

    def test_reasons_are_empty_when_nothing_is_red_or_flagged_and_for_none(self):
        assert health.health_reasons(assess()) == []
        assert health.health_reasons(None) == []

    def test_negative_equity_reads_naturally_in_the_reasons(self):
        assert "Debt / equity: negative equity" in health.health_reasons(assess(equity=-50))

    def test_description_of_a_healthy_company(self):
        text = health.describe_health(assess())
        lines = text.splitlines()
        assert lines[0] == "Health: Healthy."
        assert lines[1].startswith("- Profit & cash flow: Healthy -- Gross margin 50.0% (green); Operating margin 20.0% (green)")
        assert any(line.startswith("- Debt & risk: Healthy") for line in lines)
        assert any(line.startswith("- Growth: Healthy") for line in lines)
        assert not any(line.startswith("Watch:") for line in lines)
        assert "Rules used: default profile (standard thresholds)." in lines[-1]
        assert "not investment advice" in lines[-1]

    def test_description_names_what_to_watch(self):
        text = health.describe_health(assess(net_income=(12, 14, 17, -5), fcf=-1))
        assert text.splitlines()[0] == "Health: Weak."
        assert "Watch: Losing money; Burning cash; Return on equity: -5.0%" in text

    def test_description_mentions_the_profile_used(self):
        assert "low_margin profile" in health.describe_health(assess("Consumer Defensive"))

    def test_description_of_a_partial_company(self):
        text = health.describe_health(assess("Financial Services", gross_profit=None, current_assets=None))
        assert "Partial:" in text.splitlines()[0]
        assert "- Debt & risk: no measures available" in text

    def test_description_of_not_rated_and_of_no_statements(self):
        income = {"Total Revenue": series((90, 100)), "Net Income": series((9, 10))}
        assert health.describe_health(health.assess_health(income, {}, {})).startswith(
            "Health: not rated -- only 2 usable measures")
        assert "no financial statements are stored" in health.describe_health(None)
