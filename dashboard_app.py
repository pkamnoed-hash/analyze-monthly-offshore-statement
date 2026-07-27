import streamlit as st

from core.auth import verify_password
from core.db import init_db

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
    "Overview": [st.Page("app_pages/dashboard.py", title="Dashboard", default=True)],
    "Input": [
        st.Page("app_pages/record_trade.py", title="Record Trade"),
        st.Page("app_pages/record_dividend.py", title="Record Dividend"),
    ],
    "Tools": [st.Page("app_pages/reconciliation.py", title="Reconciliation")],
})
pg.run()
