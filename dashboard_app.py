import streamlit as st

from core.auth import verify_password
from core.db import init_db
from core.version import current_app_version

st.set_page_config(page_title="Financial Summary Dashboard", layout="wide")

if not st.session_state.get("authenticated"):
    st.title("Financial Summary Dashboard")
    pw = st.text_input("Password", type="password")
    if st.button("Log in"):
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
    ],
})
pg.run()

# Placed after pg.run() deliberately -- Streamlit renders the nav's page list as part of
# pg.run() executing, so anything appended to the sidebar after that call lands below it,
# at the bottom of the left menu.
st.sidebar.caption(f"Version {current_app_version()}")
