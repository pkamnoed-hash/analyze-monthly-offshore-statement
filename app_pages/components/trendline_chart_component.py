"""Static (no-build) Streamlit component wrapping lightweight-charts for a
candlestick/line chart with draggable/deletable/creatable "Reference Lines"
(swing highs/lows nearest to current price, captured at a moment -- see
core.calculations.compute_reference_lines), an optional MA 50/100/200
overlay, a Cost/Sh reference line, a Latest Price reference line, and a
synced Stochastic oscillator pane below it.

Reference Lines replace three earlier, separate line concepts this
component used to render: fixed Pivot Points R1-R3/S1-S3 levels (a formula
anchored to Cost/Sh), a diagonal swing-based Trend Line, and clustered
horizontal S/R Zones. All three were consolidated into one concept, driven
by real user feedback that the page had accumulated too many competing line
ideas at once. The old implementation is preserved as a commented-out block
at the bottom of trendline_chart/index.html, not deleted.

The frontend (trendline_chart/index.html) adapts the chart setup and the
manual hit-test-and-drag technique from a sibling standalone project,
lab_chart/app.js (not part of this repo) -- same lightweight-charts version,
same drag logic (lightweight-charts has no built-in line dragging, so it's
done by hand: hit-test the mouse position against each line's pixel
coordinate, move the line with applyOptions() while suppressing the chart's
own pan/zoom). Drag/delete use the Pointer Events API with
setPointerCapture rather than plain mouse events -- Streamlit renders every
custom component inside its own iframe, so a fast drag that crosses the
iframe's edge before the mouse button comes up can otherwise miss its
mouseup entirely, leaving the drag stuck (same root cause a design-mockup
Artifact hit and fixed the same way; here it's a real, not hypothetical,
risk since declare_component always iframe-isolates).

The bridge back to Python is a small hand-written implementation of
Streamlit's component postMessage protocol (verified against this app's
actual installed Streamlit frontend bundle), not the full
streamlit-component-lib package -- no build step, no new dependency beyond
the lightweight-charts CDN script the component's own HTML loads.
"""

from pathlib import Path

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).parent / "trendline_chart"
_component_func = components.declare_component("trendline_chart", path=str(_COMPONENT_DIR))


def trendline_chart(
    candles: list[dict],
    reference_lines: list[dict],
    *,
    show_reference_lines: bool = True,
    cost_per_share: float | None = None,
    latest_price: float | None = None,
    ma_series: dict | None = None,
    ma_visible: dict | None = None,
    chart_type: str = "candle",
    visibility: dict | None = None,
    locked: bool = False,
    stochastic: dict | None = None,
    key: str | None = None,
) -> dict | None:
    """Renders the chart. `candles` is a list of {time, open, high, low,
    close} dicts (time as "yyyy-mm-dd") -- already whatever the caller wants
    drawn (real OHLC, or Heikin Ashi-transformed; the component itself has
    no opinion, it just draws what it's given).

    `reference_lines` is a list of {"id": int, "price": float} dicts -- the
    caller's currently CAPTURED set (see calculations.compute_reference_lines
    and the caller's own session-state list), not recomputed by this
    component. Each line's resistance/support classification and color are
    derived here, live, from `price` vs. `latest_price` -- never passed in
    as a fixed field, so a line that's dragged (or that the market moves)
    across `latest_price` recolors correctly with no special-case handling.
    `show_reference_lines` toggles all of them at once; `locked` disables
    dragging (but not deleting -- removing a line is always a deliberate
    action, unaffected by the lock).

    `chart_type` is "candle" or "line" -- "line" draws each candle's Close
    as a line instead (Heikin Ashi isn't a separate value here; it's
    already baked into `candles` by the caller before this is called).

    `ma_series` is {period: [{time, value}, ...]} for period in (50, 100,
    200) -- always real Close, never Heikin Ashi-smoothed, computed by the
    caller. `ma_visible` is {period: bool} controlling which of the three
    actually render; all three can be passed every time regardless of
    visibility (cheap), only what's toggled on gets drawn.

    `cost_per_share` draws a plain, non-draggable, non-deletable reference
    line (your real Avg Cost) -- a fact, not an adjustable target, styled
    the same finer-dotted way as `latest_price`'s line. `visibility` is
    {"pivot": bool, "latest": bool} -- `pivot` follows Zone 3's "Cost/Sh"
    toggle; `latest` is always passed True by the caller (no separate
    toggle for it -- the latest-price line is a fact the user always wants
    visible, same as the "Latest Price" stat already shown above the
    chart). Reference Lines have their own top-level `show_reference_lines`
    toggle instead, since they're no longer part of the same "levels"
    concept Cost/Sh used to ride along with.

    `stochastic` is {"k": [{time, value}, ...], "d": [...]} for the synced
    oscillator pane below the main chart, or None to leave it empty.

    Returns None if nothing happened this render (the normal case), or a
    dict describing the most recent user action:
    - {"action": "drag", "id": 3, "price": 142.10} when a reference line
      was just dragged to a new price.
    - {"action": "delete", "id": 3} when a reference line's × was just
      clicked.
    """
    return _component_func(
        candles=candles,
        reference_lines=reference_lines,
        show_reference_lines=show_reference_lines,
        cost_per_share=cost_per_share,
        latest_price=latest_price,
        ma_series=ma_series or {},
        ma_visible=ma_visible or {},
        chart_type=chart_type,
        visibility=visibility if visibility is not None else {"pivot": True, "latest": True},
        locked=locked,
        stochastic=stochastic,
        key=key,
        default=None,
    )
