import re

import pandas as pd
import pytest

from core.fundamentals_summary import normalize_symbol, summarize_fundamentals

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
