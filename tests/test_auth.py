from core.auth import generate_salt, hash_password, verify_password


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


class TestGenerateSalt:
    def test_returns_a_string(self):
        assert isinstance(generate_salt(), str)

    def test_is_32_hex_characters(self):
        salt = generate_salt()
        assert len(salt) == 32
        int(salt, 16)  # raises ValueError if not valid hex

    def test_two_calls_are_different(self):
        assert generate_salt() != generate_salt()
