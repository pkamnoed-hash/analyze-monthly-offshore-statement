"""Vision-based parsing of Dime! broker trade confirmation slips via the
Claude API. Kept free of Streamlit imports so it can be unit tested in
isolation (see tests/test_slip_parser.py, which injects a fake client and
never touches the real API or network).
"""

import base64
import json
from dataclasses import dataclass, fields

import anthropic

MODEL = "claude-opus-5"

SLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "symbol": {"type": "string", "description": "The ticker symbol, e.g. NVDY"},
        "trade_date": {
            "type": ["string", "null"],
            "description": "The slip's Completion date (fall back to Submission Date if no "
                            "completion date is shown), as ISO 8601 YYYY-MM-DD. Null if neither "
                            "is visible on the slip -- do not guess.",
        },
        "quantity": {"type": "number", "description": "Number of shares"},
        "price": {"type": "number", "description": "Executed price per share"},
        "commission_fee": {"type": "number", "description": "The slip's Commission Fee"},
        "vat": {"type": "number", "description": "The slip's VAT amount"},
        "sec_fee": {
            "type": ["number", "null"],
            "description": "SEC portion of Reserved Fee -- sell slips only, null on a buy slip",
        },
        "taf_fee": {
            "type": ["number", "null"],
            "description": "TAF portion of Reserved Fee -- sell slips only, null on a buy slip",
        },
        "fee_rebate": {
            "type": ["number", "null"],
            "description": "The Special Coupons rebate amount if shown, else null",
        },
        "order_type": {"type": ["string", "null"], "description": "The Order Type if shown, else null"},
        "order_id": {"type": ["string", "null"], "description": "The Order ID if shown, else null"},
    },
    "required": [
        "side", "symbol", "trade_date", "quantity", "price", "commission_fee", "vat",
        "sec_fee", "taf_fee", "fee_rebate", "order_type", "order_id",
    ],
    "additionalProperties": False,
}

SLIP_PROMPT = """This is a screenshot of a Dime! brokerage trade confirmation slip. Extract the trade details as JSON matching the given schema.

Two known slip layouts:
- BUY slip: header reads "Buy <SYMBOL>". Fields shown: Executed Price, Shares, Stock Amount, Commission Fee, VAT 7%, Order Type, Submission Date, Completion date, Order ID. No Reserved Fee or Special Coupons on a buy slip.
- SELL slip: header reads "Sell <SYMBOL>". Fields shown: Shares, Limit Price, Executed Price, Total Credit, Stock Amount, Commission Fee, Special Coupons (a rebate, if present), VAT 7%, Reserved Fee (broken into SEC Fee and TAF Fee).

Read every field directly off the image -- do not compute or infer a value that isn't printed on the slip."""


@dataclass
class ParsedSlip:
    side: str
    symbol: str
    trade_date: str
    quantity: float
    price: float
    commission_fee: float
    vat: float
    sec_fee: float = 0.0
    taf_fee: float = 0.0
    fee_rebate: float = 0.0
    order_type: str = None
    order_id: str = None

    @property
    def reserved_fee(self) -> float:
        return (self.sec_fee or 0.0) + (self.taf_fee or 0.0)


_FIELD_NAMES = {f.name for f in fields(ParsedSlip)}


def parse_slip_image(image_bytes: bytes, media_type: str, *, api_key: str = None, client=None) -> ParsedSlip:
    """Sends a slip screenshot to Claude for structured field extraction.

    `client` can be injected for testing (see tests/test_slip_parser.py) --
    production callers always omit it and rely on `api_key`. Raises on API
    errors, a refusal, or a malformed response; the Upload Slip page catches
    these and surfaces an actionable st.error message rather than a raw
    traceback."""
    if client is None:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        output_config={"format": {"type": "json_schema", "schema": SLIP_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
                {"type": "text", "text": SLIP_PROMPT},
            ],
        }],
    )

    if response.stop_reason == "refusal":
        raise ValueError("Claude declined to process this image.")

    text = next((block.text for block in response.content if block.type == "text"), None)
    if text is None:
        raise ValueError("No parseable response from Claude.")

    data = json.loads(text)
    data["side"] = str(data["side"]).lower()
    return ParsedSlip(**{k: v for k, v in data.items() if k in _FIELD_NAMES})
