import re

import pandas as pd
import pytest

from core.fundamentals_summary import normalize_symbol, summarize_fundamentals, summarize_holdings_health

INCOME = {
    "Total Revenue": {"2023-12-31": 900e6, "2024-12-31": 1000e6},
    "Gross Profit": {"2023-12-31": 400e6, "2024-12-31": 420e6},
    "Operating Income": {"2023-12-31": 180e6, "2024-12-31": 200e6},
    "Net Income": {"2023-12-31": 120e6, "2024-12-31": 150e6},
}
BALANCE = {
    "Stockholders Equity": {"2024-12-31": 750e6},
    "Total Debt": {"2023-12-31": 300e6, "2024-12-31": 375e6},
    "Current Assets": {"2024-12-31": 300e6},
    "Current Liabilities": {"2024-12-31": 200e6},
}
CASHFLOW = {"Free Cash Flow": {"2023-12-31": 80e6, "2024-12-31": 100e6}}


def make_row(**overrides):
    """A fundamentals_cache row for a healthy US equity; overrides replace fields."""
    row = {
        "Symbol": "TEST", "Currency": "USD", "Financial Currency": "USD",
        "Current Price": 90.0, "Target Mean Price": 100.0, "Number Of Analysts": 20,
        "Income Statement": INCOME, "Balance Sheet": BALANCE, "Cash Flow": CASHFLOW,
        "Business Summary": "A test company that makes things.", "Industry": "Widgets",
        "Sector": "Industrials", "Employees": 65900, "Country": "United States", "City": "Atlanta",
        "Fetched At": pd.Timestamp("2026-09-09 15:45"),
    }
    row.update(overrides)
    return row


class TestNormalizeSymbol:
    @pytest.mark.parametrize("text, expected", [
        ("ko", "KO"), ("  aapl ", "AAPL"), ("BRK.B", "BRK.B"), ("brk-b", "BRK-B"),
        ("^gspc", "^GSPC"), ("eurusd=x", "EURUSD=X"), ("PG260717C00152500", "PG260717C00152500"),
    ])
    def test_valid_tickers_are_trimmed_and_upper_cased(self, text, expected):
        assert normalize_symbol(text) == expected

    @pytest.mark.parametrize("text", [
        "", "   ", "KO; DROP TABLE trades", "K O", "KO'--", "a" * 21, "the coca cola company", "KO\nAAPL", None, 123,
    ])
    def test_anything_else_is_none(self, text):
        assert normalize_symbol(text) is None


class TestHealthyEquity:
    def setup_method(self):
        self.text = summarize_fundamentals(make_row())

    def test_header_names_the_symbol_and_the_data_date(self):
        assert self.text.splitlines()[0].startswith("TEST -- stored data as of 09/09/2026")

    def test_profile_line(self):
        assert "Industry: Widgets; Sector: Industrials; Employees: 65,900; Market: Atlanta, United States" in self.text

    def test_business_summary_included(self):
        assert "About: A test company that makes things." in self.text

    def test_valuation_says_undervalued_with_the_gap_in_words(self):
        assert "Valuation (analyst target): Undervalued." in self.text
        assert "Current price $90.00 vs mean analyst target $100.00 (20 analysts)" in self.text
        assert "the price is 10.0% below the target" in self.text

    def test_kpis_show_latest_year_in_millions_with_yoy(self):
        assert "Latest fiscal year (ended 2024-12-31):" in self.text
        assert "- Revenue: $1,000M (+11.1% YoY)" in self.text
        assert "- Net income: $150M (+25.0% YoY)" in self.text
        assert "- Free cash flow: $100M (+25.0% YoY)" in self.text
        assert "- Total debt: $375M (+25.0% YoY)" in self.text

    def test_key_ratios(self):
        assert (
            "Key ratios: gross margin 42.0%, operating margin 20.0%, return on equity 20.0%, "
            "debt/equity 0.50x, current ratio 1.50x." in self.text
        )

    def test_no_currency_note_when_currencies_match(self):
        assert "Note:" not in self.text


