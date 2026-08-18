"""Throwaway Step-2 verification page -- NOT part of the app's real navigation, deleted
once real-device testing confirms both components round-trip correctly. Run directly:
    streamlit run _webauthn_debug.py
Lets the two new WebAuthn components be exercised in isolation, before any registration/
login UI gets wired into dashboard_app.py, per the v4.6 plan's Step 2."""

import streamlit as st

from app_pages.components.webauthn_login_component import webauthn_authenticate
from app_pages.components.webauthn_register_component import webauthn_register
from core.webauthn_auth import build_authentication_options, build_registration_options

st.title("WebAuthn component debug (throwaway)")

rp_id = st.text_input("RP ID", value="localhost")

st.subheader("Registration")
if st.button("Generate registration options"):
    options_json, challenge = build_registration_options(rp_id, "Portfolio Tracker Debug", [])
    st.session_state["debug_reg_options"] = options_json
    st.session_state["debug_reg_challenge"] = challenge
if "debug_reg_options" in st.session_state:
    st.code(st.session_state["debug_reg_options"], language="json")
    result = webauthn_register(st.session_state["debug_reg_options"], key="debug_register")
    st.write("Component result:", result)

st.subheader("Authentication")
if st.button("Generate authentication options"):
    options_json, challenge = build_authentication_options(rp_id, [])
    st.session_state["debug_auth_options"] = options_json
    st.session_state["debug_auth_challenge"] = challenge
if "debug_auth_options" in st.session_state:
    st.code(st.session_state["debug_auth_options"], language="json")
    result = webauthn_authenticate(st.session_state["debug_auth_options"], key="debug_authenticate")
    st.write("Component result:", result)
