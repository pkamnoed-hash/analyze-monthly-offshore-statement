import os
import sqlite3

import pytest

from core import db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


class TestDbPath:
    def test_db_path_resolves_to_project_root_data_folder_not_core(self):
        # Regression test: DB_PATH used to be computed relative to db.py's own directory,
        # which broke silently (pointed at core/data/portfolio.db -- a fresh empty database)
        # when db.py moved from the project root into core/. app_pages/ is a directory that
        # only exists at the true project root, sibling to core/ -- confirms PROJECT_ROOT
        # wasn't left one level too deep.
        assert os.path.isdir(os.path.join(db.PROJECT_ROOT, "app_pages"))
        assert db.DB_PATH == os.path.join(db.PROJECT_ROOT, "data", "portfolio.db")


class TestSchema:
    def test_creates_trades_and_dividends_tables(self, conn):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"trades", "dividends"} <= tables

    def test_init_db_is_idempotent(self, conn):
        db.init_db(conn=conn)  # should not raise on a second call
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"trades", "dividends"} <= tables


class TestComputeNetCommission:
    def test_buy_is_just_commission_fee_plus_vat(self):
        assert db.compute_net_commission(commission_fee=0.13, vat=0.0092) == pytest.approx(0.1392)

    def test_sell_matches_real_slip_example(self):
        # commission_fee=1.04, vat=0.00, reserved_fee (SEC+TAF)=0.03, fee_rebate=1.04
        result = db.compute_net_commission(commission_fee=1.04, vat=0.0, reserved_fee=0.03, fee_rebate=1.04)
        assert result == pytest.approx(0.03)
        # gross Stock Amount 689.98 - commission should match the slip's printed Total Credit 689.95
        assert 689.98 - result == pytest.approx(689.95)

    def test_defaults_to_zero_for_missing_fields(self):
        assert db.compute_net_commission() == 0.0