class TestValuationWording:
    def test_overvalued(self):
        text = summarize_fundamentals(make_row(**{"Current Price": 110.0}))
        assert "Valuation (analyst target): Overvalued." in text
        assert "the price is 10.0% above the target" in text

    def test_fair_value_within_the_band(self):
        text = summarize_fundamentals(make_row(**{"Current Price": 101.0}))
        assert "Valuation (analyst target): Fair value." in text
        assert "within 2% of the target (+1.0%)" in text

    def test_single_analyst_is_singular(self):
        assert "(1 analyst)" in summarize_fundamentals(make_row(**{"Number Of Analysts": 1}))

    def test_missing_analyst_count_is_left_out_not_shown_as_zero(self):
        text = summarize_fundamentals(make_row(**{"Number Of Analysts": None}))
        assert "analyst)" not in text and "analysts)" not in text
        assert "Valuation (analyst target): Undervalued." in text

    def test_no_analyst_target_is_no_coverage_but_still_gives_the_price(self):
        text = summarize_fundamentals(make_row(**{"Target Mean Price": None, "Number Of Analysts": None}))
        assert "Valuation (analyst target): no analyst coverage. Current price $90.00." in text

    def test_nan_target_from_a_multi_row_query_counts_as_no_coverage(self):
        text = summarize_fundamentals(make_row(**{"Target Mean Price": float("nan")}))
        assert "no analyst coverage" in text


class TestEtfOrFund:
    def setup_method(self):
        self.text = summarize_fundamentals(make_row(**{
            "Income Statement": {}, "Balance Sheet": {}, "Cash Flow": {},
            "Target Mean Price": None, "Number Of Analysts": None, "Financial Currency": None,
            "Industry": None, "Sector": None, "Employees": None,
            "Business Summary": "The fund invests at least 80% of its total assets in an index.",
        }))

    def test_says_there_are_no_statements_and_stops_there(self):
        assert "No financial statements available for this symbol (likely an ETF or fund)" in self.text
        assert "Latest fiscal year" not in self.text
        assert "Key ratios" not in self.text

    def test_still_gives_profile_and_price(self):
        assert "About: The fund invests at least 80%" in self.text
        assert "no analyst coverage. Current price $90.00." in self.text

    def test_absent_profile_fields_are_skipped_not_shown_as_none_or_nan(self):
        assert "Industry" not in self.text and "Employees" not in self.text
        assert not re.search(r"(?<![a-z])(none|nan)(?![a-z])", self.text, flags=re.IGNORECASE)  # whole words only: "financial" contains "nan"


class TestCurrencyMismatch:
    def test_warns_and_labels_statement_figures_in_the_statement_currency(self):
        text = summarize_fundamentals(make_row(**{"Currency": "USD", "Financial Currency": "TWD"}))
        assert "statements are reported in TWD but the price is quoted in USD" in text
        assert "- Revenue: TWD 1,000M" in text
        assert "$1,000M" not in text

    def test_non_usd_statements_with_a_matching_quote_currency_are_still_labelled(self):
        text = summarize_fundamentals(make_row(**{"Currency": "EUR", "Financial Currency": "EUR"}))
        assert "Note:" not in text
        assert "- Revenue: EUR 1,000M" in text

    def test_missing_financial_currency_defaults_to_dollars(self):
        assert "- Revenue: $1,000M" in summarize_fundamentals(make_row(**{"Financial Currency": None}))


class TestMissingLineItems:
    def test_a_bank_style_filer_shows_na_for_the_ratios_it_cannot_have(self):
        income = {k: v for k, v in INCOME.items() if k not in ("Gross Profit", "Operating Income")}
        balance = {k: v for k, v in BALANCE.items() if k not in ("Current Assets", "Current Liabilities")}
        text = summarize_fundamentals(make_row(**{"Income Statement": income, "Balance Sheet": balance}))
        assert (
            "Key ratios: gross margin N/A, operating margin N/A, return on equity 20.0%, "
            "debt/equity 0.50x, current ratio N/A." in text
        )

    def test_a_missing_kpi_row_reads_na(self):
        text = summarize_fundamentals(make_row(**{"Cash Flow": {"Capital Expenditure": {"2024-12-31": -5.0}}}))
        assert "- Free cash flow: N/A" in text

    def test_no_yoy_when_the_prior_year_was_not_positive(self):
        cashflow = {"Free Cash Flow": {"2023-12-31": -40e6, "2024-12-31": 25e6}}
        text = summarize_fundamentals(make_row(**{"Cash Flow": cashflow}))
        assert "- Free cash flow: $25M" in text
        assert "- Free cash flow: $25M (" not in text


class TestFormattingEdges:
    def test_long_business_summary_is_cut_at_a_word_boundary(self):
        text = summarize_fundamentals(make_row(**{"Business Summary": "word " * 200}))
        about = next(line for line in text.splitlines() if line.startswith("About: "))
        assert about.endswith("word...")
        assert len(about) <= len("About: ") + 300 + 3

    def test_missing_fetched_at_does_not_crash(self):
        text = summarize_fundamentals(make_row(**{"Fetched At": None}))
        assert "as of an unknown date" in text

    def test_works_on_a_pandas_series_row_with_nan_like_the_real_query_result(self):
        row = pd.Series(make_row(**{"City": float("nan"), "Employees": float("nan")}))
        text = summarize_fundamentals(row)
        assert "Market: United States" in text
        assert "Employees" not in text


