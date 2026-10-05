import pandas as pd
import pytest

from core import calculations

COLS = ["Trade Date", "Symbol", "Entry Type", "Side", "Quantity", "Price", "Amount", "Commission"]


def trades(*rows):
    """rows: (date, side, quantity, price) for one symbol, 'PLTR'."""
    data = [
        {"Trade Date": pd.Timestamp(d), "Symbol": "PLTR", "Entry Type": "Trade Entry", "Side": side,
         "Quantity": qty, "Price": px, "Amount": qty * px, "Commission": 0.0}
        for d, side, qty, px in rows
    ]
    return pd.DataFrame(data, columns=COLS)


# Matches the screenshot: 4.36127 shares, cost basis 567.53. Oldest lot 2 @ 100 (cost 200),
# newer lot 2.36127 at the price that makes the total 567.53.
LOT2_PRICE = (567.53 - 200) / 2.36127
BASE = trades(("2026-01-02", "buy", 2.0, 100.0), ("2026-02-02", "buy", 2.36127, LOT2_PRICE))
PRICE_NOW = 145.0


def preview(side, quantity, price=150.0, book=BASE, market=PRICE_NOW):
    return calculations.preview_trade_position(book, "PLTR", side, quantity, price, market)


def test_no_position_before_a_first_buy():
    result = preview("Buy", 10, book=trades())
    assert result["before"]["shares"] == 0
    assert result["before"]["avg_cost"] is None
    assert result["after"]["shares"] == pytest.approx(10)
    assert result["after"]["avg_cost"] == pytest.approx(150.0)
    assert result["after"]["pl_pct"] == pytest.approx((145 - 150) / 150 * 100)


def test_current_position_matches_the_page_example():
    before = preview("Buy", 10)["before"]
    assert before["shares"] == pytest.approx(4.36127)
    assert before["cost_basis"] == pytest.approx(567.53)
    assert before["avg_cost"] == pytest.approx(567.53 / 4.36127)


def test_buy_adds_shares_and_moves_the_average_cost():
    after = preview("Buy", 10)["after"]
    assert after["shares"] == pytest.approx(14.36127)
    assert after["cost_basis"] == pytest.approx(567.53 + 1500)
    assert after["avg_cost"] == pytest.approx((567.53 + 1500) / 14.36127)


def test_buy_percent_pl_at_the_cached_price():
    result = preview("Buy", 10)
    before, after = result["before"], result["after"]
    assert before["pl"] == pytest.approx(4.36127 * 145 - 567.53)
    assert before["pl_pct"] == pytest.approx(before["pl"] / 567.53 * 100)
    assert after["market_value"] == pytest.approx(14.36127 * 145)
    assert after["pl"] == pytest.approx(14.36127 * 145 - 2067.53)
    assert after["pl_pct"] == pytest.approx(after["pl"] / 2067.53 * 100)


def test_sell_of_exactly_the_oldest_lot_leaves_the_newer_lot_as_the_average():
    after = preview("Sell", 2)["after"]
    assert after["shares"] == pytest.approx(2.36127)
    assert after["avg_cost"] == pytest.approx(LOT2_PRICE)
    assert after["cost_basis"] == pytest.approx(2.36127 * LOT2_PRICE)


def test_sell_partway_into_a_lot_keeps_the_rest_of_that_lot():
    # Sell 3: all of the 2 @ 100 lot plus 1 of the 2.36127 @ 150 lot -> 1.36127 @ 150 left.
    after = preview("Sell", 3)["after"]
    assert after["shares"] == pytest.approx(1.36127)
    assert after["avg_cost"] == pytest.approx(LOT2_PRICE)
    assert after["cost_basis"] == pytest.approx(1.36127 * LOT2_PRICE)


def test_sell_moves_the_average_when_both_lots_are_partly_left():
    # Two lots, sell 1 -> only the oldest (2 @ 100) loses a share: left 1 @ 100 + 2.36127 @ 150.
    after = preview("Sell", 1)["after"]
    assert after["shares"] == pytest.approx(3.36127)
    assert after["cost_basis"] == pytest.approx(100 + 2.36127 * LOT2_PRICE)
    assert after["avg_cost"] == pytest.approx((100 + 2.36127 * LOT2_PRICE) / 3.36127)


def test_sell_percent_pl_uses_the_remaining_cost():
    after = preview("Sell", 2)["after"]
    assert after["pl"] == pytest.approx(2.36127 * 145 - 2.36127 * LOT2_PRICE)
    assert after["pl_pct"] == pytest.approx(after["pl"] / (2.36127 * LOT2_PRICE) * 100)


def test_selling_everything_leaves_no_position():
    after = preview("Sell", 4.36127)["after"]
    assert after["shares"] == pytest.approx(0)
    assert after["avg_cost"] is None
    assert after["pl"] is None and after["pl_pct"] is None


def test_selling_more_than_held_is_rejected():
    with pytest.raises(ValueError, match="only 4.36127 held"):
        preview("Sell", 5)


def test_missing_market_price_gives_shares_and_cost_but_no_pl():
    after = preview("Buy", 10, market=None)["after"]
    assert after["shares"] == pytest.approx(14.36127)
    assert after["cost_basis"] == pytest.approx(2067.53)
    assert after["pl"] is None and after["pl_pct"] is None


def test_bad_inputs_are_rejected():
    with pytest.raises(ValueError):
        preview("Hold", 1)
    with pytest.raises(ValueError):
        preview("Buy", 0)
    with pytest.raises(ValueError):
        preview("Buy", 1, price=-1)


def test_the_real_trade_history_is_not_modified():
    snapshot = BASE.copy()
    preview("Sell", 3)
    pd.testing.assert_frame_equal(BASE, snapshot)
