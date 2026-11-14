import json

import pytest

from core.option_slip_parser import ParsedOptionSlip, parse_option_slip_image


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


# Real values from the user's two MNST $45.00 Call "Order Information" screenshots.
BUY_PAYLOAD = {
    "side": "buy",
    "underlying": "MNST",
    "option_type": "Call",
    "strike": 45.00,
    "expiration_date": "2026-12-18",
    "contracts": 1,
    "price": 1.80,
    "trade_date": "2026-11-12",
    "commission_fee": 0.55,
    "vat": 0.04,
    "occ_fee": 0.03,
    "orf_fee": 0.02,
    "taf_fee": None,
    "sec_fee": None,
    "order_id": "OPTBLO2026111105740345974",
}

SELL_PAYLOAD = {
    "side": "sell",
    "underlying": "MNST",
    "option_type": "Call",
    "strike": 45.00,
    "expiration_date": "2026-12-18",
    "contracts": 1,
    "price": 2.35,
    "trade_date": "2026-11-13",
    "commission_fee": 0.55,
    "vat": 0.04,
    "occ_fee": 0.03,
    "orf_fee": 0.02,
    "taf_fee": 0.00,
    "sec_fee": 0.01,
    "order_id": "OPTSLO202611130403339376862",
}


class TestParseOptionSlipImage:
    def test_parses_a_valid_buy_slip(self):
        client = FakeClient(FakeResponse(BUY_PAYLOAD))
        result = parse_option_slip_image(b"fakebytes", "image/png", client=client)

        assert isinstance(result, ParsedOptionSlip)
        assert result.side == "buy"
        assert result.underlying == "MNST"
        assert result.option_type == "Call"
        assert result.strike == 45.00
        assert result.expiration_date == "2026-12-18"
        assert result.contracts == 1
        assert result.price == 1.80
        total_fees = result.commission_fee + result.vat + result.occ_fee + result.orf_fee
        assert round(result.contracts * result.price * 100 + total_fees, 2) == 180.64

    def test_parses_a_valid_sell_slip(self):
        client = FakeClient(FakeResponse(SELL_PAYLOAD))
        result = parse_option_slip_image(b"fakebytes", "image/jpeg", client=client)

        assert result.side == "sell"
        total_fees = (
            result.commission_fee + result.vat + result.occ_fee
            + result.orf_fee + result.taf_fee + result.sec_fee
        )
        assert round(result.contracts * result.price * 100 - total_fees, 2) == 234.35

    def test_null_taf_sec_fee_on_a_buy_slip_preserved_as_none(self):
        # A buy slip genuinely doesn't show TAF/SEC Fee -- preserved as None here,
        # same convention as ParsedSlip's own sec_fee/taf_fee; coalescing to 0.0
        # happens at the point of use (e.g. `fee or 0.0`), not in the dataclass.
        client = FakeClient(FakeResponse(BUY_PAYLOAD))
        result = parse_option_slip_image(b"fakebytes", "image/png", client=client)
        assert result.taf_fee is None
        assert result.sec_fee is None

    def test_normalizes_side_and_option_type_casing(self):
        payload = dict(BUY_PAYLOAD, side="BUY", option_type="call")
        client = FakeClient(FakeResponse(payload))
        result = parse_option_slip_image(b"fakebytes", "image/png", client=client)
        assert result.side == "buy"
        assert result.option_type == "Call"

    def test_raises_on_refusal(self):
        client = FakeClient(FakeResponse(BUY_PAYLOAD, stop_reason="refusal"))
        with pytest.raises(ValueError, match="declined"):
            parse_option_slip_image(b"fakebytes", "image/png", client=client)

    def test_raises_on_no_text_content(self):
        client = FakeClient(FakeResponse(None))
        with pytest.raises(ValueError, match="No parseable response"):
            parse_option_slip_image(b"fakebytes", "image/png", client=client)

    def test_raises_on_malformed_json(self):
        response = FakeResponse(BUY_PAYLOAD)
        response.content = [FakeTextBlock("not valid json{{")]
        client = FakeClient(response)
        with pytest.raises(json.JSONDecodeError):
            parse_option_slip_image(b"fakebytes", "image/png", client=client)

    def test_sends_the_image_as_base64_with_correct_media_type(self):
        client = FakeClient(FakeResponse(BUY_PAYLOAD))
        parse_option_slip_image(b"fakebytes", "image/jpeg", client=client)

        sent = client.messages.last_kwargs
        image_block = sent["messages"][0]["content"][0]
        assert image_block["type"] == "image"
        assert image_block["source"]["media_type"] == "image/jpeg"
        assert sent["model"] == "claude-opus-5"
