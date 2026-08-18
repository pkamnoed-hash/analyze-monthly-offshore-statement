"""Static (no-build) Streamlit component that calls the browser's WebAuthn
navigator.credentials.get() directly inside its own iframe -- unlike
registration (see webauthn_register_component.py), Streamlit's component
iframe permits the `publickey-credentials-get` Permissions Policy feature but
NOT `publickey-credentials-create` (confirmed by grepping the installed
Streamlit frontend bundle's IFrameUtil.*.js `allow` attribute directly, not
assumed), so login can run in this simpler, direct-in-iframe shape while
registration cannot.

Renders its own "Unlock with Face ID / Touch ID" button INSIDE the iframe --
the click that starts a WebAuthn ceremony must originate in the same document
that calls navigator.credentials (the WebAuthn spec requires an active user-
activation gesture; a parent-page st.button click doesn't carry that gesture
across an iframe boundary), so this component owns its own trigger, not just
the ceremony.

Bridge is the same hand-written Streamlit postMessage protocol
trendline_chart_component.py already uses (verified against the same
installed frontend bundle), extended with an `async` click handler since
navigator.credentials.get() is Promise-based -- the existing component's
bridge never needed that.
"""

from pathlib import Path

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).parent / "webauthn_login"
_component_func = components.declare_component("webauthn_login", path=str(_COMPONENT_DIR))


def webauthn_authenticate(options_json: str, *, key: str | None = None) -> dict | None:
    """`options_json` is core.webauthn_auth.build_authentication_options()'s first
    return value, passed straight through -- the browser needs its exact shape
    (challenge/allowCredentials already base64url text, JSON-serialized).

    Returns None until the button inside the component is clicked and the ceremony
    resolves one way or the other:
    - {"status": "success", "credential_json": {...}} -- the assertion response,
      already shaped for core.webauthn_auth.verify_authentication()'s
      credential_json parameter (that function accepts it as-is, no reshaping
      needed on the Python side -- the JS side builds it to match exactly what
      `webauthn.helpers.parse_authentication_credential_json` expects).
    - {"status": "error", "message": str} -- declined, cancelled, timed out, or no
      matching credential registered on this device. Never fatal -- the caller
      falls back to the password form, which stays visible regardless."""
    return _component_func(options_json=options_json, key=key, default=None)
