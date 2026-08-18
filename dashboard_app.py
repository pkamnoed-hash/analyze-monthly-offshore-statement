import os

import streamlit as st

from app_pages.components.webauthn_register_component import webauthn_register
from core import db
from core.auth import verify_password
from core.db import init_db
from core.version import current_app_version
from core.webauthn_auth import build_registration_options, verify_registration

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

if not st.session_state.get("authenticated"):
    st.title("Financial Summary Dashboard")
    # st.form so pressing Enter in the password field submits -- a bare st.text_input +
    # st.button pair doesn't do this in Streamlit; Enter only commits the text_input's
    # own value, it doesn't trigger a separate button below it. Enter inside a form
    # triggers that form's submit button instead.
    with st.form("login_form"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if verify_password(pw, st.secrets["APP_PASSWORD_SALT"], st.secrets["APP_PASSWORD_HASH"]):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

if st.sidebar.button("Log out"):
    st.session_state["authenticated"] = False
    st.rerun()

# v4.5 -- init_db() only needs to run once per browser session, not on every single
# rerun (every navigation/widget interaction re-executes this whole script). It was
# unconditional before, meaning every rerun paid a real Turso round trip per
# CREATE TABLE statement plus the reference_lines migration check -- one of three
# real causes behind the app's "always reloads" feeling (see docs/ROADMAP.md V4.5).
# A brand-new session still runs it exactly once, so a fresh deploy still initializes
# its schema correctly. Must run before the Face ID/Touch ID expander below -- that
# reads webauthn_credentials, which won't exist yet on a genuinely fresh database.
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state["db_initialized"] = True

# v4.6 -- additive to the password gate above, never a replacement: registering a
# device requires already being logged in via password (there's no way to reach this
# without one), and password login itself is completely unaffected whether or not
# anyone ever opens this expander. See
# app_pages/components/webauthn_register_component.py's own docstring for why
# registration needs this two-step (plain button generates options, THEN the
# component's own internal button actually starts the ceremony) rather than a single
# click -- generating options on every single page render (even collapsed/unopened)
# would mean a wasted crypto/random-challenge operation on every navigation across the
# whole app, not just when someone actually opens this expander.
with st.sidebar.expander("Set up Face ID / Touch ID"):
    registered = db.fetch_webauthn_credentials()
    if not registered.empty:
        st.caption("Registered devices:")
        for _, row in registered.iterrows():
            st.caption(f"• {row['Device Label'] or 'Unnamed device'} — added {row['Created At']}")

    device_label = st.text_input("Device label", placeholder="e.g. iPad", key="webauthn_device_label")

    if st.button("Start registration", key="webauthn_start_reg"):
        existing_ids = registered["Credential Id"].tolist() if not registered.empty else []
        options_json, challenge = build_registration_options(WEBAUTHN_RP_ID, WEBAUTHN_RP_NAME, existing_ids)
        st.session_state["webauthn_reg_options"] = options_json
        st.session_state["webauthn_reg_challenge"] = challenge
        st.session_state.pop("webauthn_reg_handled", None)

    if "webauthn_reg_options" in st.session_state:
        result = webauthn_register(st.session_state["webauthn_reg_options"], key="webauthn_register_component")
        if result and result.get("status") == "success" and not st.session_state.get("webauthn_reg_handled"):
            try:
                credential_id, public_key = verify_registration(
                    result["credential_json"], st.session_state["webauthn_reg_challenge"],
                    WEBAUTHN_ORIGIN, WEBAUTHN_RP_ID,
                )
                db.save_webauthn_credential(credential_id, public_key, device_label=device_label or None)
                st.session_state["webauthn_reg_handled"] = True
                del st.session_state["webauthn_reg_options"]
                st.success("Device registered for Face ID / Touch ID.")
                st.rerun()
            except Exception:
                st.error("Registration couldn't be verified -- please try again.")
        elif result and result.get("status") == "error":
            st.warning("Registration didn't complete -- please try again.")

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