def statements(*, revenue=(900e6, 1000e6), net_income=(120e6, 150e6), gross=420e6, operating=200e6,
               equity=750e6, debt=375e6, cur_assets=300e6, cur_liab=200e6, fcf=100e6):
    """{"Income Statement", "Balance Sheet", "Cash Flow"} overrides for make_row; the defaults
    are the healthy company the tests above use. Two fiscal years, latest 2024."""
    def years(values):
        return {"2023-12-31": values[0], "2024-12-31": values[1]}
    return {
        "Income Statement": {
            "Total Revenue": years(revenue), "Net Income": years(net_income),
            "Gross Profit": {"2024-12-31": gross}, "Operating Income": {"2024-12-31": operating},
        },
        "Balance Sheet": {
            "Stockholders Equity": {"2024-12-31": equity}, "Total Debt": {"2024-12-31": debt},
            "Current Assets": {"2024-12-31": cur_assets}, "Current Liabilities": {"2024-12-31": cur_liab},
        },
        "Cash Flow": {"Free Cash Flow": {"2024-12-31": fcf}},
    }


WEAK = statements(revenue=(1000e6, 800e6), net_income=(10e6, -60e6), gross=100e6, operating=-50e6,
                  equity=200e6, debt=600e6, cur_assets=100e6, cur_liab=200e6, fcf=-40e6)
HIGH_DEBT = statements(debt=1875e6)                                   # debt/equity 2.5x, everything else fine
MIDDLING = statements(revenue=(1000e6, 1000e6), net_income=(150e6, 150e6), debt=1875e6)   # flat + high debt
ALL_YELLOW = statements(revenue=(1000e6, 1000e6), net_income=(80e6, 80e6), gross=150e6, operating=50e6, fcf=40e6)
FUND = {"Income Statement": {}, "Balance Sheet": {}, "Cash Flow": {}, "Target Mean Price": None,
        "Number Of Analysts": None, "Sector": None, "Industry": None}
BANK = {
    "Income Statement": {k: v for k, v in INCOME.items() if k not in ("Gross Profit", "Operating Income")},
    "Balance Sheet": {k: v for k, v in BALANCE.items() if k not in ("Current Assets", "Current Liabilities")},
}


class TestHealthInTheCompanyAnswer:
    def test_a_healthy_company_gets_a_health_block_after_the_key_ratios(self):
        lines = summarize_fundamentals(make_row()).splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("Key ratios:"))
        assert lines[index + 1] == "Health: Healthy."
        assert any(line.startswith("- Profit & cash flow: Healthy -- Gross margin 42.0% (green)") for line in lines)
        assert lines[-1].startswith("Rules used: cyclical profile")

    def test_a_weak_company_says_weak_and_what_to_watch(self):
        text = summarize_fundamentals(make_row(**WEAK))
        assert "Health: Weak." in text
        assert "Watch: Losing money; Burning cash;" in text

    def test_the_sector_picks_the_rule_profile(self):
        assert "Rules used: default profile" in summarize_fundamentals(make_row(Sector="Technology"))
        assert "Rules used: leveraged profile" in summarize_fundamentals(make_row(Sector="Utilities"))

    def test_an_etf_or_fund_gets_no_health_block(self):
        assert "Health" not in summarize_fundamentals(make_row(**FUND))

    def test_a_nan_sector_from_a_multi_row_query_does_not_break_it(self):
        assert "Health: Healthy." in summarize_fundamentals(make_row(Sector=float("nan"), Industry=float("nan")))

    def test_a_bank_style_filer_is_marked_partial(self):
        text = summarize_fundamentals(make_row(**BANK))
        assert next(line for line in text.splitlines() if line.startswith("Health:")).count("Partial:") == 1


