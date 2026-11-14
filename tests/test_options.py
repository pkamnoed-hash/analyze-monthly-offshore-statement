import pandas as pd

from core.options import build_option_symbol, parse_option_symbol


class TestParseOptionSymbol:
    def test_decodes_real_historical_symbols(self):
        cases = [
            ("ONDS260130P00012000", "ONDS", "2026-01-30", "Put", 12.0),
            ("GDX260320P00088000", "GDX", "2026-03-20", "Put", 88.0),
            ("SPY260430P00510000", "SPY", "2026-04-30", "Put", 510.0),
            ("PLTR260717P00105000", "PLTR", "2026-07-17", "Put", 105.0),
            ("PG260717C00152500", "PG", "2026-07-17", "Call", 152.5),
            ("AVGO261002C00362500", "AVGO", "2026-10-02", "Call", 362.5),
        ]
        for symbol, underlying, exp, option_type, strike in cases:
            result = parse_option_symbol(symbol)
            assert result["underlying"] == underlying, symbol
            assert result["expiration_date"] == pd.Timestamp(exp), symbol
            assert result["option_type"] == option_type, symbol
            assert result["strike"] == strike, symbol

    def test_single_digit_dollar_strike(self):
        result = parse_option_symbol("ONDS260130P00008000")
        assert result["strike"] == 8.0

    def test_strike_with_cents(self):
        result = parse_option_symbol("PG260717C00152570")
        assert result["strike"] == 152.57

    def test_plain_stock_ticker_is_not_an_option(self):
        assert parse_option_symbol("AAPL") is None

    def test_malformed_symbol_rejected(self):
        assert parse_option_symbol("MNST26121C00045000") is None  # expiration digit missing
        assert parse_option_symbol("MNST261218X00045000") is None  # neither C nor P
        assert parse_option_symbol("mnst261218c00045000") is None  # lowercase

    def test_non_string_input_rejected(self):
        assert parse_option_symbol(None) is None
        assert parse_option_symbol(12345) is None

    def test_invalid_calendar_date_rejected(self):
        assert parse_option_symbol("MNST261332C00045000") is None  # month 13


class TestBuildOptionSymbol:
    def test_builds_real_historical_symbols(self):
        assert build_option_symbol("ONDS", "2026-01-30", "Put", 12.0) == "ONDS260130P00012000"
        assert build_option_symbol("PG", "2026-07-17", "Call", 152.5) == "PG260717C00152500"

    def test_is_inverse_of_parse(self):
        original = "MNST261218C00045000"
        decoded = parse_option_symbol(original)
        rebuilt = build_option_symbol(
            decoded["underlying"], decoded["expiration_date"], decoded["option_type"], decoded["strike"],
        )
        assert rebuilt == original

    def test_lowercase_underlying_and_put_normalize(self):
        assert build_option_symbol("mnst", "2026-12-18", "put", 45.0) == "MNST261218P00045000"
