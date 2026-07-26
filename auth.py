"""Password gate helpers. Pure logic, no Streamlit import -- see tests/test_auth.py."""

import hashlib
import hmac


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), stored_hash)
