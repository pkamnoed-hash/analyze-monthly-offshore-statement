"""verify_registration()/verify_authentication() are tested against REAL WebAuthn
response fixtures pulled from py_webauthn's own test suite (github.com/duo-labs/
py_webauthn/tree/master/tests) -- genuine captured browser/authenticator output with
matching challenge/origin/rp_id/public_key, not hand-faked JSON. This exercises the
actual cryptographic verification pipeline (signature checks, client data hash,
attestation parsing), which nothing invented in this repo could substitute for.

What these tests can't cover: a Face ID/Touch ID ceremony against THIS app's real
`WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGIN` on a real iPad/iPhone -- that's Step 2+'s manual
verification, unavoidable since it needs a real browser and a real platform
authenticator. These tests only prove the Python-side plumbing (option building,
response verification, the UV-required enforcement this wrapper adds on top of the
library's own default) is correct in isolation.
"""
import json

import pytest

from core.webauthn_auth import (
    build_authentication_options,
    build_registration_options,
    verify_authentication,
    verify_registration,
)

# Real "none"-attestation registration response, github.com/duo-labs/py_webauthn's own
# tests/test_verify_registration_response.py -- confirmed (via a standalone check
# against the installed `webauthn` package) to have its User Verification flag SET,
# so it succeeds even with this wrapper's require_user_verification=True.
_REG_CREDENTIAL = {
    "id": "9y1xA8Tmg1FEmT-c7_fvWZ_uoTuoih3OvR45_oAK-cwHWhAbXrl2q62iLVTjiyEZ7O7n-CROOY494k7Q3xrs_w",
    "rawId": "9y1xA8Tmg1FEmT-c7_fvWZ_uoTuoih3OvR45_oAK-cwHWhAbXrl2q62iLVTjiyEZ7O7n-CROOY494k7Q3xrs_w",
    "response": {
        "attestationObject": "o2NmbXRkbm9uZWdhdHRTdG10oGhhdXRoRGF0YVjESZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2NFAAAAFwAAAAAAAAAAAAAAAAAAAAAAQPctcQPE5oNRRJk_nO_371mf7qE7qIodzr0eOf6ACvnMB1oQG165dqutoi1U44shGezu5_gkTjmOPeJO0N8a7P-lAQIDJiABIVggSFbUJF-42Ug3pdM8rDRFu_N5oiVEysPDB6n66r_7dZAiWCDUVnB39FlGypL-qAoIO9xWHtJygo2jfDmHl-_eKFRLDA",
        "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIiwiY2hhbGxlbmdlIjoiVHdON240V1R5R0tMYzRaWS1xR3NGcUtuSE00bmdscXN5VjBJQ0psTjJUTzlYaVJ5RnRya2FEd1V2c3FsLWdrTEpYUDZmbkYxTWxyWjUzTW00UjdDdnciLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9",
    },
    "type": "public-key",
    "clientExtensionResults": {},
    "transports": ["nfc", "usb"],
}
_REG_CHALLENGE = "TwN7n4WTyGKLc4ZY-qGsFqKnHM4nglqsyV0ICJlN2TO9XiRyFtrkaDwUvsql-gkLJXP6fnF1MlrZ53Mm4R7Cvw"
_REG_ORIGIN = "http://localhost:5000"
_REG_RP_ID = "localhost"

# Real EC2 authentication response, same test suite's
# test_verify_authentication_response.py -- confirmed (via a standalone check) to have
# its User Verification flag NOT set, used below to exercise this wrapper's own
# require_user_verification=True rejecting an otherwise-valid response.
_AUTH_CREDENTIAL_NO_UV = {
    "id": "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q",
    "rawId": "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q",
    "response": {
        "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2MBAAAATg",
        "clientDataJSON": "eyJjaGFsbGVuZ2UiOiJ4aTMwR1BHQUZZUnhWRHBZMXNNMTBEYUx6VlFHNjZudi1fN1JVYXpIMHZJMll2RzhMWWdERW52TjVmWlpOVnV2RUR1TWk5dGUzVkxxYjQyTjBma0xHQSIsImNsaWVudEV4dGVuc2lvbnMiOnt9LCJoYXNoQWxnb3JpdGhtIjoiU0hBLTI1NiIsIm9yaWdpbiI6Imh0dHA6Ly9sb2NhbGhvc3Q6NTAwMCIsInR5cGUiOiJ3ZWJhdXRobi5nZXQifQ",
        "signature": "MEUCIGisVZOBapCWbnJJvjelIzwpixxIwkjCCb5aCHafQu68AiEA88v-2pJNNApPFwAKFiNuf82-2hBxYW5kGwVweeoxCwo",
    },
    "type": "public-key",
    "clientExtensionResults": {},
}
_AUTH_CHALLENGE_NO_UV = "xi30GPGAFYRxVDpY1sM10DaLzVQG66nv-_7RUazH0vI2YvG8LYgDEnvN5fZZNVuvEDuMi9te3VLqb42N0fkLGA"
_AUTH_PUBLIC_KEY_NO_UV = "pQECAyYgASFYIIeDTe-gN8A-zQclHoRnGFWN8ehM1b7yAsa8I8KIvmplIlgg4nFGT5px8o6gpPZZhO01wdy9crDSA_Ngtkx0vGpvPHI"

