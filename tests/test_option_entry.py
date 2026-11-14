import sqlite3

import pandas as pd
import pytest

from core import db
from core.option_entry import (
    apply_overclose_check,
    describe_option_batch_preview,
    match_against_existing_option_trades,
    preview_option_batch,
    record_option_batch,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


def row_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _candidate(**overrides):
    row = {
        "trade_date": pd.Timestamp("2026-11-12"), "symbol": "MNST261218C00045000",
        "underlying": "MNST", "option_type": "Call", "strike": 45.0,
        "expiration_date": pd.Timestamp("2026-12-18"), "side": "buy", "position_effect": "open",
        "contracts": 1.0, "price": 1.80,
        "commission": 0.55, "vat": 0.04, "occ_fee": 0.03, "orf_fee": 0.02, "taf_fee": 0.0, "sec_fee": 0.0,
    }
    row.update(overrides)
    return row


class TestMatchAgainstExistingOptionTrades:
    def test_new_row_is_not_matched(self):
        candidates = pd.DataFrame([_candidate()])
        db_options = pd.DataFrame(columns=["trade_date", "symbol", "side", "position_effect", "contracts", "price"])
        result = match_against_existing_option_trades(candidates, db_options)
        assert result["matched"].tolist() == [False]
        assert result["duplicate_in_batch"].tolist() == [False]

    def test_row_already_in_db_is_matched(self):
        candidates = pd.DataFrame([_candidate()])
        db_options = pd.DataFrame([{
            "trade_date": pd.Timestamp("2026-11-12"), "symbol": "MNST261218C00045000",
            "side": "buy", "position_effect": "open", "contracts": 1.0, "price": 1.80,
        }])
        result = match_against_existing_option_trades(candidates, db_options)
        assert result["matched"].tolist() == [True]

    def test_an_open_and_a_close_on_the_same_date_price_are_not_confused(self):
        # position_effect is what must tell these apart -- a naive key without it
        # would treat the close as "already recorded" because the open matches.
        candidates = pd.DataFrame([_candidate(position_effect="close", side="sell")])
        db_options = pd.DataFrame([{
            "trade_date": pd.Timestamp("2026-11-12"), "symbol": "MNST261218C00045000",
            "side": "buy", "position_effect": "open", "contracts": 1.0, "price": 1.80,
        }])
        result = match_against_existing_option_trades(candidates, db_options)
        assert result["matched"].tolist() == [False]

    def test_duplicate_within_batch_is_flagged(self):
        row = _candidate()
        candidates = pd.DataFrame([row, row])
        db_options = pd.DataFrame(columns=["trade_date", "symbol", "side", "position_effect", "contracts", "price"])
        result = match_against_existing_option_trades(candidates, db_options)
        assert result["duplicate_in_batch"].tolist() == [False, True]


class TestApplyOvercloseCheck:
    def test_close_within_open_contracts_is_not_overclose(self):
        candidates = pd.DataFrame([
            _candidate(position_effect="open", side="buy", contracts=10.0, amount=-8000.0),
            _candidate(trade_date=pd.Timestamp("2026-11-13"), position_effect="close", side="sell", contracts=4.0, amount=4600.0),
        ])
        db_options = pd.DataFrame(columns=db.OPTION_TRADE_COLUMNS)
        result = apply_overclose_check(candidates, db_options)
        assert result["is_overclose"].tolist() == [False, False]

    def test_close_exceeding_batch_open_is_overclose(self):
        candidates = pd.DataFrame([
            _candidate(position_effect="open", side="buy", contracts=1.0, amount=-180.64),
            _candidate(trade_date=pd.Timestamp("2026-11-13"), position_effect="close", side="sell", contracts=5.0, amount=1000.0),
        ])
        db_options = pd.DataFrame(columns=db.OPTION_TRADE_COLUMNS)
        result = apply_overclose_check(candidates, db_options)
        assert result["is_overclose"].tolist() == [False, True]

    def test_close_covered_by_existing_db_open_is_not_overclose(self):
        candidates = pd.DataFrame([
            _candidate(trade_date=pd.Timestamp("2026-11-13"), position_effect="close", side="sell", contracts=1.0, amount=234.35),
        ])
        db_options = pd.DataFrame([{
            "trade_date": pd.Timestamp("2026-11-12"), "symbol": "MNST261218C00045000",
            "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": pd.Timestamp("2026-12-18"), "side": "buy", "position_effect": "open",
            "contracts": 1.0, "price": 1.80, "amount": -180.64,
        }])
        result = apply_overclose_check(candidates, db_options)
        assert result["is_overclose"].tolist() == [False]


class TestPreviewOptionBatch:
    def test_new_batch_writes_nothing(self, conn):
        before = row_count(conn, "option_trades")
        rows = [{
            "trade_date": "2026-11-12", "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
            "contracts": 1, "price": 1.80, "commission": 0.55, "vat": 0.04, "occ_fee": 0.03, "orf_fee": 0.02,
        }]
        result = preview_option_batch(rows, conn=conn)
        assert len(result["new"]) == 1
        assert row_count(conn, "option_trades") == before

    def test_already_recorded_row_is_skipped(self, conn):
        db.insert_option_trade(
            trade_date="2026-11-12", symbol="MNST261218C00045000", underlying="MNST",
            option_type="Call", strike=45.0, expiration_date="2026-12-18",
            side="buy", position_effect="open", contracts=1, price=1.80,
            commission=0.55, vat=0.04, occ_fee=0.03, orf_fee=0.02, conn=conn,
        )
        rows = [{
            "trade_date": "2026-11-12", "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
            "contracts": 1, "price": 1.80, "commission": 0.55, "vat": 0.04, "occ_fee": 0.03, "orf_fee": 0.02,
        }]
        result = preview_option_batch(rows, conn=conn)
        assert result["new"] == []
        assert len(result["already_recorded"]) == 1
        assert result["already_recorded"][0]["reason"] == "already recorded"

    def test_overclosing_row_is_flagged_not_blocked(self, conn):
        db.insert_option_trade(
            trade_date="2026-11-12", symbol="MNST261218C00045000", underlying="MNST",
            option_type="Call", strike=45.0, expiration_date="2026-12-18",
            side="buy", position_effect="open", contracts=1, price=1.80, conn=conn,
        )
        rows = [{
            "trade_date": "2026-11-13", "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "sell", "position_effect": "close",
            "contracts": 5, "price": 2.35,
        }]
        result = preview_option_batch(rows, conn=conn)
        assert len(result["new"]) == 1
        assert len(result["overclose_rows"]) == 1
        assert result["new"][0]["is_overclose"] is True

    def test_describe_preview_mentions_skip_and_new(self, conn):
        db.insert_option_trade(
            trade_date="2026-11-12", symbol="MNST261218C00045000", underlying="MNST",
            option_type="Call", strike=45.0, expiration_date="2026-12-18",
            side="buy", position_effect="open", contracts=1, price=1.80, conn=conn,
        )
        rows = [
            {
                "trade_date": "2026-11-12", "underlying": "MNST", "option_type": "Call", "strike": 45.0,
                "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
                "contracts": 1, "price": 1.80,
            },
            {
                "trade_date": "2026-11-13", "underlying": "ONDS", "option_type": "Put", "strike": 12.0,
                "expiration_date": "2027-01-30", "side": "buy", "position_effect": "open",
                "contracts": 1, "price": 0.79,
            },
        ]
        result = preview_option_batch(rows, conn=conn)
        text = describe_option_batch_preview(result)
        assert "already recorded" in text
        assert "ONDS" in text
        assert "confirm" in text.lower()


class TestRecordOptionBatch:
    def test_confirm_writes_exactly_the_new_rows(self, conn):
        db.insert_option_trade(
            trade_date="2026-11-12", symbol="MNST261218C00045000", underlying="MNST",
            option_type="Call", strike=45.0, expiration_date="2026-12-18",
            side="buy", position_effect="open", contracts=1, price=1.80, conn=conn,
        )
        before = row_count(conn, "option_trades")
        rows = [
            {
                "trade_date": "2026-11-12", "underlying": "MNST", "option_type": "Call", "strike": 45.0,
                "expiration_date": "2026-12-18", "side": "buy", "position_effect": "open",
                "contracts": 1, "price": 1.80,
            },  # dup, skip
            {
                "trade_date": "2026-11-13", "underlying": "ONDS", "option_type": "Put", "strike": 12.0,
                "expiration_date": "2027-01-30", "side": "buy", "position_effect": "open",
                "contracts": 1, "price": 0.79,
            },  # new
        ]
        result = record_option_batch(rows, conn=conn)
        assert len(result["recorded"]) == 1
        assert len(result["skipped_already_recorded"]) == 1
        assert result["failed"] == []
        assert row_count(conn, "option_trades") == before + 1

        options = db.fetch_option_trades(conn=conn)
        new_row = options[options["underlying"] == "ONDS"].iloc[0]
        assert new_row["contracts"] == 1
        assert new_row["price"] == 0.79
        assert new_row["source"] == "manual"

    def test_values_match_what_insert_option_trade_would_produce(self, conn):
        rows = [{
            "trade_date": "2026-11-13", "underlying": "MNST", "option_type": "Call", "strike": 45.0,
            "expiration_date": "2026-12-18", "side": "sell", "position_effect": "close",
            "contracts": 1, "price": 2.35, "commission": 0.55, "vat": 0.04,
            "occ_fee": 0.03, "orf_fee": 0.02, "taf_fee": 0.0, "sec_fee": 0.01,
        }]
        record_option_batch(rows, conn=conn)

        conn2 = sqlite3.connect(":memory:")
        db.init_db(conn=conn2)
        db.insert_option_trade(
            trade_date="2026-11-13", symbol="MNST261218C00045000", underlying="MNST",
            option_type="Call", strike=45.0, expiration_date="2026-12-18",
            side="sell", position_effect="close", contracts=1, price=2.35,
            commission=0.55, vat=0.04, occ_fee=0.03, orf_fee=0.02, taf_fee=0.0, sec_fee=0.01,
            source="manual", conn=conn2,
        )

        a = db.fetch_option_trades(conn=conn).drop(columns=["id", "created_at"])
        b = db.fetch_option_trades(conn=conn2).drop(columns=["id", "created_at"])
        pd.testing.assert_frame_equal(a, b)
        conn2.close()
