from core.auth import hash_password, verify_password


class TestVerifyPassword:
    def test_correct_password_verifies(self):
        salt = "somesalt"
        stored_hash = hash_password("admin", salt)
        assert verify_password("admin", salt, stored_hash) is True

    def test_wrong_password_fails(self):
        salt = "somesalt"
        stored_hash = hash_password("admin", salt)
        assert verify_password("wrong", salt, stored_hash) is False

    def test_wrong_salt_fails(self):
        stored_hash = hash_password("admin", "salt-a")
        assert verify_password("admin", "salt-b", stored_hash) is False

    def test_empty_password_fails_against_real_password(self):
        salt = "somesalt"
        stored_hash = hash_password("admin", salt)
        assert verify_password("", salt, stored_hash) is False

    def test_uses_constant_time_comparison(self, monkeypatch):
        # hmac.compare_digest, not `==` -- guards against timing attacks.
        import hmac

        from core import auth

        calls = []
        original = hmac.compare_digest
        monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: (calls.append((a, b)), original(a, b))[1])
        verify_password("admin", "somesalt", hash_password("admin", "somesalt"))
        assert len(calls) == 1
