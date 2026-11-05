import sqlite3

import pytest

from core import db
from core.rebalance_entry import (
    apply_rebalance_edit,
    apply_rebalance_reset,
    describe_plan,
    get_plan_with_holdings,
    preview_rebalance_edit,
    preview_rebalance_reset,
    validate_rebalance_edits,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(conn=c)
    yield c
    c.close()


def row_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _seed_two_dividend_holdings(conn):
    db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
    db.insert_trade(trade_date="2026-01-05", side="buy", symbol="BBB", quantity=5, price=50.0, conn=conn)
    db.set_symbol_type("AAA", "Dividend", conn=conn)
    db.set_symbol_type("BBB", "Dividend", conn=conn)
    db.save_market_profile_cache([
        {"Symbol": "AAA", "Latest Price": 100.0, "Dividend Yield %": 4.0, "Quote Type": "EQUITY", "Sector": "Tech"},
        {"Symbol": "BBB", "Latest Price": 50.0, "Dividend Yield %": 8.0, "Quote Type": "EQUITY", "Sector": "Utilities"},
    ], conn=conn)


class TestGetPlanWithHoldings:
    def test_auto_starts_a_plan_when_none_active(self, conn):
        _seed_two_dividend_holdings(conn)
        assert db.get_active_rebalance_plan(conn=conn) is None

        data = get_plan_with_holdings(conn=conn)
        assert data["plan"] is not None
        assert data["plan"]["amount"] == 0
        assert set(data["plan"]["items"]) == {"AAA", "BBB"}
        assert db.get_active_rebalance_plan(conn=conn) is not None

    def test_holdings_carry_a_dividends_received_placeholder(self, conn):
        _seed_two_dividend_holdings(conn)
        data = get_plan_with_holdings(conn=conn)
        assert (data["holdings"]["Dividends Received"] == 0.0).all()

    def test_empty_holdings_still_returns_a_plan(self, conn):
        data = get_plan_with_holdings(conn=conn)
        assert data["holdings"].empty
        assert data["plan"]["items"] == {}


class TestDescribePlan:
    def test_renders_amount_and_allocation_percent(self, conn):
        _seed_two_dividend_holdings(conn)
        data = get_plan_with_holdings(conn=conn)
        db.update_rebalance_plan_amount(data["plan"]["id"], 1000.0, conn=conn)
        db.update_rebalance_plan_item(data["plan"]["id"], "AAA", pct=60.0, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)

        text = describe_plan(data["holdings"], plan)
        assert "$1,000.00" in text
        assert "60.0% allocated" in text
        assert "40.0% remaining" in text
        assert "AAA: 60.0% ($600.00) [not bought]" in text
        assert "BBB: 0.0% ($0.00) [not bought]" in text

    def test_marks_bought_status(self, conn):
        _seed_two_dividend_holdings(conn)
        data = get_plan_with_holdings(conn=conn)
        db.update_rebalance_plan_item(data["plan"]["id"], "AAA", bought=True, conn=conn)
        plan = db.get_active_rebalance_plan(conn=conn)

        text = describe_plan(data["holdings"], plan)
        assert "AAA: 0.0% ($0.00) [bought]" in text

    def test_no_holdings_returns_plain_message(self, conn):
        data = get_plan_with_holdings(conn=conn)
        assert describe_plan(data["holdings"], data["plan"]) == "No Dividend-classified holdings currently held."


class TestValidateRebalanceEdits:
    def test_valid_item_passes(self):
        valid, errors = validate_rebalance_edits([{"symbol": "AAA", "pct": 50.0}], {"AAA"})
        assert errors == []
        assert valid == [{"symbol": "AAA", "pct": 50.0}]

    def test_unknown_symbol_rejected(self):
        valid, errors = validate_rebalance_edits([{"symbol": "ZZZ", "pct": 50.0}], {"AAA"})
        assert valid == []
        assert "ZZZ" in errors[0]
        assert "Dividend-classified" in errors[0]

    def test_pct_out_of_range_rejected(self):
        valid, errors = validate_rebalance_edits([{"symbol": "AAA", "pct": 150.0}], {"AAA"})
        assert valid == []
        assert "pct" in errors[0]

    def test_non_bool_bought_rejected(self):
        valid, errors = validate_rebalance_edits([{"symbol": "AAA", "bought": "yes"}], {"AAA"})
        assert valid == []
        assert "bought" in errors[0]

    def test_one_bad_item_does_not_drop_a_good_item(self):
        valid, errors = validate_rebalance_edits(
            [{"symbol": "AAA", "pct": 50.0}, {"symbol": "ZZZ", "pct": 50.0}], {"AAA"},
        )
        assert len(valid) == 1
        assert len(errors) == 1
        assert "item 1" in errors[0]


class TestPreviewRebalanceEdit:
    def test_writes_nothing(self, conn):
        _seed_two_dividend_holdings(conn)
        get_plan_with_holdings(conn=conn)  # establishes the active plan first
        before_amount = db.get_active_rebalance_plan(conn=conn)["amount"]
        result = preview_rebalance_edit(1000.0, [{"symbol": "AAA", "pct": 50.0}], conn=conn)
        assert result["errors"] == []
        assert db.get_active_rebalance_plan(conn=conn)["amount"] == before_amount

    def test_after_reflects_the_hypothetical_change(self, conn):
        _seed_two_dividend_holdings(conn)
        result = preview_rebalance_edit(1000.0, [{"symbol": "AAA", "pct": 50.0}], conn=conn)
        assert "$0.00 to invest" in result["before"]
        assert "$1,000.00 to invest" in result["after"]
        assert "AAA: 50.0%" in result["after"]

    def test_invalid_item_reported_as_error_not_silently_dropped(self, conn):
        _seed_two_dividend_holdings(conn)
        result = preview_rebalance_edit(None, [{"symbol": "ZZZ", "pct": 50.0}], conn=conn)
        assert len(result["errors"]) == 1
        assert "ZZZ" in result["errors"][0]

    def test_negative_amount_rejected(self, conn):
        _seed_two_dividend_holdings(conn)
        result = preview_rebalance_edit(-5.0, [], conn=conn)
        assert len(result["errors"]) == 1
        assert "amount" in result["errors"][0]


class TestApplyRebalanceEdit:
    def test_writes_exactly_the_requested_changes(self, conn):
        _seed_two_dividend_holdings(conn)
        result = apply_rebalance_edit(1000.0, [{"symbol": "AAA", "pct": 50.0}], conn=conn)
        assert result["errors"] == []
        assert set(result["applied"]) == {"amount", "AAA"}

        plan = db.get_active_rebalance_plan(conn=conn)
        assert plan["amount"] == 1000.0
        assert plan["items"]["AAA"]["pct"] == 50.0
        assert plan["items"]["BBB"]["pct"] == 0.0

    def test_unchanged_amount_is_not_reported_as_applied(self, conn):
        _seed_two_dividend_holdings(conn)
        result = apply_rebalance_edit(0.0, [{"symbol": "AAA", "pct": 25.0}], conn=conn)
        assert "amount" not in result["applied"]
        assert result["applied"] == ["AAA"]

    def test_revalidates_against_fresh_state_not_caller_input(self, conn):
        # A symbol stops being a Dividend holding between preview and confirm --
        # apply_rebalance_edit must re-check against the DB's current state, not
        # anything decided earlier.
        _seed_two_dividend_holdings(conn)
        preview_rebalance_edit(None, [{"symbol": "AAA", "pct": 50.0}], conn=conn)
        db.set_symbol_type("AAA", "Growth", conn=conn)

        result = apply_rebalance_edit(None, [{"symbol": "AAA", "pct": 50.0}], conn=conn)
        assert len(result["errors"]) == 1
        assert "AAA" in result["errors"][0]

    def test_ticking_last_row_bought_auto_starts_a_fresh_plan(self, conn):
        db.insert_trade(trade_date="2026-01-05", side="buy", symbol="AAA", quantity=10, price=100.0, conn=conn)
        db.set_symbol_type("AAA", "Dividend", conn=conn)
        db.save_market_profile_cache([{"Symbol": "AAA", "Latest Price": 100.0, "Dividend Yield %": 4.0}], conn=conn)

        old_plan_id = get_plan_with_holdings(conn=conn)["plan"]["id"]
        result = apply_rebalance_edit(None, [{"symbol": "AAA", "bought": True}], conn=conn)

        assert result["plan_completed"] is True
        fresh_plan = db.get_active_rebalance_plan(conn=conn)
        assert fresh_plan["id"] != old_plan_id
        assert fresh_plan["items"]["AAA"]["bought"] is False


class TestRebalanceReset:
    def test_preview_describes_without_writing(self, conn):
        _seed_two_dividend_holdings(conn)
        plan_id = get_plan_with_holdings(conn=conn)["plan"]["id"]
        db.update_rebalance_plan_item(plan_id, "AAA", pct=50.0, conn=conn)

        result = preview_rebalance_reset(conn=conn)
        assert "AAA: 50.0%" in result["before"]
        assert db.get_active_rebalance_plan(conn=conn)["id"] == plan_id

    def test_apply_abandons_old_plan_and_starts_a_fresh_one(self, conn):
        _seed_two_dividend_holdings(conn)
        plan_id = get_plan_with_holdings(conn=conn)["plan"]["id"]
        db.update_rebalance_plan_item(plan_id, "AAA", pct=50.0, conn=conn)

        result = apply_rebalance_reset(conn=conn)
        assert "AAA: 0.0%" in result["after"]

        fresh_plan = db.get_active_rebalance_plan(conn=conn)
        assert fresh_plan["id"] != plan_id
        assert fresh_plan["items"]["AAA"]["pct"] == 0.0
