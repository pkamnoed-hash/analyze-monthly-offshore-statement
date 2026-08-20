"""Password gate helpers. Pure logic, no Streamlit import -- see tests/test_auth.py."""

import hashlib
import hmac
import secrets


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), stored_hash)


def generate_salt() -> str:
    """v4.7 -- a fresh random salt for Change Password. 32 hex chars (128 bits),
    matching the original hand-generated salt's format; nothing about hash_password
    requires hex specifically, it's just a convenient TEXT-column encoding."""
    return secrets.token_hex(16)
