"""Tests for :mod:`core.auth_store` (settings + secret persistence)."""

import pytest

from core.auth_store import AuthStore


class FakeBackend:
    """Dict-based QSettings double."""

    def __init__(self):
        self.data = {}
        self.removed = []

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.removed.append(key)
        self.data.pop(key, None)

    def sync(self):
        pass


class FakeKeyring:
    """In-memory keyring double; can be told to fail."""

    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    def set_password(self, service, key, value):
        if self.fail:
            raise RuntimeError("keyring broken")
        self.store[key] = value

    def get_password(self, service, key):
        return self.store.get(key)

    def delete_password(self, service, key):
        self.store.pop(key, None)


class TestPlainSettings:
    """Typed access and round-trips for non-secret settings."""

    def test_defaults(self):
        store = AuthStore(backend=FakeBackend(), keyring_module="disabled")
        assert store.base_url == ""
        assert store.auth_mode == "password"
        assert store.locale == "en"
        assert store.poll_interval == 1.5
        assert store.include_map_context is True

    def test_attribute_assignment_persists(self):
        backend = FakeBackend()
        store = AuthStore(backend=backend, keyring_module="disabled")
        store.base_url = "https://epid.example"
        store.include_map_context = False
        store.poll_interval = 2.5
        assert backend.data["base_url"] == "https://epid.example"
        # Re-read from a fresh store over the same backend.
        store2 = AuthStore(backend=backend, keyring_module="disabled")
        assert store2.base_url == "https://epid.example"
        assert store2.include_map_context is False
        assert store2.poll_interval == 2.5

    def test_qsettings_boolean_string_coercion(self):
        """QSettings may hand back 'true'/'false' strings for bools."""
        backend = FakeBackend()
        backend.data["include_map_context"] = "false"
        store = AuthStore(backend=backend, keyring_module="disabled")
        assert store.include_map_context is False


class TestSecrets:
    """Secrets go to the keyring when available, QSettings otherwise."""

    def test_secrets_stored_in_keyring_when_available(self):
        backend = FakeBackend()
        keyring = FakeKeyring()
        store = AuthStore(backend=backend, keyring_module=keyring)
        store.set_access_token("acc")
        store.set_refresh_token("ref")
        assert keyring.store == {"access_token": "acc", "refresh_token": "ref"}
        # No plaintext copies in QSettings.
        assert "secrets/access_token" not in backend.data
        assert store.get_access_token() == "acc"

    def test_secrets_fall_back_to_backend_when_keyring_broken(self):
        backend = FakeBackend()
        store = AuthStore(backend=backend,
                          keyring_module=FakeKeyring(fail=True))
        store.set_api_key("ek_x")
        assert backend.data["secrets/api_key"] == "ek_x"
        assert store.keyring_fallback_used is True
        assert store.get_api_key() == "ek_x"

    def test_clear_tokens_forgets_secrets(self):
        keyring = FakeKeyring()
        store = AuthStore(backend=FakeBackend(), keyring_module=keyring)
        store.set_access_token("acc")
        store.set_refresh_token("ref")
        store.set_api_key("ek_keep")
        store.clear_tokens()
        assert store.get_access_token() is None
        assert store.get_refresh_token() is None
        assert store.get_api_key() == "ek_keep"

    def test_has_credentials_respects_mode(self):
        store = AuthStore(backend=FakeBackend(), keyring_module="disabled")
        assert store.has_credentials() is False
        store.set_plain("auth_mode", "api_key")
        store.set_api_key("ek_1")
        assert store.has_credentials() is True
        store.set_plain("auth_mode", "password")
        store.set_refresh_token("ref")
        assert store.has_credentials() is True

    def test_unknown_attribute_raises(self):
        store = AuthStore(backend=FakeBackend(), keyring_module="disabled")
        with pytest.raises(AttributeError):
            _ = store.no_such_setting
