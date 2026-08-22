import base64
import json
import os

import streamlit as st

from app_pages.components.webauthn_login_component import webauthn_authenticate
from core import db
from core.auth import generate_salt, hash_password, verify_password
from core.db import init_db
from core.version import current_app_version
from core.webauthn_auth import (
    build_authentication_options,
    build_registration_options,
    decode_challenge,
    verify_authentication,
    verify_registration,
)

# v4.6.1 -- runs navigator.credentials.create() directly in this page's own top-level
# document via st.html(unsafe_allow_javascript=True), NOT a Streamlit custom component
# (unlike login's webauthn_authenticate above). Three real, hard-won reasons, confirmed
# on real hardware and by reading the installed Streamlit source, not assumed:
# 1. A component's own iframe blocks navigator.credentials.create() outright --
#    Streamlit's component iframe Permissions Policy allows publickey-credentials-get
#    (why login can stay a component) but not publickey-credentials-create (confirmed
#    by grepping the installed Streamlit frontend bundle's IFrameUtil.*.js).
# 2. A same-origin popup opened via window.open() (this branch's first fix attempt for
#    #1) broke in actual production use: Streamlit Community Cloud's own private-app
#    viewer authentication (separate from and in front of this app's own password
#    gate) doesn't reliably carry its session into a popup on iOS Safari, leaving the
#    popup stuck in Streamlit's own login redirect forever instead of ever reaching
#    the registration page.
# 3. st.markdown(unsafe_allow_html=True) (this branch's second fix attempt) silently
#    strips inline event handler attributes like onclick -- confirmed from source
#    (Html.*.js's DOMPurify config for st.markdown has no ADD_ATTR override, so
#    DOMPurify's default forbid-all-on*-attributes behavior applies). st.html's own
#    unsafe_allow_javascript=True path uses a DIFFERENT config that specifically
#    re-allows <script> tags (extracting and recreating them as real, executing
#    script elements -- the standard "innerHTML scripts don't run, but
#    dynamically-created ones do" workaround) and, per its own docstring, is never
#    iframed either -- solving both #1 and #3 at once, and #2 doesn't apply since
#    there's no popup/separate browsing context involved here at all.
#
# Result gets back to Python via st.query_params, set through a real page navigation
# (not Streamlit's component protocol, which doesn't exist for raw injected HTML).
# Deliberately self-contained: the challenge and device label round-trip inside the
# result payload itself (challenge_b64/device_label below) rather than st.session_state,
# so this works correctly regardless of whether a page navigation preserves session
# state -- one less thing to depend on. Only navigates on genuine success; a
# cancel/error just updates the on-page status text in place, no reload needed since
# nothing has to reach Python for those.
_WEBAUTHN_REG_SCRIPT = """<script>
(function () {
  function b64uToBuf(s) {
    const p = '='.repeat((4 - (s.length % 4)) % 4);
    const b64 = (s + p).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes.buffer;
  }
  function bufToB64u(buf) {
    const bytes = new Uint8Array(buf);
    let str = '';
    for (const b of bytes) str += String.fromCharCode(b);
    return btoa(str).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
  }
  const btn = document.getElementById('webauthn_reg_btn');
  const status = document.getElementById('webauthn_reg_status');
  if (!btn || btn.dataset.bound === '1') return;
  btn.dataset.bound = '1';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    status.textContent = 'Waiting for Face ID / Touch ID...';
    try {
      if (!window.PublicKeyCredential) { throw new Error('This browser does not support WebAuthn.'); }
      const optionsJson = atob(btn.dataset.optionsB64);
      const opts = JSON.parse(optionsJson);
      const challengeB64 = opts.challenge;
      opts.challenge = b64uToBuf(opts.challenge);
      opts.user.id = b64uToBuf(opts.user.id);
      if (opts.excludeCredentials) {
        opts.excludeCredentials = opts.excludeCredentials.map((c) => Object.assign({}, c, { id: b64uToBuf(c.id) }));
      }
      const credential = await navigator.credentials.create({ publicKey: opts });
      const response = credential.response;
      const credentialJson = {
        id: credential.id,
        rawId: bufToB64u(credential.rawId),
        type: credential.type,
        clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
        response: {
          clientDataJSON: bufToB64u(response.clientDataJSON),
          attestationObject: bufToB64u(response.attestationObject),
          transports: response.getTransports ? response.getTransports() : [],
        },
      };
      const resultObj = {
        status: 'success',
        credential_json: credentialJson,
        challenge_b64: challengeB64,
        device_label: atob(btn.dataset.labelB64),
      };
      const resultB64 = btoa(JSON.stringify(resultObj));
      const url = new URL(window.location.href);
      url.searchParams.set('webauthn_reg_result', resultB64);
      status.textContent = 'Verifying...';
      window.location.href = url.toString();
    } catch (err) {
      status.textContent = err instanceof Error ? err.message : String(err);
      btn.disabled = false;
    }
  });
})();
</script>"""

