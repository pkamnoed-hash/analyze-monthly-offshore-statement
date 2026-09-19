import sqlite3

import pandas as pd
import pytest

from core import db
from core.fundamentals_capture import capture_fundamentals


class FakeTicker:
    def __init__(self, info):
        self.info = info
        # Real yfinance returns empty DataFrames for a fund with no statements.
        self.income_stmt = pd.DataFrame()
        self.balance_sheet = pd.DataFrame()
        self.cashflow = pd.DataFrame()


class FakeYfModule:
    """Serves the given {symbol: info dict}; a symbol not in the mapping raises, like a
    real lookup of something Yahoo doesn't know. Records every symbol it was asked for."""

    def __init__(self, infos: dict):
        self._infos = infos
        self.requested = []

    def Ticker(self, symbol):
        self.requested.append(symbol)
        if symbol not in self._infos:
            raise RuntimeError(f"404 Not Found: {symbol}")
        return FakeTicker(self._infos[symbol])


NOW_INFO = {
    "currentPrice": 810.5, "targetMeanPrice": 1000.0, "numberOfAnalystOpinions": 45,
    "currency": "USD", "financialCurrency": "USD", "industry": "Software - Application",
    "sector": "Technology", "longBusinessSummary": "ServiceNow provides workflow software.",
    "fullTimeEmployees": 26000, "country": "United States", "city": "Santa Clara",
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


def stored(conn, symbol):
    cached = db.fetch_fundamentals_cache(conn=conn)
    match = cached[cached["Symbol"] == symbol]
    return None if match.empty else match.iloc[0]


class TestCaptureFundamentals:
    def test_a_successful_fetch_is_saved_and_reports_true(self, conn):
        assert capture_fundamentals("NOW", conn=conn, yf_module=FakeYfModule({"NOW": NOW_INFO})) is True

        row = stored(conn, "NOW")
        assert row["Current Price"] == pytest.approx(810.5)
        assert row["Target Mean Price"] == pytest.approx(1000.0)
        assert row["Industry"] == "Software - Application"
        assert row["Business Summary"] == "ServiceNow provides workflow software."

    def test_only_the_requested_symbol_is_fetched_and_saved(self, conn):
        yf = FakeYfModule({"NOW": NOW_INFO, "KO": dict(NOW_INFO, currentPrice=88.0)})
        capture_fundamentals("NOW", conn=conn, yf_module=yf)

        assert yf.requested == ["NOW"]
        assert list(db.fetch_fundamentals_cache(conn=conn)["Symbol"]) == ["NOW"]

    def test_a_symbol_yahoo_does_not_know_saves_nothing(self, conn):
        assert capture_fundamentals("PG260717C00152500", conn=conn, yf_module=FakeYfModule({})) is False
        assert db.fetch_fundamentals_cache(conn=conn).empty

    def test_a_response_with_no_price_saves_nothing(self, conn):
        # yfinance can answer with an info dict that has no price fields at all.
        no_price = {"industry": "Software", "longBusinessSummary": "Something."}
        assert capture_fundamentals("ODD", conn=conn, yf_module=FakeYfModule({"ODD": no_price})) is False
        assert db.fetch_fundamentals_cache(conn=conn).empty

    def test_a_fund_with_a_price_but_no_statements_or_targets_is_saved_not_treated_as_a_failure(self, conn):
        etf_info = {"regularMarketPrice": 64.16, "currency": "USD", "longBusinessSummary": "The fund invests in an index."}
        assert capture_fundamentals("AIQ", conn=conn, yf_module=FakeYfModule({"AIQ": etf_info})) is True

        row = stored(conn, "AIQ")
        assert row["Current Price"] == pytest.approx(64.16)
        assert pd.isna(row["Target Mean Price"])
        assert row["Income Statement"] == {}

    def test_refresh_replaces_an_existing_row_on_success(self, conn):
        capture_fundamentals("NOW", conn=conn, yf_module=FakeYfModule({"NOW": NOW_INFO}))
        capture_fundamentals("NOW", conn=conn, yf_module=FakeYfModule({"NOW": dict(NOW_INFO, currentPrice=905.25)}))

        cached = db.fetch_fundamentals_cache(conn=conn)
        assert len(cached) == 1
        assert cached.iloc[0]["Current Price"] == pytest.approx(905.25)

    def test_a_failed_refresh_leaves_the_existing_row_exactly_as_it_was(self, conn):
        capture_fundamentals("NOW", conn=conn, yf_module=FakeYfModule({"NOW": NOW_INFO}))
        before = stored(conn, "NOW")

        assert capture_fundamentals("NOW", conn=conn, yf_module=FakeYfModule({})) is False

        after = stored(conn, "NOW")
        assert after["Current Price"] == pytest.approx(810.5)
        assert after["Industry"] == "Software - Application"
        assert after["Fetched At"] == before["Fetched At"]

    def test_only_fundamentals_cache_is_written(self, conn):
        conn.execute("INSERT INTO symbol_types (symbol, allocation_type) VALUES ('KO', 'Dividend')")
        conn.commit()
        tables_before = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        }

        capture_fundamentals("NOW", conn=conn, yf_module=FakeYfModule({"NOW": NOW_INFO}))

        tables_after = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables_before}
        changed = {name for name in tables_before if tables_before[name] != tables_after[name]}
        assert changed == {"fundamentals_cache"}