class TestInsertTrade:
    def test_buy_signs_quantity_positive_and_amount_negative(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=5.0, conn=conn)
        row = conn.execute("SELECT quantity, amount, side FROM trades WHERE symbol='AAA'").fetchone()
        assert row == (10.0, -50.0, "buy")

    def test_sell_signs_quantity_negative_and_amount_positive(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="sell", symbol="AAA", quantity=10, price=5.0, conn=conn)
        row = conn.execute("SELECT quantity, amount, side FROM trades WHERE symbol='AAA'").fetchone()
        assert row == (-10.0, 50.0, "sell")

    def test_side_is_lowercased(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="Buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        side = conn.execute("SELECT side FROM trades").fetchone()[0]
        assert side == "buy"

    def test_commission_is_netted_from_raw_fee_fields(self, conn):
        db.insert_trade(
            trade_date="2026-01-05", side="sell", symbol="AAA", quantity=14, price=49.28,
            commission_fee=1.04, vat=0.0, reserved_fee=0.03, fee_rebate=1.04, conn=conn,
        )
        commission = conn.execute("SELECT commission FROM trades").fetchone()[0]
        assert commission == pytest.approx(0.03)

    def test_defaults_source_to_manual(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        source = conn.execute("SELECT source FROM trades").fetchone()[0]
        assert source == "manual"

    def test_defaults_entry_type_to_trade_entry(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        entry_type = conn.execute("SELECT entry_type FROM trades").fetchone()[0]
        assert entry_type == "Trade Entry"


class TestInsertTradeRaw:
    def test_inserts_values_unmodified(self, conn):
        db.insert_trade_raw({
            "trade_date": "2023-01-03", "entry_type": "Trade Entry", "side": "buy", "symbol": "BBB",
            "quantity": 5.0, "price": 10.0, "amount": -50.0, "commission": 1.0, "source": "seed",
        }, conn=conn)
        row = conn.execute("SELECT quantity, amount, source FROM trades WHERE symbol='BBB'").fetchone()
        assert row == (5.0, -50.0, "seed")


class TestInsertTradesBulk:
    def test_all_rows_land_in_one_call(self, conn):
        rows = [
            {"trade_date": "2023-01-03", "entry_type": "Trade Entry", "side": "buy", "symbol": "CCC",
             "quantity": 1.0, "price": 1.0, "amount": -1.0, "commission": 0.0, "source": "seed"},
            {"trade_date": "2023-01-04", "entry_type": "Trade Entry", "side": "sell", "symbol": "CCC",
             "quantity": -1.0, "price": 2.0, "amount": 2.0, "commission": 0.0, "source": "seed"},
        ]
        db.insert_trades_bulk(rows, conn=conn)
        count = conn.execute("SELECT COUNT(*) FROM trades WHERE symbol='CCC'").fetchone()[0]
        assert count == 2

    def test_bad_row_rolls_back_the_whole_batch(self, conn):
        # second row is missing the NOT NULL 'symbol' -- whole batch should be rejected
        rows = [
            {"trade_date": "2023-01-03", "entry_type": "Trade Entry", "side": "buy", "symbol": "DDD",
             "quantity": 1.0, "price": 1.0, "amount": -1.0, "commission": 0.0, "source": "seed"},
            {"trade_date": "2023-01-04", "entry_type": "Trade Entry", "side": "sell", "symbol": None,
             "quantity": -1.0, "price": 2.0, "amount": 2.0, "commission": 0.0, "source": "seed"},
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_trades_bulk(rows, conn=conn)
        count = conn.execute("SELECT COUNT(*) FROM trades WHERE symbol='DDD'").fetchone()[0]
        assert count == 0


class TestDividends:
    def test_insert_dividend_defaults_source_to_manual(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Dividend", net_amount=2.45, symbol="HDV", conn=conn)
        row = conn.execute("SELECT symbol, net_amount, source FROM dividends").fetchone()
        assert row == ("HDV", 2.45, "manual")

    def test_interest_row_allows_null_symbol(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Interest", net_amount=0.5, conn=conn)
        symbol = conn.execute("SELECT symbol FROM dividends").fetchone()[0]
        assert symbol is None

    def test_insert_dividends_bulk_lands_all_rows(self, conn):
        rows = [
            {"trade_date": "2026-06-19", "symbol": "HDV", "entry_type": "Dividend", "net_amount": 2.45, "source": "manual"},
            {"trade_date": "2026-06-19", "symbol": "DVYE", "entry_type": "Dividend", "net_amount": 8.64, "source": "manual"},
        ]
        db.insert_dividends_bulk(rows, conn=conn)
        count = conn.execute("SELECT COUNT(*) FROM dividends").fetchone()[0]
        assert count == 2

    def test_bad_batch_rolls_back_atomically(self, conn):
        rows = [
            {"trade_date": "2026-06-19", "symbol": "HDV", "entry_type": "Dividend", "net_amount": 2.45, "source": "manual"},
            {"trade_date": "2026-06-19", "symbol": "BAD", "entry_type": "NotAValidType", "net_amount": 1.0, "source": "manual"},
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_dividends_bulk(rows, conn=conn)
        count = conn.execute("SELECT COUNT(*) FROM dividends").fetchone()[0]
        assert count == 0

    def test_invalid_entry_type_is_rejected_by_check_constraint(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_dividend(trade_date="2026-01-05", entry_type="NotAValidType", net_amount=1.0, conn=conn)


class TestFetchTrades:
    def test_returns_columns_matching_calculations_contract(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=5.0, conn=conn)
        df = db.fetch_trades(conn=conn)
        assert list(df.columns) >= list(df.columns)  # sanity: no exception building the frame
        for col in ["Symbol", "Trade Date", "Entry Type", "Side", "Quantity", "Price", "Amount", "Commission", "Month"]:
            assert col in df.columns

    def test_month_is_first_of_trade_month(self, conn):
        db.insert_trade(trade_date="2026-03-17", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        df = db.fetch_trades(conn=conn)
        assert df.iloc[0]["Month"].strftime("%Y-%m-%d") == "2026-03-01"


class TestSeedTracking:
    def test_count_seed_rows_reflects_only_seed_source(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, source="manual", conn=conn)
        db.insert_trade_raw({
            "trade_date": "2023-01-03", "entry_type": "Trade Entry", "side": "buy", "symbol": "BBB",
            "quantity": 1.0, "price": 1.0, "amount": -1.0, "commission": 0.0, "source": "seed",
        }, conn=conn)
        assert db.count_seed_rows(conn=conn) == 1

    def test_delete_seed_rows_leaves_manual_rows_untouched(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, source="manual", conn=conn)
        db.insert_trade_raw({
            "trade_date": "2023-01-03", "entry_type": "Trade Entry", "side": "buy", "symbol": "BBB",
            "quantity": 1.0, "price": 1.0, "amount": -1.0, "commission": 0.0, "source": "seed",
        }, conn=conn)
        db.delete_seed_rows(conn=conn)
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM trades")]
        assert remaining == ["AAA"]


class TestDeleteTrade:
    def test_deletes_only_the_given_id(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        db.insert_trade(trade_date="2026-01-06", side="buy", symbol="BBB", quantity=1, price=1.0, conn=conn)
        target_id = conn.execute("SELECT id FROM trades WHERE symbol='AAA'").fetchone()[0]
        db.delete_trade(target_id, conn=conn)
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM trades")]
        assert remaining == ["BBB"]

    def test_unknown_id_is_a_noop(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        db.delete_trade(999999, conn=conn)
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM trades")]
        assert remaining == ["AAA"]


class TestDeleteDividend:
    def test_deletes_only_the_given_id(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Dividend", net_amount=1.0, symbol="AAA", conn=conn)
        db.insert_dividend(trade_date="2026-01-06", entry_type="Dividend", net_amount=2.0, symbol="BBB", conn=conn)
        target_id = conn.execute("SELECT id FROM dividends WHERE symbol='AAA'").fetchone()[0]
        db.delete_dividend(target_id, conn=conn)
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM dividends")]
        assert remaining == ["BBB"]

    def test_unknown_id_is_a_noop(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Dividend", net_amount=1.0, symbol="AAA", conn=conn)
        db.delete_dividend(999999, conn=conn)
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM dividends")]
        assert remaining == ["AAA"]


class TestDeleteBySource:
    def test_delete_trades_by_source_only_removes_that_source(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, source="manual", conn=conn)
        db.insert_trade(trade_date="2026-01-06", side="buy", symbol="BBB", quantity=1, price=1.0, source="slip", conn=conn)
        db.insert_trade_raw({
            "trade_date": "2023-01-03", "entry_type": "Trade Entry", "side": "buy", "symbol": "CCC",
            "quantity": 1.0, "price": 1.0, "amount": -1.0, "commission": 0.0, "source": "seed",
        }, conn=conn)
        db.delete_trades_by_source("manual", conn=conn)
        remaining = sorted(r[0] for r in conn.execute("SELECT symbol FROM trades"))
        assert remaining == ["BBB", "CCC"]

    def test_delete_dividends_by_source_only_removes_that_source(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Dividend", net_amount=1.0, symbol="AAA", source="manual", conn=conn)
        db.insert_dividend(trade_date="2026-01-06", entry_type="Dividend", net_amount=2.0, symbol="BBB", source="seed", conn=conn)
        db.delete_dividends_by_source("manual", conn=conn)
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM dividends")]
        assert remaining == ["BBB"]

    def test_delete_by_source_with_no_matching_rows_is_a_noop(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, source="manual", conn=conn)
        db.delete_trades_by_source("slip", conn=conn)  # nothing with this source exists
        remaining = [r[0] for r in conn.execute("SELECT symbol FROM trades")]
        assert remaining == ["AAA"]