st.set_page_config(page_title="Financial Summary Dashboard", layout="wide")

# core/db.py deliberately has no Streamlit import (see CLAUDE.md), so it reads its
# Turso connection details from the environment instead of st.secrets directly --
# bridge them here, before init_db() or any other core.db call happens below.
os.environ.setdefault("TURSO_DATABASE_URL", st.secrets["TURSO_DATABASE_URL"])
os.environ.setdefault("TURSO_AUTH_TOKEN", st.secrets["TURSO_AUTH_TOKEN"])

# v4.6 -- defaults to the real prod origin so a secrets.toml without these keys still
# behaves correctly there; dev/tunnel testing overrides all three explicitly (WebAuthn
# binds a credential to the exact RP ID/origin it was created against, so these must
# match whatever's actually in the browser's address bar or every ceremony fails).
WEBAUTHN_RP_ID = st.secrets.get("WEBAUTHN_RP_ID", "myinvestment27.streamlit.app")
WEBAUTHN_RP_NAME = st.secrets.get("WEBAUTHN_RP_NAME", "Portfolio Tracker")
WEBAUTHN_ORIGIN = st.secrets.get("WEBAUTHN_ORIGIN", "https://myinvestment27.streamlit.app")


def _effective_credential() -> tuple[str, str]:
    """v4.7 -- (salt, password_hash) to check a login/change-password attempt against:
    the app_password DB row if the password's ever been changed from the app, else the
    original st.secrets values every deploy starts with. Deliberately uncached -- called
    at most once per page load (the login form or the change-password form, never both
    in the same run), so a password changed in one tab is picked up immediately
    everywhere else rather than waiting out a stale cache."""
    row = db.fetch_app_password()
    if row is not None:
        return row
    return st.secrets["APP_PASSWORD_SALT"], st.secrets["APP_PASSWORD_HASH"]


# v4.5 -- init_db() only needs to run once per browser session, not on every single
# rerun (every navigation/widget interaction re-executes this whole script). It was
# unconditional before, meaning every rerun paid a real Turso round trip per
# CREATE TABLE statement plus the reference_lines migration check -- one of three
# real causes behind the app's "always reloads" feeling (see docs/ROADMAP.md V4.5).
# A brand-new session still runs it exactly once, so a fresh deploy still initializes
# its schema correctly.
#
# v4.6 moved this above the login gate (it used to run only after authenticating) --
# the login page itself now needs to read webauthn_credentials (to decide whether to
# offer a biometric unlock button at all), which won't exist yet on a genuinely fresh
# database without this having run first, even for a not-yet-authenticated session.
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state["db_initialized"] = True

