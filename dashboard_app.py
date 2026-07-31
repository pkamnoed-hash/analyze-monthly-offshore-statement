import os

import streamlit as st

from core.auth import verify_password
from core.db import init_db
from core.version import current_app_version

st.set_page_config(page_title="Financial Summary Dashboard", layout="wide")

# core/db.py deliberately has no Streamlit import (see CLAUDE.md), so it reads its
# Turso connection details from the environment instead of st.secrets directly --
# bridge them here, before init_db() or any other core.db call happens below.
os.environ.setdefault("TURSO_DATABASE_URL", st.secrets["TURSO_DATABASE_URL"])
os.environ.setdefault("TURSO_AUTH_TOKEN", st.secrets["TURSO_AUTH_TOKEN"])

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

init_db()

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
_app_env = st.secrets.get("APP_ENV", "prod")
if _app_env == "dev":
    st.sidebar.caption("🟡 Environment: DEV")
else:
    st.sidebar.caption("🟢 Environment: PROD")
