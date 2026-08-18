"""Throwaway Step-2 verification page -- NOT part of the app's real navigation, deleted
once real-device testing confirms the full round trip. Run directly:
    streamlit run _webauthn_debug.py
Exercises the whole chain a real device needs: options generated -> browser ceremony
(via the two new custom components) -> core.webauthn_auth's actual crypto verification
-> a real save/update against core/db.py's webauthn_credentials table. Not just "the
browser produced a plausible-looking JSON blob" -- this is the same verify+persist logic
Step 3/4 wires into dashboard_app.py for real, just driven from a standalone page first."""

import os

import streamlit as st

from app_pages.components.webauthn_login_component import webauthn_authenticate
from app_pages.components.webauthn_register_component import webauthn_register
from core import db
from core.webauthn_auth import (
    build_authentication_options,
    build_registration_options,
    verify_authentication,
    verify_registration,
)

os.environ.setdefault("TURSO_DATABASE_URL", st.secrets["TURSO_DATABASE_URL"])
os.environ.setdefault("TURSO_AUTH_TOKEN", st.secrets["TURSO_AUTH_TOKEN"])
if "debug_db_initialized" not in st.session_state:
    db.init_db()
    st.session_state["debug_db_initialized"] = True

st.title("WebAuthn full round-trip debug (throwaway)")

rp_id = st.text_input("RP ID", value="localhost")
origin = st.text_input("Origin", value=f"https://{rp_id}" if rp_id != "localhost" else "http://localhost:8504")
device_label = st.text_input("Device label", value="iPad")

st.divider()
st.subheader("Currently registered credentials (real DB rows)")
existing = db.fetch_webauthn_credentials()
st.dataframe(existing[["Credential Id", "Device Label", "Sign Count", "Created At"]] if not existing.empty else existing)

st.divider()
st.subheader("Registration")
if st.button("Generate registration options"):
    existing_ids = existing["Credential Id"].tolist() if not existing.empty else []
    options_json, challenge = build_registration_options(rp_id, "Portfolio Tracker Debug", existing_ids)
    st.session_state["debug_reg_options"] = options_json
    st.session_state["debug_reg_challenge"] = challenge
    st.session_state.pop("debug_reg_verified", None)

if "debug_reg_options" in st.session_state:
    result = webauthn_register(st.session_state["debug_reg_options"], key="debug_register")
    if result and result.get("status") == "success" and not st.session_state.get("debug_reg_verified"):
        try:
            credential_id, public_key = verify_registration(
                result["credential_json"], st.session_state["debug_reg_challenge"], origin, rp_id,
            )
            db.save_webauthn_credential(credential_id, public_key, device_label=device_label)
            st.session_state["debug_reg_verified"] = True
            st.success(f"Server-side verification passed AND saved to DB. credential_id={credential_id}")
            st.rerun()
        except Exception as e:
            st.error(f"Server-side verification FAILED: {type(e).__name__}: {e}")
    elif result and result.get("status") == "error":
        st.warning(f"Browser ceremony failed: {result.get('message')}")

st.divider()
st.subheader("Authentication")
if st.button("Generate authentication options"):
    allowed_ids = existing["Credential Id"].tolist() if not existing.empty else []
    options_json, challenge = build_authentication_options(rp_id, allowed_ids)
    st.session_state["debug_auth_options"] = options_json
    st.session_state["debug_auth_challenge"] = challenge

if "debug_auth_options" in st.session_state:
    result = webauthn_authenticate(st.session_state["debug_auth_options"], key="debug_authenticate")
    if result and result.get("status") == "success":
        cred_id = result["credential_json"]["id"]
        matching = existing[existing["Credential Id"] == cred_id]
        if matching.empty:
            st.error(f"No stored credential matches id {cred_id} -- was it registered against this same RP ID?")
        else:
            row = matching.iloc[0]
            try:
                new_sign_count = verify_authentication(
                    result["credential_json"], st.session_state["debug_auth_challenge"], origin, rp_id,
                    row["Public Key"], int(row["Sign Count"]),
                )
                db.update_webauthn_sign_count(cred_id, new_sign_count)
                st.success(f"Server-side verification passed. sign_count {row['Sign Count']} -> {new_sign_count}")
            except Exception as e:
                st.error(f"Server-side verification FAILED: {type(e).__name__}: {e}")
    elif result and result.get("status") == "error":
        st.warning(f"Browser ceremony failed: {result.get('message')}")
