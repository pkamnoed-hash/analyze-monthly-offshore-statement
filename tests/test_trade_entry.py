import sqlite3

import pandas as pd
import pytest

from core import db
from core.trade_entry import (
    apply_sequential_position_check,
    describe_trade_batch_preview,
    match_against_existing_trades,
    preview_trade_batch,
    record_trade_batch,
    validate_trade_rows,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


def row_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestValidateTradeRows:
    def test_valid_row_passes(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 10, "price": 150.0}]
        )
        assert errors == []
        assert len(valid) == 1
        assert valid[0]["side"] == "buy"
        assert valid[0]["symbol"] == "AAPL"

    def test_side_is_case_insensitive(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "2026-10-01", "side": "SELL", "symbol": "AAPL", "quantity": 1, "price": 1}]
        )
        assert errors == []
        assert valid[0]["side"] == "sell"

    def test_invalid_side_is_rejected_not_silently_treated_as_sell(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "2026-10-01", "side": "byu", "symbol": "AAPL", "quantity": 1, "price": 1}]
        )
        assert valid == []
        assert len(errors) == 1
        assert "side" in errors[0]

    def test_non_positive_quantity_rejected(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 0, "price": 1}]
        )
        assert valid == []
        assert "quantity" in errors[0]

    def test_non_positive_price_rejected(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 1, "price": -5}]
        )
        assert valid == []
        assert "price" in errors[0]

    def test_bad_symbol_rejected(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "2026-10-01", "side": "buy", "symbol": "not a ticker!!", "quantity": 1, "price": 1}]
        )
        assert valid == []
        assert "symbol" in errors[0]

    def test_unparseable_date_rejected(self):
        valid, errors = validate_trade_rows(
            [{"trade_date": "not a date", "side": "buy", "symbol": "AAPL", "quantity": 1, "price": 1}]
        )
        assert valid == []
        assert len(errors) == 1

    def test_one_bad_row_does_not_drop_a_good_row(self):
        valid, errors = validate_trade_rows([
            {"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 1, "price": 1},
            {"trade_date": "2026-10-01", "side": "byu", "symbol": "AAPL", "quantity": 1, "price": 1},
        ])
        assert len(valid) == 1
        assert len(errors) == 1
        assert "row 1" in errors[0]


class TestMatchAgainstExistingTrades:
    def test_new_row_is_not_matched(self):
        candidates = pd.DataFrame([{
            "trade_date": pd.Timestamp("2026-10-01"), "side": "buy", "symbol": "AAPL",
            "quantity": 10.0, "price": 150.0,
        }])
        db_trades = pd.DataFrame(columns=["Trade Date", "Symbol", "Quantity", "Price"])
        result = match_against_existing_trades(candidates, db_trades)
        assert result["matched"].tolist() == [False]
        assert result["duplicate_in_batch"].tolist() == [False]

    def test_row_already_in_db_is_matched(self):
        candidates = pd.DataFrame([{
            "trade_date": pd.Timestamp("2026-10-01"), "side": "buy", "symbol": "AAPL",
            "quantity": 10.0, "price": 150.0,
        }])
        db_trades = pd.DataFrame([{
            "Trade Date": pd.Timestamp("2026-10-01"), "Symbol": "AAPL", "Quantity": 10.0, "Price": 150.0,
        }])
        result = match_against_existing_trades(candidates, db_trades)
        assert result["matched"].tolist() == [True]

    def test_duplicate_within_batch_is_flagged(self):
        row = {
            "trade_date": pd.Timestamp("2026-10-01"), "side": "buy", "symbol": "AAPL",
            "quantity": 10.0, "price": 150.0,
        }
        candidates = pd.DataFrame([row, row])
        db_trades = pd.DataFrame(columns=["Trade Date", "Symbol", "Quantity", "Price"])
        result = match_against_existing_trades(candidates, db_trades)
        assert result["duplicate_in_batch"].tolist() == [False, True]


class TestApplySequentialPositionCheck:
    def test_sell_within_batch_buy_is_not_oversell(self):
        candidates = pd.DataFrame([
            {"trade_date": pd.Timestamp("2026-10-01"), "side": "buy", "symbol": "AAPL", "quantity": 10.0, "price": 100.0},
            {"trade_date": pd.Timestamp("2026-10-02"), "side": "sell", "symbol": "AAPL", "quantity": 5.0, "price": 110.0},
        ])
        db_trades = pd.DataFrame(columns=["Trade Date", "Symbol", "Entry Type", "Side", "Quantity", "Price", "Amount", "Commission", "Month"])
        result = apply_sequential_position_check(candidates, db_trades)
        assert result["is_oversell"].tolist() == [False, False]

    def test_sell_more_than_batch_buy_is_oversell(self):
        candidates = pd.DataFrame([
            {"trade_date": pd.Timestamp("2026-10-01"), "side": "buy", "symbol": "AAPL", "quantity": 10.0, "price": 100.0},
            {"trade_date": pd.Timestamp("2026-10-02"), "side": "sell", "symbol": "AAPL", "quantity": 15.0, "price": 110.0},
        ])
        db_trades = pd.DataFrame(columns=["Trade Date", "Symbol", "Entry Type", "Side", "Quantity", "Price", "Amount", "Commission", "Month"])
        result = apply_sequential_position_check(candidates, db_trades)
        assert result["is_oversell"].tolist() == [False, True]

    def test_sell_covered_by_existing_db_position_is_not_oversell(self):
        candidates = pd.DataFrame([
            {"trade_date": pd.Timestamp("2026-10-02"), "side": "sell", "symbol": "AAPL", "quantity": 5.0, "price": 110.0},
        ])
        db_trades = pd.DataFrame([{
            "Trade Date": pd.Timestamp("2026-10-01"), "Symbol": "AAPL", "Entry Type": "Trade Entry",
            "Side": "buy", "Quantity": 10.0, "Price": 100.0, "Amount": -1000.0, "Commission": 0.0,
            "Month": pd.Timestamp("2026-10-01"),
        }])
        result = apply_sequential_position_check(candidates, db_trades)
        assert result["is_oversell"].tolist() == [False]


class TestPreviewTradeBatch:
    def test_new_batch_writes_nothing(self, conn):
        before = row_count(conn, "trades")
        rows = [{"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 10, "price": 150.0}]
        result = preview_trade_batch(rows, conn=conn)
        assert len(result["new"]) == 1
        assert row_count(conn, "trades") == before

    def test_already_recorded_row_is_skipped(self, conn):
        db.insert_trade(trade_date="2026-10-01", side="buy", symbol="AAPL", quantity=10, price=150.0, conn=conn)
        rows = [{"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 10, "price": 150.0}]
        result = preview_trade_batch(rows, conn=conn)
        assert result["new"] == []
        assert len(result["already_recorded"]) == 1
        assert result["already_recorded"][0]["reason"] == "already recorded"

    def test_describe_preview_mentions_skip_and_new(self, conn):
        db.insert_trade(trade_date="2026-10-01", side="buy", symbol="AAPL", quantity=10, price=150.0, conn=conn)
        rows = [
            {"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 10, "price": 150.0},
            {"trade_date": "2026-10-05", "side": "buy", "symbol": "MSFT", "quantity": 3, "price": 300.0},
        ]
        result = preview_trade_batch(rows, conn=conn)
        text = describe_trade_batch_preview(result)
        assert "already recorded" in text
        assert "MSFT" in text
        assert "confirm" in text.lower()


class TestRecordTradeBatch:
    def test_confirm_writes_exactly_the_new_rows(self, conn):
        db.insert_trade(trade_date="2026-10-01", side="buy", symbol="AAPL", quantity=10, price=150.0, conn=conn)
        before = row_count(conn, "trades")
        rows = [
            {"trade_date": "2026-10-01", "side": "buy", "symbol": "AAPL", "quantity": 10, "price": 150.0},  # dup, skip
            {"trade_date": "2026-10-05", "side": "buy", "symbol": "MSFT", "quantity": 3, "price": 300.0},  # new
        ]
        result = record_trade_batch(rows, conn=conn)
        assert len(result["recorded"]) == 1
        assert len(result["skipped_already_recorded"]) == 1
        assert result["failed"] == []
        assert row_count(conn, "trades") == before + 1

        trades = db.fetch_trades(conn=conn)
        new_row = trades[trades["Symbol"] == "MSFT"].iloc[0]
        assert new_row["Quantity"] == 3
        assert new_row["Price"] == 300.0
        assert new_row["source"] == "manual"

    def test_values_match_what_insert_trade_would_produce(self, conn):
        rows = [{"trade_date": "2026-10-05", "side": "sell", "symbol": "NVDA", "quantity": 2, "price": 500.0}]
        record_trade_batch(rows, conn=conn)

        conn2 = sqlite3.connect(":memory:")
        db.init_db(conn=conn2)
        db.insert_trade(trade_date="2026-10-05", side="sell", symbol="NVDA", quantity=2, price=500.0, source="manual", conn=conn2)

        a = db.fetch_trades(conn=conn).drop(columns=["id"])
        b = db.fetch_trades(conn=conn2).drop(columns=["id"])
        pd.testing.assert_frame_equal(a, b)
        conn2.close()
