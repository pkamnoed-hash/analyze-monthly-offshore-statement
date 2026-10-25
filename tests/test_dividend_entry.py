import sqlite3

import pandas as pd
import pytest

from core import db
from core.dividend_entry import (
    describe_dividend_batch_preview,
    match_against_existing_dividends,
    preview_dividend_batch,
    record_dividend_batch,
    validate_dividend_rows,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


def row_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestValidateDividendRows:
    def test_valid_dividend_row_passes(self):
        valid, errors = validate_dividend_rows(
            [{"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "GOOY",
              "gross_amount": 6.52, "withholding_tax": 0.97}]
        )
        assert errors == []
        assert valid[0]["net_amount"] == 5.55

    def test_net_amount_rounds_cleanly_not_float_noise(self):
        valid, errors = validate_dividend_rows(
            [{"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "GOOY",
              "gross_amount": 6.52, "withholding_tax": 0.97}]
        )
        assert errors == []
        assert valid[0]["net_amount"] == round(6.52 - 0.97, 2)

    def test_interest_row_without_symbol_is_valid(self):
        valid, errors = validate_dividend_rows(
            [{"trade_date": "2026-10-01", "entry_type": "Interest", "gross_amount": 1.23}]
        )
        assert errors == []
        assert valid[0]["symbol"] is None

    def test_non_interest_without_symbol_is_rejected(self):
        valid, errors = validate_dividend_rows(
            [{"trade_date": "2026-10-01", "entry_type": "Dividend", "gross_amount": 1.23}]
        )
        assert valid == []
        assert "symbol" in errors[0]

    def test_unknown_entry_type_rejected(self):
        valid, errors = validate_dividend_rows(
            [{"trade_date": "2026-10-01", "entry_type": "Bonus", "symbol": "AAPL", "gross_amount": 1.0}]
        )
        assert valid == []
        assert "entry_type" in errors[0]

    def test_non_positive_gross_amount_rejected(self):
        valid, errors = validate_dividend_rows(
            [{"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "AAPL", "gross_amount": 0}]
        )
        assert valid == []
        assert "gross_amount" in errors[0]


class TestMatchAgainstExistingDividends:
    def test_row_already_in_db_is_matched(self):
        candidates = pd.DataFrame([{
            "trade_date": pd.Timestamp("2026-10-01"), "entry_type": "Dividend", "symbol": "GOOY",
            "gross_amount": 6.52, "withholding_tax": 0.97, "net_amount": 5.55,
        }])
        db_dividends = pd.DataFrame([{
            "Trade Date": pd.Timestamp("2026-10-01"), "Symbol": "GOOY", "Entry Type": "Dividend", "Net Amt": 5.55,
        }])
        result = match_against_existing_dividends(candidates, db_dividends)
        assert result["matched"].tolist() == [True]

    def test_duplicate_within_batch_is_flagged(self):
        row = {
            "trade_date": pd.Timestamp("2026-10-01"), "entry_type": "Dividend", "symbol": "GOOY",
            "gross_amount": 6.52, "withholding_tax": 0.97, "net_amount": 5.55,
        }
        candidates = pd.DataFrame([row, row])
        db_dividends = pd.DataFrame(columns=["Trade Date", "Symbol", "Entry Type", "Net Amt"])
        result = match_against_existing_dividends(candidates, db_dividends)
        assert result["duplicate_in_batch"].tolist() == [False, True]


class TestPreviewDividendBatch:
    def test_new_batch_writes_nothing(self, conn):
        before = row_count(conn, "dividends")
        rows = [{"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "GOOY",
                  "gross_amount": 6.52, "withholding_tax": 0.97}]
        result = preview_dividend_batch(rows, conn=conn)
        assert len(result["new"]) == 1
        assert row_count(conn, "dividends") == before

    def test_already_recorded_row_is_skipped(self, conn):
        db.insert_dividend(trade_date="2026-10-01", entry_type="Dividend", net_amount=5.55, symbol="GOOY", conn=conn)
        rows = [{"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "GOOY",
                  "gross_amount": 6.52, "withholding_tax": 0.97}]
        result = preview_dividend_batch(rows, conn=conn)
        assert result["new"] == []
        assert len(result["already_recorded"]) == 1
        assert result["already_recorded"][0]["reason"] == "already recorded"

    def test_describe_preview_mentions_skip_and_new(self, conn):
        db.insert_dividend(trade_date="2026-10-01", entry_type="Dividend", net_amount=5.55, symbol="GOOY", conn=conn)
        rows = [
            {"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "GOOY",
             "gross_amount": 6.52, "withholding_tax": 0.97},
            {"trade_date": "2026-10-05", "entry_type": "Interest", "gross_amount": 1.23},
        ]
        result = preview_dividend_batch(rows, conn=conn)
        text = describe_dividend_batch_preview(result)
        assert "already recorded" in text
        assert "Interest" in text
        assert "confirm" in text.lower()


class TestRecordDividendBatch:
    def test_confirm_writes_exactly_the_new_rows(self, conn):
        db.insert_dividend(trade_date="2026-10-01", entry_type="Dividend", net_amount=5.55, symbol="GOOY", conn=conn)
        before = row_count(conn, "dividends")
        rows = [
            {"trade_date": "2026-10-01", "entry_type": "Dividend", "symbol": "GOOY",
             "gross_amount": 6.52, "withholding_tax": 0.97},  # dup, skip
            {"trade_date": "2026-10-05", "entry_type": "Interest", "gross_amount": 1.23},  # new
        ]
        result = record_dividend_batch(rows, conn=conn)
        assert len(result["recorded"]) == 1
        assert len(result["skipped_already_recorded"]) == 1
        assert result["failed"] == []
        assert row_count(conn, "dividends") == before + 1

        dividends = db.fetch_dividends(conn=conn)
        new_row = dividends[dividends["Entry Type"] == "Interest"].iloc[0]
        assert new_row["Net Amt"] == 1.23

    def test_values_match_what_insert_dividend_would_produce(self, conn):
        rows = [{"trade_date": "2026-10-05", "entry_type": "Capital Distribution", "symbol": "AAPL",
                  "gross_amount": 10.0, "withholding_tax": 0.0}]
        record_dividend_batch(rows, conn=conn)

        conn2 = sqlite3.connect(":memory:")
        db.init_db(conn=conn2)
        db.insert_dividend(trade_date="2026-10-05", entry_type="Capital Distribution", net_amount=10.0, symbol="AAPL", conn=conn2)

        a = db.fetch_dividends(conn=conn).drop(columns=["id"])
        b = db.fetch_dividends(conn=conn2).drop(columns=["id"])
        pd.testing.assert_frame_equal(a, b)
        conn2.close()