# Real RSA-public-key authentication response, same test suite's
# test_verify_authentication_response.py::test_verify_authentication_response_with_RSA_public_key
# -- also confirmed to have its User Verification flag set.
_AUTH_CREDENTIAL = {
    "id": "ZoIKP1JQvKdrYj1bTUPJ2eTUsbLeFkv-X5xJQNr4k6s",
    "rawId": "ZoIKP1JQvKdrYj1bTUPJ2eTUsbLeFkv-X5xJQNr4k6s",
    "response": {
        "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2MFAAAAAQ",
        "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uZ2V0IiwiY2hhbGxlbmdlIjoiaVBtQWkxUHAxWEw2b0FncTNQV1p0WlBuWmExekZVRG9HYmFRMF9LdlZHMWxGMnMzUnRfM280dVN6Y2N5MHRtY1RJcFRUVDRCVTFULUk0bWFhdm5kalEiLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9",
        "signature": "iOHKX3erU5_OYP_r_9HLZ-CexCE4bQRrxM8WmuoKTDdhAnZSeTP0sjECjvjfeS8MJzN1ArmvV0H0C3yy_FdRFfcpUPZzdZ7bBcmPh1XPdxRwY747OrIzcTLTFQUPdn1U-izCZtP_78VGw9pCpdMsv4CUzZdJbEcRtQuRS03qUjqDaovoJhOqEBmxJn9Wu8tBi_Qx7A33RbYjlfyLm_EDqimzDZhyietyop6XUcpKarKqVH0M6mMrM5zTjp8xf3W7odFCadXEJg-ERZqFM0-9Uup6kJNLbr6C5J4NDYmSm3HCSA6lp2iEiMPKU8Ii7QZ61kybXLxsX4w4Dm3fOLjmDw",
        "userHandle": "T1RWa1l6VXdPRFV0WW1NNVlTMDBOVEkxTFRnd056Z3RabVZpWVdZNFpEVm1ZMk5p",
    },
    "type": "public-key",
    "clientExtensionResults": {},
}
_AUTH_CHALLENGE = "iPmAi1Pp1XL6oAgq3PWZtZPnZa1zFUDoGbaQ0_KvVG1lF2s3Rt_3o4uSzccy0tmcTIpTTT4BU1T-I4maavndjQ"
_AUTH_ORIGIN = "http://localhost:5000"
_AUTH_RP_ID = "localhost"
_AUTH_PUBLIC_KEY = "pAEDAzkBACBZAQDfV20epzvQP-HtcdDpX-cGzdOxy73WQEvsU7Dnr9UWJophEfpngouvgnRLXaEUn_d8HGkp_HIx8rrpkx4BVs6X_B6ZjhLlezjIdJbLbVeb92BaEsmNn1HW2N9Xj2QM8cH-yx28_vCjf82ahQ9gyAr552Bn96G22n8jqFRQKdVpO-f-bvpvaP3IQ9F5LCX7CUaxptgbog1SFO6FI6ob5SlVVB00lVXsaYg8cIDZxCkkENkGiFPgwEaZ7995SCbiyCpUJbMqToLMgojPkAhWeyktu7TlK6UBWdJMHc3FPAIs0lH_2_2hKS-mGI1uZAFVAfW1X-mzKL0czUm2P1UlUox7IUMBAAE"


def _decode_challenge(b64url: str) -> bytes:
    from webauthn import base64url_to_bytes
    return base64url_to_bytes(b64url)


