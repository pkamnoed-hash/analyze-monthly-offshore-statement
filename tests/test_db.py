import os
import sqlite3

import pandas as pd
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


class TestFetchUnreconciledTrades:
    def test_excludes_rows_after_cutoff(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        db.insert_trade(trade_date="2026-02-05", side="buy", symbol="BBB", quantity=1, price=1.0, conn=conn)
        df = db.fetch_unreconciled_trades(cutoff=pd.Timestamp("2026-01-31"), conn=conn)
        assert list(df["Symbol"]) == ["AAA"]

    def test_includes_rows_exactly_at_cutoff(self, conn):
        db.insert_trade(trade_date="2026-01-31", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        df = db.fetch_unreconciled_trades(cutoff=pd.Timestamp("2026-01-31"), conn=conn)
        assert list(df["Symbol"]) == ["AAA"]

    def test_excludes_already_reconciled_rows(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        target_id = conn.execute("SELECT id FROM trades WHERE symbol='AAA'").fetchone()[0]
        db.mark_reconciled("trades", target_id, "2026-01", conn=conn)
        df = db.fetch_unreconciled_trades(cutoff=pd.Timestamp("2026-01-31"), conn=conn)
        assert df.empty

    def test_empty_table_returns_empty_frame_without_error(self, conn):
        df = db.fetch_unreconciled_trades(cutoff=pd.Timestamp("2026-01-31"), conn=conn)
        assert df.empty


class TestFetchUnreconciledDividends:
    def test_excludes_rows_after_cutoff_and_already_reconciled(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Dividend", net_amount=1.0, symbol="AAA", conn=conn)
        db.insert_dividend(trade_date="2026-02-05", entry_type="Dividend", net_amount=1.0, symbol="BBB", conn=conn)
        db.insert_dividend(trade_date="2026-01-06", entry_type="Dividend", net_amount=1.0, symbol="CCC", conn=conn)
        reconciled_id = conn.execute("SELECT id FROM dividends WHERE symbol='CCC'").fetchone()[0]
        db.mark_reconciled("dividends", reconciled_id, "2026-01", conn=conn)
        df = db.fetch_unreconciled_dividends(cutoff=pd.Timestamp("2026-01-31"), conn=conn)
        assert list(df["Symbol"]) == ["AAA"]


class TestMarkReconciled:
    def test_sets_reconciled_month_on_only_the_given_row(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        db.insert_trade(trade_date="2026-01-06", side="buy", symbol="BBB", quantity=1, price=1.0, conn=conn)
        target_id = conn.execute("SELECT id FROM trades WHERE symbol='AAA'").fetchone()[0]
        db.mark_reconciled("trades", target_id, "2026-01", conn=conn)
        rows = dict(conn.execute("SELECT symbol, reconciled_month FROM trades").fetchall())
        assert rows == {"AAA": "2026-01", "BBB": None}

    def test_unknown_id_is_a_noop(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        db.mark_reconciled("trades", 999999, "2026-01", conn=conn)
        reconciled_month = conn.execute("SELECT reconciled_month FROM trades").fetchone()[0]
        assert reconciled_month is None

    def test_invalid_table_raises_value_error(self, conn):
        with pytest.raises(ValueError):
            db.mark_reconciled("not_a_table", 1, "2026-01", conn=conn)


class TestMarkReconciledBulk:
    def test_sets_reconciled_month_on_all_given_ids(self, conn):
        db.insert_dividend(trade_date="2026-01-05", entry_type="Dividend", net_amount=1.0, symbol="AAA", conn=conn)
        db.insert_dividend(trade_date="2026-01-06", entry_type="Dividend", net_amount=1.0, symbol="BBB", conn=conn)
        db.insert_dividend(trade_date="2026-01-07", entry_type="Dividend", net_amount=1.0, symbol="CCC", conn=conn)
        ids = [r[0] for r in conn.execute("SELECT id FROM dividends WHERE symbol IN ('AAA','BBB')")]
        db.mark_reconciled_bulk("dividends", ids, "2026-01", conn=conn)
        rows = dict(conn.execute("SELECT symbol, reconciled_month FROM dividends").fetchall())
        assert rows == {"AAA": "2026-01", "BBB": "2026-01", "CCC": None}

    def test_invalid_table_raises_value_error_before_touching_db(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        target_id = conn.execute("SELECT id FROM trades").fetchone()[0]
        with pytest.raises(ValueError):
            db.mark_reconciled_bulk("not_a_table", [target_id], "2026-01", conn=conn)


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


class TestSymbolTypes:
    def test_set_symbol_type_inserts(self, conn):
        db.set_symbol_type("VRIG", "Dividend", conn=conn)
        row = conn.execute("SELECT allocation_type FROM symbol_types WHERE symbol='VRIG'").fetchone()
        assert row == ("Dividend",)

    def test_set_symbol_type_upserts_an_existing_symbol(self, conn):
        db.set_symbol_type("PLTR", "Dividend", conn=conn)
        db.set_symbol_type("PLTR", "Growth", conn=conn)
        rows = conn.execute("SELECT allocation_type FROM symbol_types WHERE symbol='PLTR'").fetchall()
        assert rows == [("Growth",)]  # exactly one row, updated in place -- not a duplicate insert

    def test_invalid_allocation_type_is_rejected_by_check_constraint(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            db.set_symbol_type("VRIG", "Speculative", conn=conn)

    def test_others_itself_is_rejected_as_a_stored_value(self, conn):
        # "Others" is the absence-of-a-row default, never a value actually
        # written to the table -- confirms the CHECK constraint enforces that.
        with pytest.raises(sqlite3.IntegrityError):
            db.set_symbol_type("VRIG", "Others", conn=conn)

    def test_clear_symbol_type_removes_the_row(self, conn):
        db.set_symbol_type("VRIG", "Dividend", conn=conn)
        db.clear_symbol_type("VRIG", conn=conn)
        row = conn.execute("SELECT * FROM symbol_types WHERE symbol='VRIG'").fetchone()
        assert row is None

    def test_clear_symbol_type_on_unknown_symbol_is_a_noop(self, conn):
        db.clear_symbol_type("NOPE", conn=conn)  # never existed -- should not raise

    def test_fetch_symbol_types_covers_every_traded_symbol(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=1, price=1.0, conn=conn)
        db.insert_trade(trade_date="2026-01-06", side="buy", symbol="BBB", quantity=1, price=1.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        result = db.fetch_symbol_types(conn=conn)
        assert dict(zip(result["Symbol"], result["Allocation Type"])) == {"AAA": "Dividend", "BBB": "Others"}

    def test_fetch_symbol_types_includes_a_fully_sold_out_symbol(self, conn):
        # Real-data-shaped regression: 44 of this account's 96 traded symbols
        # are fully bought-and-sold (net position zero) today -- a coverage
        # query accidentally scoped to "current holdings" instead of
        # "everything in trades" would silently drop ~46% of symbols.
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=1.0, conn=conn)
        db.insert_trade(trade_date="2026-01-06", side="sell", symbol="AAA", quantity=10, price=1.0, conn=conn)
        result = db.fetch_symbol_types(conn=conn)
        assert "AAA" in set(result["Symbol"])

    def test_fetch_symbol_types_returns_empty_frame_without_error_when_no_trades(self, conn):
        result = db.fetch_symbol_types(conn=conn)
        assert result.empty
        assert list(result.columns) == ["Symbol", "Allocation Type"]


class TestReferenceLines:
    def test_save_derives_captured_side_from_latest_price(self, conn):
        db.save_reference_lines(
            "AIQ",
            [{"price": 70.0, "is_override": False}, {"price": 60.0, "is_override": False}],
            latest_price=65.0, conn=conn,
        )
        rows = conn.execute(
            "SELECT price, captured_side FROM reference_lines WHERE symbol='AIQ' ORDER BY price",
        ).fetchall()
        assert rows == [(60.0, "support"), (70.0, "resistance")]

    def test_save_resets_passed_at_on_every_call(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        db.mark_reference_lines_passed({"AIQ": 71.0}, conn=conn)
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0] is not None

        # A fresh save (Regenerate/drag/delete/add) re-derives everything, including
        # wiping the just-set passed_at back to NULL -- confirmed design: any edit on the
        # symbol's own page counts as fresh engagement with it.
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0] is None

    def test_fetch_reference_lines_returns_the_new_columns(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        result = db.fetch_reference_lines(conn=conn)
        assert list(result.columns) == [
            "Symbol", "Price", "Is Override", "Captured Side", "Passed At",
            "Captured Timeline", "Captured Interval", "Updated At",
        ]
        assert result.iloc[0]["Captured Side"] == "resistance"
        assert pd.isna(result.iloc[0]["Passed At"])


class TestMarkReferenceLinesPassed:
    def test_marks_a_resistance_line_passed_when_price_rises_to_it(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        newly_passed = db.mark_reference_lines_passed({"AIQ": 70.0}, conn=conn)
        assert newly_passed == 1
        passed_at = conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0]
        assert passed_at is not None

    def test_marks_a_support_line_passed_when_price_falls_to_it(self, conn):
        db.save_reference_lines("AIQ", [{"price": 60.0, "is_override": False}], latest_price=65.0, conn=conn)
        newly_passed = db.mark_reference_lines_passed({"AIQ": 60.0}, conn=conn)
        assert newly_passed == 1

    def test_does_not_pass_a_resistance_line_price_has_not_reached_yet(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        newly_passed = db.mark_reference_lines_passed({"AIQ": 68.0}, conn=conn)
        assert newly_passed == 0
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0] is None

    def test_is_idempotent_does_not_re_mark_an_already_passed_row(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        db.mark_reference_lines_passed({"AIQ": 70.0}, conn=conn)
        first_passed_at = conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0]

        second_run_count = db.mark_reference_lines_passed({"AIQ": 75.0}, conn=conn)
        assert second_run_count == 0
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0] == first_passed_at

    def test_skips_symbols_missing_from_latest_prices(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        newly_passed = db.mark_reference_lines_passed({}, conn=conn)
        assert newly_passed == 0

    def test_does_not_touch_other_symbols_lines(self, conn):
        db.save_reference_lines("AIQ", [{"price": 70.0, "is_override": False}], latest_price=65.0, conn=conn)
        db.save_reference_lines("BLK", [{"price": 1200.0, "is_override": False}], latest_price=1100.0, conn=conn)
        db.mark_reference_lines_passed({"AIQ": 71.0, "BLK": 1100.0}, conn=conn)
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='AIQ'").fetchone()[0] is not None
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='BLK'").fetchone()[0] is None

    def test_backfills_captured_side_for_a_legacy_row_with_no_captured_side(self, conn):
        # Regression: a real row saved before v4.4.1 added captured_side has NULL there --
        # caught live on the real DB, where several already-captured symbols silently
        # showed "-" on both sides because a NULL captured_side matched neither filter.
        conn.execute(
            "INSERT INTO reference_lines (symbol, price, is_override, captured_side, updated_at) "
            "VALUES ('OLD', 70.0, 0, NULL, datetime('now'))",
        )
        db.mark_reference_lines_passed({"OLD": 65.0}, conn=conn)  # price below the row -> should backfill 'resistance'
        row = conn.execute("SELECT captured_side, passed_at FROM reference_lines WHERE symbol='OLD'").fetchone()
        assert row == ("resistance", None)  # backfilled, and correctly NOT passed (65 < 70)

    def test_backfilled_legacy_row_can_still_be_marked_passed_once_price_crosses_it(self, conn):
        conn.execute(
            "INSERT INTO reference_lines (symbol, price, is_override, captured_side, updated_at) "
            "VALUES ('OLD', 70.0, 0, NULL, datetime('now'))",
        )
        db.mark_reference_lines_passed({"OLD": 65.0}, conn=conn)  # backfills to 'resistance', not yet passed
        newly_passed = db.mark_reference_lines_passed({"OLD": 72.0}, conn=conn)  # price now above it
        assert newly_passed == 1
        assert conn.execute("SELECT passed_at FROM reference_lines WHERE symbol='OLD'").fetchone()[0] is not None


class TestMarketProfileCache:
    def _row(self, **overrides):
        row = {
            "Symbol": "AIQ", "Description": "AI Powered Equity ETF", "Sector": "Technology",
            "Industry": "Software", "Quote Type": "ETF", "Beta": 1.2, "Latest Price": 70.0,
            "High90D": 75.0, "Low90D": 60.0, "Dividend Per Year": 1.5, "Dividend Yield %": 2.1,
            "Dividend Frequency": "Quarterly", "Ex-Date": pd.Timestamp("2026-06-01"),
            "History90D": [68.0, 69.0, 70.0],
        }
        row.update(overrides)
        return row

    def test_save_and_fetch_round_trip(self, conn):
        db.save_market_profile_cache([self._row()], conn=conn)
        result = db.fetch_market_profile_cache(conn=conn)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["Symbol"] == "AIQ"
        assert row["Description"] == "AI Powered Equity ETF"
        assert row["Beta"] == 1.2
        assert row["Latest Price"] == 70.0
        assert row["Dividend Frequency"] == "Quarterly"
        assert row["Ex-Date"] == pd.Timestamp("2026-06-01")
        assert row["History90D"] == [68.0, 69.0, 70.0]
        assert pd.notna(row["Fetched At"])

    def test_upsert_replaces_rather_than_duplicates(self, conn):
        db.save_market_profile_cache([self._row()], conn=conn)
        db.save_market_profile_cache([self._row(**{"Latest Price": 80.0})], conn=conn)
        result = db.fetch_market_profile_cache(conn=conn)
        assert len(result) == 1
        assert result.iloc[0]["Latest Price"] == 80.0

    def test_nan_beta_stored_and_fetched_as_nan_not_a_crash(self, conn):
        # A real, valid outcome of a successful fetch -- yfinance has neither `beta`
        # nor `beta3Year` for some symbols, not a failure case.
        db.save_market_profile_cache([self._row(Beta=float("nan"))], conn=conn)
        result = db.fetch_market_profile_cache(conn=conn)
        assert pd.isna(result.iloc[0]["Beta"])

    def test_nat_ex_date_stored_and_fetched_as_nat(self, conn):
        # A real non-dividend-paying symbol (e.g. ARM) -- compute_reference_lines-style
        # "no data" case, not a fetch failure.
        db.save_market_profile_cache([self._row(**{"Ex-Date": pd.NaT})], conn=conn)
        result = db.fetch_market_profile_cache(conn=conn)
        assert pd.isna(result.iloc[0]["Ex-Date"])

    def test_multiple_symbols_in_one_call(self, conn):
        db.save_market_profile_cache(
            [self._row(Symbol="AIQ"), self._row(Symbol="BLK", **{"Latest Price": 1200.0})],
            conn=conn,
        )
        result = db.fetch_market_profile_cache(conn=conn)
        assert set(result["Symbol"]) == {"AIQ", "BLK"}

    def test_empty_history_defaults_to_empty_list_not_none(self, conn):
        db.save_market_profile_cache([self._row(History90D=None)], conn=conn)
        result = db.fetch_market_profile_cache(conn=conn)
        assert result.iloc[0]["History90D"] == []

    def test_fetch_returns_empty_dataframe_when_nothing_cached_yet(self, conn):
        result = db.fetch_market_profile_cache(conn=conn)
        assert result.empty
        assert "Symbol" in result.columns


class TestRebalancePlan:
    def test_get_active_rebalance_plan_returns_none_when_none_exists(self, conn):
        assert db.get_active_rebalance_plan(conn=conn) is None

    def test_start_rebalance_plan_creates_plan_with_zeroed_items(self, conn):
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan == {
            "id": plan_id,
            "amount": 0,
            "items": {
                "AAA": {"pct": 0, "bought": False},
                "BBB": {"pct": 0, "bought": False},
            },
        }

    def test_update_rebalance_plan_amount(self, conn):
        plan_id = db.start_rebalance_plan(["AAA"], conn=conn)
        db.update_rebalance_plan_amount(plan_id, 1000.0, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["amount"] == 1000.0

    def test_update_rebalance_plan_item_pct_only(self, conn):
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", pct=40, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["items"]["AAA"] == {"pct": 40, "bought": False}
        assert plan["items"]["BBB"] == {"pct": 0, "bought": False}  # untouched

    def test_update_rebalance_plan_item_bought_only(self, conn):
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", bought=True, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["items"]["AAA"]["bought"] is True

    def test_ticking_bought_does_not_reset_pct(self, conn):
        # The "Bought?" checkbox is a separate remark, not a lock -- setting
        # it must not clobber a previously-entered pct. Two items so the
        # plan doesn't auto-complete (and vanish) the moment AAA is ticked.
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", pct=25, conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", bought=True, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["items"]["AAA"] == {"pct": 25, "bought": True}

    def test_pct_stays_editable_after_being_marked_bought(self, conn):
        # Confirms the "doesn't lock the row" requirement at the data layer.
        # Two items so the plan stays active after AAA is ticked bought.
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", bought=True, conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", pct=60, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["items"]["AAA"] == {"pct": 60, "bought": True}

    def test_plan_auto_completes_when_all_items_ticked_bought(self, conn):
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", bought=True, conn=conn)
        assert db.get_active_rebalance_plan(conn=conn) is not None  # BBB still unticked
        db.update_rebalance_plan_item(plan_id, "BBB", bought=True, conn=conn)
        assert db.get_active_rebalance_plan(conn=conn) is None  # now cleared

    def test_new_plan_starts_after_previous_one_auto_completes(self, conn):
        first_id = db.start_rebalance_plan(["AAA"], conn=conn)
        db.update_rebalance_plan_item(first_id, "AAA", bought=True, conn=conn)
        assert db.get_active_rebalance_plan(conn=conn) is None
        second_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        assert second_id != first_id
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["id"] == second_id
        assert set(plan["items"]) == {"AAA", "BBB"}

    def test_reset_rebalance_plan_clears_without_requiring_all_bought(self, conn):
        plan_id = db.start_rebalance_plan(["AAA", "BBB"], conn=conn)
        db.update_rebalance_plan_item(plan_id, "AAA", pct=50, conn=conn)  # BBB untouched
        db.reset_rebalance_plan(plan_id, conn=conn)
        assert db.get_active_rebalance_plan(conn=conn) is None

    def test_start_rebalance_plan_with_no_symbols_creates_empty_plan(self, conn):
        plan_id = db.start_rebalance_plan([], conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan == {"id": plan_id, "amount": 0, "items": {}}