class TestSummarizeHoldingsHealth:
    def cache(self, **rows):
        """A fundamentals_cache frame: each keyword is a symbol, its value that row's overrides."""
        return pd.DataFrame([make_row(Symbol=symbol, **overrides) for symbol, overrides in rows.items()])

    def test_groups_holdings_and_gives_reasons_for_weak_and_mixed(self):
        text = summarize_holdings_health(
            ["BAD", "MEH", "GOOD", "FUND", "NEW"],
            self.cache(GOOD={}, MEH=MIDDLING, BAD=WEAK, FUND=FUND),
        )
        lines = text.splitlines()
        assert lines[0].startswith("Health of your 5 current holdings")
        assert "Weak (1):" in lines
        assert any(line.startswith("- BAD: Losing money; Burning cash;") for line in lines)
        assert "Mixed (1):" in lines
        assert "- MEH: Debt / equity: 2.50x" in lines
        assert "Healthy (1): GOOD." in lines
        assert "No financial statements, so not rated -- ETFs/funds (1): FUND." in lines
        assert any(line.startswith("Not stored yet, so not rated (1): NEW") for line in lines)

    def test_says_none_when_a_group_is_empty(self):
        lines = summarize_holdings_health(["GOOD"], self.cache(GOOD={})).splitlines()
        assert "Weak: none." in lines
        assert "Mixed: none." in lines
        assert "Healthy (1): GOOD." in lines

    def test_a_mixed_holding_with_nothing_red_says_so(self):
        text = summarize_holdings_health(["YEL"], self.cache(YEL=ALL_YELLOW))
        assert "- YEL: no single red measure, several middling readings" in text.splitlines()

    def test_a_healthy_holding_with_one_red_measure_is_called_out(self):
        text = summarize_holdings_health(["GOOD", "DEBT"], self.cache(GOOD={}, DEBT=HIGH_DEBT))
        assert "Healthy (2): DEBT, GOOD." in text.splitlines()
        assert "Healthy but with a red measure: DEBT (Debt / equity: 2.50x)." in text.splitlines()

    def test_no_callout_line_when_no_healthy_holding_has_a_red_measure(self):
        assert "Healthy but" not in summarize_holdings_health(["GOOD"], self.cache(GOOD={}))

    def test_states_the_date_range_of_the_stored_data(self):
        cache = self.cache(A={"Fetched At": pd.Timestamp("2026-08-01")}, B={"Fetched At": pd.Timestamp("2026-09-09")})
        assert "stored 01/08/2026 to 09/09/2026" in summarize_holdings_health(["A", "B"], cache)

    def test_funds_do_not_count_toward_the_date_range_or_the_statement_count(self):
        cache = self.cache(A={}, FUND=dict(FUND, **{"Fetched At": pd.Timestamp("2020-01-01")}))
        text = summarize_holdings_health(["A", "FUND"], cache)
        assert "1 have statements (stored 09/09/2026 to 09/09/2026)" in text
        assert "2020" not in text

    def test_partial_statements_are_marked_and_explained(self):
        lines = summarize_holdings_health(["BANK"], self.cache(BANK=BANK)).splitlines()
        assert "Healthy (1): BANK (partial)." in lines
        assert any(line.startswith("(partial) = bank/lender/fund-shaped") for line in lines)

    def test_too_little_data_is_listed_as_not_rated(self):
        thin = {"Income Statement": {"Total Revenue": {"2023-12-31": 90.0, "2024-12-31": 100.0}}, "Balance Sheet": {},
                "Cash Flow": {}}
        text = summarize_holdings_health(["THIN"], self.cache(THIN=thin))
        assert "Not rated, too little data (1): THIN." in text.splitlines()
        assert "Healthy (0): none." in text.splitlines()

    def test_an_empty_cache_names_every_holding_as_not_stored_without_crashing(self):
        text = summarize_holdings_health(["A", "B"], pd.DataFrame())
        assert "Not stored yet, so not rated (2): A, B" in text
        assert "Weak: none." in text and "Mixed: none." in text
        assert "have statements" not in text

    def test_no_holdings(self):
        assert summarize_holdings_health([], self.cache(GOOD={})) == "No current holdings."

    def test_symbols_are_deduplicated_and_listed_alphabetically(self):
        cache = self.cache(B_BAD=WEAK, A_BAD=WEAK)
        text = summarize_holdings_health(["B_BAD", "A_BAD", "A_BAD"], cache)
        assert text.splitlines()[0].startswith("Health of your 2 current holdings")
        assert text.index("- A_BAD:") < text.index("- B_BAD:")

    def test_symbols_in_the_cache_but_not_held_are_ignored(self):
        text = summarize_holdings_health(["GOOD"], self.cache(GOOD={}, BAD=WEAK))
        assert "BAD" not in text

    def test_matches_the_per_company_answer(self):
        # The list and the single-company answer must give one verdict per company.
        for overrides, label in (({}, "Healthy"), (WEAK, "Weak"), (MIDDLING, "Mixed")):
            assert f"Health: {label}." in summarize_fundamentals(make_row(**overrides))
            listing = summarize_holdings_health(["X"], self.cache(X=overrides))
            assert f"{label} (1)" in listing