if not st.session_state.get("authenticated"):
    st.title("Financial Summary Dashboard")

    # v4.6 -- offered above the password form when at least one device is registered;
    # falls through to the password form below on any cancel/failure/error, which is
    # always rendered regardless -- never a dead end. Options are generated once per
    # unauthenticated session (not regenerated on every rerun of this block, e.g. a
    # failed password attempt), same reasoning as the registration expander, just
    # without needing an extra "start" click first -- unlike that sidebar expander,
    # this login page is only ever shown a handful of times per real session (once
    # per not-yet-authenticated visit), not on every navigation across the whole app,
    # so showing the button immediately is better UX with no real cost.
    registered = db.fetch_webauthn_credentials()
    if not registered.empty:
        if "webauthn_login_options" not in st.session_state:
            options_json, challenge = build_authentication_options(
                WEBAUTHN_RP_ID, registered["Credential Id"].tolist(),
            )
            st.session_state["webauthn_login_options"] = options_json
            st.session_state["webauthn_login_challenge"] = challenge

        result = webauthn_authenticate(st.session_state["webauthn_login_options"], key="webauthn_login_component")
        if result and result.get("status") == "success":
            cred_id = result["credential_json"]["id"]
            matching = registered[registered["Credential Id"] == cred_id]
            if matching.empty:
                st.warning("Unrecognized device -- use your password below.")
            else:
                row = matching.iloc[0]
                try:
                    new_sign_count = verify_authentication(
                        result["credential_json"], st.session_state["webauthn_login_challenge"],
                        WEBAUTHN_ORIGIN, WEBAUTHN_RP_ID, row["Public Key"], int(row["Sign Count"]),
                    )
                    db.update_webauthn_sign_count(cred_id, new_sign_count)
                    st.session_state["authenticated"] = True
                    st.session_state.pop("webauthn_login_options", None)
                    st.session_state.pop("webauthn_login_challenge", None)
                    st.rerun()
                except Exception:
                    st.warning("Biometric login failed to verify -- use your password below.")
        elif result and result.get("status") == "error":
            st.caption("Biometric login unavailable or cancelled -- use your password below.")
        st.divider()

    # st.form so pressing Enter in the password field submits -- a bare st.text_input +
    # st.button pair doesn't do this in Streamlit; Enter only commits the text_input's
    # own value, it doesn't trigger a separate button below it. Enter inside a form
    # triggers that form's submit button instead.
    with st.form("login_form"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if verify_password(pw, *_effective_credential()):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

if st.sidebar.button("Log out"):
    st.session_state["authenticated"] = False
    st.rerun()

# v4.7 -- placed before "Set up Face ID / Touch ID" below: this is the more
# fundamental credential control (it's what biometric login falls back to, and the
# only way to register a new device in the first place), so it reads naturally ahead
# of the additive convenience layered on top of it.
with st.sidebar.expander("Change Password"):
    with st.form("change_password_form", clear_on_submit=True):
        current_pw = st.text_input("Current password", type="password")
        new_pw = st.text_input("New password", type="password")
        confirm_pw = st.text_input("Confirm new password", type="password")
        change_submitted = st.form_submit_button("Change password")
    if change_submitted:
        if not verify_password(current_pw, *_effective_credential()):
            st.error("Current password is incorrect.")
        elif not new_pw:
            st.error("New password can't be empty.")
        elif new_pw != confirm_pw:
            st.error("New password and confirmation don't match.")
        else:
            new_salt = generate_salt()
            db.save_app_password(new_salt, hash_password(new_pw, new_salt))
            st.success("Password changed.")

# v4.6 -- additive to the password gate above, never a replacement: registering a
# device requires already being logged in via password (there's no way to reach this
# without one), and password login itself is completely unaffected whether or not
# anyone ever opens this expander.
with st.sidebar.expander("Set up Face ID / Touch ID"):
    # A completed ceremony's result arrives via a real page navigation (see
    # _WEBAUTHN_REG_SCRIPT above) -- handle it before anything else in this block,
    # then clear it immediately so refreshing the page never reprocesses it.
    if "webauthn_reg_result" in st.query_params:
        try:
            result = json.loads(base64.b64decode(st.query_params["webauthn_reg_result"]))
        except Exception:
            result = None
        del st.query_params["webauthn_reg_result"]
        if result and result.get("status") == "success":
            try:
                credential_id, public_key = verify_registration(
                    result["credential_json"], decode_challenge(result["challenge_b64"]),
                    WEBAUTHN_ORIGIN, WEBAUTHN_RP_ID,
                )
                db.save_webauthn_credential(
                    credential_id, public_key, device_label=result.get("device_label") or None,
                )
                st.success("Device registered for Face ID / Touch ID.")
            except Exception:
                st.error("Registration couldn't be verified -- please try again.")
        elif result and result.get("status") == "error":
            st.warning(f"Registration didn't complete: {result.get('message', 'unknown error')} -- please try again.")

    registered = db.fetch_webauthn_credentials()
    if not registered.empty:
        st.caption("Registered devices:")
        for _, row in registered.iterrows():
            st.caption(f"• {row['Device Label'] or 'Unnamed device'} — added {row['Created At']}")

    device_label = st.text_input("Device label", placeholder="e.g. iPad", key="webauthn_device_label")

    # Two-step, same reasoning as before: a plain button generates fresh options only
    # on click, so a wasted crypto/random-challenge call doesn't run on every render of
    # this expander across every navigation in the app -- only when someone actually
    # clicks to start.
    if st.button("Start registration", key="webauthn_start_reg"):
        existing_ids = registered["Credential Id"].tolist() if not registered.empty else []
        options_json, _ = build_registration_options(WEBAUTHN_RP_ID, WEBAUTHN_RP_NAME, existing_ids)
        st.session_state["webauthn_reg_options_b64"] = base64.b64encode(options_json.encode()).decode()

    if "webauthn_reg_options_b64" in st.session_state:
        options_b64 = st.session_state["webauthn_reg_options_b64"]
        label_b64 = base64.b64encode((device_label or "").encode()).decode()
        st.html(
            f"""
<button id="webauthn_reg_btn" data-options-b64="{options_b64}" data-label-b64="{label_b64}"
        style="width:100%;padding:0.5rem 1rem;font-size:0.95rem;font-weight:500;border-radius:6px;
               border:1px solid rgba(250,250,250,0.2);background:transparent;color:inherit;cursor:pointer;">
  Register this device
</button>
<div id="webauthn_reg_status" style="font-size:0.78rem;color:#9aa0a6;margin-top:4px;min-height:1em;"></div>
{_WEBAUTHN_REG_SCRIPT}
""",
            unsafe_allow_javascript=True,
        )

pg = st.navigation({
    "Overview": [
        st.Page("app_pages/dashboard.py", title="Dashboard", default=True),
        st.Page("app_pages/monitor_stocks.py", title="Monitor Stocks"),
    ],
    "Input": [
        st.Page("app_pages/record_trade.py", title="Record Trade"),
        st.Page("app_pages/record_dividend.py", title="Record Dividend"),
    ],
    "Tools": [
        st.Page("app_pages/reconciliation.py", title="Reconciliation"),
        st.Page("app_pages/allocation_type.py", title="Allocation Type"),
        st.Page("app_pages/backup.py", title="System Backup"),
        st.Page("app_pages/rebalance.py", title="Rebalance & Reallocate"),
    ],
    "Analysis": [
        # Reachable both ways -- directly from the sidebar (shows its own symbol picker
        # when opened with no "symbol" query param, see app_pages/symbol_analysis.py) and
        # via the "view" cell on Monitor Stocks' Overall/Reference Lines/Highlight tabs
        # (st.switch_page(..., query_params={"symbol": ...}), which pre-selects a symbol
        # and skips the picker).
        st.Page("app_pages/symbol_analysis.py", title="Auto Trendline", url_path="symbol-analysis"),
        st.Page("app_pages/target_allocation.py", title="Target Allocation"),
    ],
})
pg.run()

# Placed after pg.run() deliberately -- Streamlit renders the nav's page list as part of
# pg.run() executing, so anything appended to the sidebar after that call lands below it,
# at the bottom of the left menu.
st.sidebar.caption(f"Version {current_app_version()}")

# APP_ENV is an explicit, manually-set secret (not inferred from the Turso URL --
# safer to require stating it outright than to guess from a hostname pattern) --
# defaults to "prod" so existing secrets.toml files without this key still show the
# correct, safe default. Toggle this alongside TURSO_DATABASE_URL/TURSO_AUTH_TOKEN
# when switching environments (see docs/BACKUP_AND_TESTING.md).
# Solid background colors, not emoji -- emoji circle colors (🟢/🟡) render too
# similarly to reliably tell apart at a glance, which defeats the point of this label.
_app_env = st.secrets.get("APP_ENV", "prod")
_env_color = "#2e7d32" if _app_env == "dev" else "#c62828"  # green=dev (safe), red=prod (caution)
_env_label = "DEV" if _app_env == "dev" else "PROD"
st.sidebar.markdown(
    f"<span style='background-color:{_env_color};color:white;padding:2px 10px;"
    f"border-radius:4px;font-size:0.85em;font-weight:600;'>{_env_label} environment</span>",
    unsafe_allow_html=True,
)
