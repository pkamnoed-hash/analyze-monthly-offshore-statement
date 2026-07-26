import json

import pytest

from slip_parser import ParsedSlip, parse_slip_image


class FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, payload, stop_reason="end_turn"):
        self.content = [FakeTextBlock(json.dumps(payload))] if payload is not None else []
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


BUY_PAYLOAD = {
    "side": "buy",
    "symbol": "NVDY",
    "trade_date": "2026-03-14",
    "quantity": 5,
    "price": 21.50,
    "commission_fee": 1.99,
    "vat": 0.14,
    "sec_fee": None,
    "taf_fee": None,
    "fee_rebate": None,
    "order_type": "Market",
    "order_id": "ORD-123",
}

SELL_PAYLOAD = {
    "side": "sell",
    "symbol": "NVDY",
    "trade_date": "2026-03-20",
    "quantity": 10,
    "price": 25.0,
    "commission_fee": 1.04,
    "vat": 0.00,
    "sec_fee": 0.02,
    "taf_fee": 0.01,
    "fee_rebate": 1.04,
    "order_type": "Limit",
    "order_id": "ORD-456",
}


class TestParseSlipImage:
    def test_parses_a_valid_buy_slip(self):
        client = FakeClient(FakeResponse(BUY_PAYLOAD))
        result = parse_slip_image(b"fakebytes", "image/png", client=client)

        assert isinstance(result, ParsedSlip)
        assert result.side == "buy"
        assert result.symbol == "NVDY"
        assert result.quantity == 5
        assert result.price == 21.50
        assert result.reserved_fee == 0.0

    def test_parses_a_valid_sell_slip_and_computes_reserved_fee(self):
        client = FakeClient(FakeResponse(SELL_PAYLOAD))
        result = parse_slip_image(b"fakebytes", "image/jpeg", client=client)

        assert result.side == "sell"
        assert result.reserved_fee == pytest.approx(0.03)
        assert result.fee_rebate == 1.04

    def test_normalizes_side_casing(self):
        payload = dict(BUY_PAYLOAD, side="BUY")
        client = FakeClient(FakeResponse(payload))
        result = parse_slip_image(b"fakebytes", "image/png", client=client)
        assert result.side == "buy"

    def test_raises_on_refusal(self):
        client = FakeClient(FakeResponse(BUY_PAYLOAD, stop_reason="refusal"))
        with pytest.raises(ValueError, match="declined"):
            parse_slip_image(b"fakebytes", "image/png", client=client)

    def test_raises_on_no_text_content(self):
        client = FakeClient(FakeResponse(None))
        with pytest.raises(ValueError, match="No parseable response"):
            parse_slip_image(b"fakebytes", "image/png", client=client)

    def test_raises_on_malformed_json(self):
        response = FakeResponse(BUY_PAYLOAD)
        response.content = [FakeTextBlock("not valid json{{")]
        client = FakeClient(response)
        with pytest.raises(json.JSONDecodeError):
            parse_slip_image(b"fakebytes", "image/png", client=client)

    def test_allows_null_trade_date_when_not_visible_on_the_slip(self):
        payload = dict(SELL_PAYLOAD, trade_date=None)
        client = FakeClient(FakeResponse(payload))
        result = parse_slip_image(b"fakebytes", "image/jpeg", client=client)
        assert result.trade_date is None

    def test_sends_the_image_as_base64_with_correct_media_type(self):
        client = FakeClient(FakeResponse(BUY_PAYLOAD))
        parse_slip_image(b"fakebytes", "image/jpeg", client=client)

        sent = client.messages.last_kwargs
        image_block = sent["messages"][0]["content"][0]
        assert image_block["type"] == "image"
        assert image_block["source"]["media_type"] == "image/jpeg"
        assert sent["model"] == "claude-opus-5"
