"""Vision-based parsing of Dime! option "Order Information" confirmation screens via
the Claude API -- mirrors core/slip_parser.py's structure exactly (same SDK call
shape), new schema/prompt for the option layout instead of the stock trade slip.
Kept free of Streamlit imports so it can be unit tested in isolation (see
tests/test_option_slip_parser.py, which injects a fake client and never touches the
real API or network).

Deliberately does NOT extract `position_effect` (open/close) -- the screenshot only
ever shows "Buy"/"Sell", never whether that buy/sell opened or closed a position, so
inventing it here would violate this project's "never invent a value that isn't
actually on the slip" rule. The user picks Open/Close themselves when reviewing the
pre-filled form, same as Option Monitor's manual-entry fields they're free to edit
before Save.
"""

import base64
import json
from dataclasses import dataclass, fields

import anthropic

MODEL = "claude-opus-5"

OPTION_SLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "underlying": {"type": "string", "description": "The underlying ticker symbol, e.g. MNST"},
        "option_type": {"type": "string", "enum": ["Call", "Put"]},
        "strike": {"type": "number", "description": "The strike price, e.g. 45.00"},
        "expiration_date": {
            "type": "string",
            "description": "The slip's Expiration Date, as ISO 8601 YYYY-MM-DD, e.g. '18 December 2026' -> '2026-12-18'.",
        },
        "contracts": {"type": "number", "description": "Number of contracts"},
        "price": {"type": "number", "description": "Executed Price/Share"},
        "trade_date": {
            "type": ["string", "null"],
            "description": "The slip's Completion Date (fall back to Submission Date if no "
                            "Completion Date is shown), as ISO 8601 YYYY-MM-DD. Null if neither "
                            "is visible on the slip -- do not guess.",
        },
        "commission_fee": {"type": "number", "description": "The slip's Commission Fee"},
        "vat": {"type": "number", "description": "The slip's VAT amount"},
        "occ_fee": {"type": ["number", "null"], "description": "OCC Fee under Estimated Reserved Fee, else null"},
        "orf_fee": {"type": ["number", "null"], "description": "ORF Fee under Estimated Reserved Fee, else null"},
        "taf_fee": {"type": ["number", "null"], "description": "TAF Fee under Estimated Reserved Fee, else null"},
        "sec_fee": {"type": ["number", "null"], "description": "SEC Fee under Estimated Reserved Fee, else null"},
        "order_id": {"type": ["string", "null"], "description": "The Order ID if shown, else null"},
    },
    "required": [
        "side", "underlying", "option_type", "strike", "expiration_date", "contracts", "price",
        "trade_date", "commission_fee", "vat", "occ_fee", "orf_fee", "taf_fee", "sec_fee", "order_id",
    ],
    "additionalProperties": False,
}

OPTION_SLIP_PROMPT = """This is a screenshot of a Dime! brokerage "Order Information" screen for a US option trade. Extract the trade details as JSON matching the given schema.

Header reads "Buy <UNDERLYING> <Call|Put> <STRIKE> USD" or "Sell <UNDERLYING> <Call|Put> <STRIKE> USD" -- underlying, option_type, and strike all come from this line. Below it: Expiration Date, "N Contract(s) = N*100 Shares", Limit Price, Executed Price/Share, then either "Total Cost"/"Contract Cost" (buy) or "Estimated Total Credit"/"Total Contract Value" (sell), followed by Commission Fee and VAT 7%, then an "Estimated Reserved Fee" section listing OCC Fee, ORF Fee, and on a sell slip also TAF Fee and SEC Fee (a buy slip may omit TAF Fee/SEC Fee entirely -- use null, don't assume 0). Submission Date / Completion Date and Order ID are near the bottom.

Read every field directly off the image -- do not compute or infer a value that isn't printed on the slip. Do NOT attempt to determine whether this trade opened or closed a position -- that's never shown on the slip itself."""


@dataclass
class ParsedOptionSlip:
    side: str
    underlying: str
    option_type: str
    strike: float
    expiration_date: str
    contracts: float
    price: float
    trade_date: str
    commission_fee: float
    vat: float
    occ_fee: float = 0.0
    orf_fee: float = 0.0
    taf_fee: float = 0.0
    sec_fee: float = 0.0
    order_id: str = None


_FIELD_NAMES = {f.name for f in fields(ParsedOptionSlip)}


def parse_option_slip_image(image_bytes: bytes, media_type: str, *, api_key: str = None, client=None) -> ParsedOptionSlip:
    """Sends an option "Order Information" screenshot to Claude for structured field
    extraction.

    `client` can be injected for testing (see tests/test_option_slip_parser.py) --
    production callers always omit it and rely on `api_key`. Raises on API errors, a
    refusal, or a malformed response; Option Monitor catches these and surfaces an
    actionable st.error message rather than a raw traceback -- same contract as
    core/slip_parser.py::parse_slip_image."""
    if client is None:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        output_config={"format": {"type": "json_schema", "schema": OPTION_SLIP_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
                {"type": "text", "text": OPTION_SLIP_PROMPT},
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
    data["option_type"] = str(data["option_type"]).title()
    return ParsedOptionSlip(**{k: v for k, v in data.items() if k in _FIELD_NAMES})
