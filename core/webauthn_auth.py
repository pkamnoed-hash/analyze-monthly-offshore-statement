"""WebAuthn (Face ID/Touch ID) registration and authentication. Pure logic, no
Streamlit import -- see tests/test_webauthn_auth.py.

Thin wrapper around the `webauthn` library (PyPI package `webauthn`,
github.com/duo-labs/py_webauthn), narrowing its general-purpose API down to
this app's specific case: one shared account (no per-user table, matching
core/auth.py's single-password model), platform authenticators only (Face
ID/Touch ID -- not external USB security keys), user verification always
required (an actual biometric/PIN check, not just "device present").

Every function here takes plain strings/bytes/ints in and returns plain
strings/bytes/ints out -- callers (dashboard_app.py) never touch a
`webauthn`-library object directly, so a future library upgrade that
renames/reshapes those objects only has to be absorbed here.
"""

import webauthn
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

# ES256 only, not the library's own default (EdDSA/ES256/RS256) -- confirmed via real
# iPad registration that Microsoft Authenticator (this app's actual platform credential
# provider on that device, per iOS's own "Passwords" settings, not iCloud Keychain)
# rejects a request offering EdDSA with "Microsoft Authenticator doesn't support this
# passkey." ES256 is the one algorithm virtually every WebAuthn authenticator supports
# (Apple, Google, Microsoft, hardware keys alike), so this is the safe universal choice,
# not a security downgrade -- ES256 is the WebAuthn spec's own baseline-required algorithm.
_SUPPORTED_ALGS = [COSEAlgorithmIdentifier.ECDSA_SHA_256]

# Single-account app -- WebAuthn still requires a stable "user id" bytes value to
# bind every registered device's credential to the same account. Not secret, just
# needs to stay constant across every registration call so the iPad's and iPhone's
# credentials both register under "the same user."
_USER_ID = b"portfolio-tracker-app-user"
_USER_NAME = "Portfolio Tracker"


def decode_challenge(challenge_b64: str) -> bytes:
    """Converts a base64url challenge string back to the bytes verify_registration()/
    verify_authentication() expect. Exists so callers never need `import webauthn`
    directly just to decode a challenge that round-tripped through, e.g., a URL
    query param (see dashboard_app.py's registration flow, where the challenge
    travels inside the result payload itself rather than st.session_state -- keeps
    that flow working correctly regardless of whether a page reload preserves
    session state)."""
    return webauthn.base64url_to_bytes(challenge_b64)


def build_registration_options(rp_id: str, rp_name: str, existing_credential_ids: list[str]) -> tuple[str, bytes]:
    """Returns (options_json, challenge) for a new device registration ceremony.
    `options_json` goes straight to the browser (navigator.credentials.create());
    `challenge` must be held (e.g. in st.session_state) and passed back into
    verify_registration() once the ceremony completes. `existing_credential_ids`
    (base64url text, as stored by core/db.py) are passed as exclude_credentials so
    re-registering an already-registered device is rejected by the OS itself rather
    than silently producing a duplicate row."""
    exclude = [
        PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(cid))
        for cid in existing_credential_ids
    ]
    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=_USER_ID,
        user_name=_USER_NAME,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            user_verification=UserVerificationRequirement.REQUIRED,
            # Explicit, not left at the library's default of unset -- caught live on a
            # real iPad that Microsoft Authenticator refused the request entirely
            # ("doesn't support this passkey") when this was omitted. A "passkey" is a
            # resident/discoverable credential by definition; some providers (apparently
            # including Microsoft Authenticator) only implement that mode and reject a
            # request that doesn't explicitly ask for it. Doesn't conflict with this
            # app's own always-explicit allow_credentials/exclude_credentials use --
            # discoverability is just an authenticator-side storage detail here, never
            # relied on for a usernameless/discoverable-only login flow.
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        exclude_credentials=exclude or None,
        supported_pub_key_algs=_SUPPORTED_ALGS,
    )
    return webauthn.options_to_json(options), options.challenge


def verify_registration(
    credential_json: str, expected_challenge: bytes, expected_origin: str, expected_rp_id: str,
) -> tuple[str, str]:
    """Verifies a completed registration ceremony's response (whatever the browser's
    navigator.credentials.create() returned, JSON-serialized -- the `webauthn` library
    accepts the raw JSON string directly, no manual parsing needed). Returns
    (credential_id, public_key) as base64url text, ready for core/db.py's
    save_webauthn_credential(). Raises on any verification failure (wrong challenge,
    wrong origin, no user verification performed, malformed response, etc.) -- the
    caller catches and shows an error; nothing gets saved on failure."""
    verified = webauthn.verify_registration_response(
        credential=credential_json,
        expected_challenge=expected_challenge,
        expected_origin=expected_origin,
        expected_rp_id=expected_rp_id,
        require_user_verification=True,
    )
    return bytes_to_base64url(verified.credential_id), bytes_to_base64url(verified.credential_public_key)


def build_authentication_options(rp_id: str, allowed_credential_ids: list[str]) -> tuple[str, bytes]:
    """Returns (options_json, challenge) for a login ceremony. `allowed_credential_ids`
    (base64url text, every currently-registered device from
    core/db.py's fetch_webauthn_credentials()) becomes allow_credentials -- only those
    specific devices' authenticators will offer to respond."""
    allow = [
        PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(cid))
        for cid in allowed_credential_ids
    ]
    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow or None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return webauthn.options_to_json(options), options.challenge


def verify_authentication(
    credential_json: str, expected_challenge: bytes, expected_origin: str, expected_rp_id: str,
    stored_public_key: str, stored_sign_count: int,
) -> int:
    """Verifies a completed login ceremony's response against ONE already-matched
    stored credential (the caller looks up which row by the credential id embedded in
    `credential_json` before calling this -- see dashboard_app.py). `stored_public_key`
    is base64url text as stored by core/db.py. Returns the new sign count to persist
    via update_webauthn_sign_count() -- WebAuthn's replay-attack defense requires this
    counter to strictly increase; verify_authentication_response() itself rejects a
    response whose counter isn't higher than `stored_sign_count`, raising before this
    function would return. Raises on any other verification failure too."""
    verified = webauthn.verify_authentication_response(
        credential=credential_json,
        expected_challenge=expected_challenge,
        expected_origin=expected_origin,
        expected_rp_id=expected_rp_id,
        credential_public_key=webauthn.base64url_to_bytes(stored_public_key),
        credential_current_sign_count=stored_sign_count,
        require_user_verification=True,
    )
    return verified.new_sign_count