class TestBuildRegistrationOptions:
    def test_targets_the_given_rp(self):
        options_json, _ = build_registration_options("localhost", "Portfolio Tracker", [])
        parsed = json.loads(options_json)
        assert parsed["rp"] == {"name": "Portfolio Tracker", "id": "localhost"}

    def test_restricts_to_platform_authenticator_with_required_verification(self):
        # This is what actually enforces "Face ID/Touch ID, not a USB security key" and
        # "an actual biometric check, not just device-present" -- the whole point of a
        # BIOMETRIC login feature, so it's worth locking in explicitly.
        options_json, _ = build_registration_options("localhost", "Portfolio Tracker", [])
        selection = json.loads(options_json)["authenticatorSelection"]
        assert selection["authenticatorAttachment"] == "platform"
        assert selection["userVerification"] == "required"

    def test_excludes_already_registered_devices(self):
        options_json, _ = build_registration_options("localhost", "Portfolio Tracker", ["AQID", "BAUG"])
        excluded_ids = {c["id"] for c in json.loads(options_json)["excludeCredentials"]}
        assert excluded_ids == {"AQID", "BAUG"}

    def test_no_existing_credentials_means_empty_exclude_list(self):
        options_json, _ = build_registration_options("localhost", "Portfolio Tracker", [])
        assert json.loads(options_json)["excludeCredentials"] == []

    def test_challenge_is_real_random_bytes(self):
        _, challenge_1 = build_registration_options("localhost", "Portfolio Tracker", [])
        _, challenge_2 = build_registration_options("localhost", "Portfolio Tracker", [])
        assert isinstance(challenge_1, bytes)
        assert len(challenge_1) >= 32  # WebAuthn's own minimum recommended challenge size
        assert challenge_1 != challenge_2  # never reused across calls


class TestBuildAuthenticationOptions:
    def test_targets_the_given_rp_and_requires_verification(self):
        options_json, _ = build_authentication_options("localhost", ["AQID"])
        parsed = json.loads(options_json)
        assert parsed["rpId"] == "localhost"
        assert parsed["userVerification"] == "required"

    def test_allow_credentials_matches_input(self):
        options_json, _ = build_authentication_options("localhost", ["AQID", "BAUG"])
        allowed_ids = {c["id"] for c in json.loads(options_json)["allowCredentials"]}
        assert allowed_ids == {"AQID", "BAUG"}


class TestVerifyRegistration:
    def test_real_response_with_user_verification_succeeds(self):
        credential_id, public_key = verify_registration(
            json.dumps(_REG_CREDENTIAL), _decode_challenge(_REG_CHALLENGE), _REG_ORIGIN, _REG_RP_ID,
        )
        assert credential_id  # base64url text, non-empty
        assert public_key
        assert isinstance(credential_id, str)
        assert isinstance(public_key, str)

    def test_wrong_challenge_is_rejected(self):
        # Same real response, but the challenge doesn't match what was actually
        # signed over -- exactly what stops a replayed/stale registration attempt.
        with pytest.raises(Exception):
            verify_registration(json.dumps(_REG_CREDENTIAL), b"not-the-real-challenge-bytes!!!!", _REG_ORIGIN, _REG_RP_ID)

    def test_wrong_origin_is_rejected(self):
        with pytest.raises(Exception):
            verify_registration(
                json.dumps(_REG_CREDENTIAL), _decode_challenge(_REG_CHALLENGE),
                "https://attacker.example", _REG_RP_ID,
            )


class TestVerifyAuthentication:
    def test_real_response_with_user_verification_succeeds(self):
        new_sign_count = verify_authentication(
            json.dumps(_AUTH_CREDENTIAL), _decode_challenge(_AUTH_CHALLENGE), _AUTH_ORIGIN, _AUTH_RP_ID,
            _AUTH_PUBLIC_KEY, stored_sign_count=0,
        )
        assert new_sign_count == 1

    def test_response_without_user_verification_is_rejected(self):
        # This fixture is otherwise perfectly valid (right challenge, right signature,
        # right public key) -- only its UV flag is unset, exercising this wrapper's own
        # require_user_verification=True actually doing something, on a real response.
        with pytest.raises(Exception):
            verify_authentication(
                json.dumps(_AUTH_CREDENTIAL_NO_UV), _decode_challenge(_AUTH_CHALLENGE_NO_UV),
                _AUTH_ORIGIN, _AUTH_RP_ID, _AUTH_PUBLIC_KEY_NO_UV, stored_sign_count=77,
            )

    def test_stale_sign_count_is_rejected(self):
        # WebAuthn's replay-attack defense: the stored sign count must be lower than
        # what the authenticator reports, or a cloned/replayed assertion is assumed.
        # This real response's own counter corresponds to new_sign_count=1 -- asserting
        # a stored count that's already >= that should be rejected.
        with pytest.raises(Exception):
            verify_authentication(
                json.dumps(_AUTH_CREDENTIAL), _decode_challenge(_AUTH_CHALLENGE), _AUTH_ORIGIN, _AUTH_RP_ID,
                _AUTH_PUBLIC_KEY, stored_sign_count=1,
            )
