"""Static (no-build) Streamlit component that triggers WebAuthn REGISTRATION
(navigator.credentials.create()) via a same-origin popup relay -- NOT directly
inside this component's own iframe, unlike webauthn_login_component.py's
authenticate() call. Streamlit's component iframe permits the
`publickey-credentials-get` Permissions Policy feature but NOT
`publickey-credentials-create` (confirmed by grepping the installed Streamlit
frontend bundle's IFrameUtil.*.js `allow` attribute directly) -- an iframe
without a feature explicitly listed there has it OFF, even same-origin, so
create() has to run in a genuine top-level browsing context instead.

The button's click handler opens static/webauthn_register.html (served by
Streamlit's own static-file serving -- see .streamlit/config.toml's
enableStaticServing) as a real popup window, still inside the same click that
started the ceremony, so the user-activation gesture both window.open() and
(once the popup runs it) navigator.credentials.create() require is preserved.
Forwards the server-generated options to the popup via postMessage, and
relays whatever the popup posts back into Streamlit.setComponentValue().

The popup-relay mechanism itself (desktop-browser postMessage + window.close()
is well-established; mobile Safari's specific handling isn't something that
can be confirmed without a real device) is the one piece of this component
verified on real hardware during Step 2 before any surrounding registration
UI gets built on top of it -- see docs/ROADMAP.md's V4.6 section once that
verification is done."""

from pathlib import Path

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).parent / "webauthn_register"
_component_func = components.declare_component("webauthn_register", path=str(_COMPONENT_DIR))


def webauthn_register(options_json: str, *, key: str | None = None) -> dict | None:
    """`options_json` is core.webauthn_auth.build_registration_options()'s first
    return value.

    Returns None until the button is clicked and the popup ceremony resolves:
    - {"status": "success", "credential_json": {...}} -- the attestation response,
      already shaped for core.webauthn_auth.verify_registration()'s credential_json
      parameter.
    - {"status": "error", "message": str} -- popup blocked, declined, cancelled, or
      any other failure. Never fatal -- the caller shows a message and leaves the
      rest of the registration UI intact to retry."""
    return _component_func(options_json=options_json, key=key, default=None)
