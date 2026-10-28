"""Persistent storage for plugin settings and the EpidBot API key.

Non-secret settings live in ``QSettings`` (org "EpidBot", app
"EpidBotQGISPlugin"). The API key is stored in the system keyring via the
optional ``keyring`` package when available, with a documented fallback to
``QSettings`` so the plugin still works on minimal installations.

The Qt/keyring imports are lazy and the backend/keyring module can be
injected, so this module is unit-testable without QGIS.
"""

import importlib.util
from contextlib import suppress
from typing import Any, Dict, Optional

from .api_client import DEFAULT_BASE_URL

QSETTINGS_ORG = "EpidBot"
QSETTINGS_APP = "EpidBotQGISPlugin"
KEYRING_SERVICE = "EpidBot QGIS Plugin"

# Plain (non-secret) settings with defaults. The default server URL is the
# production EpidBot API host; users only need to paste their API key.
_PLAIN_KEYS = {
    "base_url": DEFAULT_BASE_URL,
    "locale": "en",
    "poll_interval": 1.5,
    "include_map_context": True,
    "inline_budget_kb": 64.0,
    "artifact_uploads": False,
}

_SECRET_KEYS = ("api_key",)


def _default_backend():
    """Create the default QSettings-backed backend (lazy Qt import)."""
    from qgis.PyQt.QtCore import QSettings

    return QSettings(QSETTINGS_ORG, QSETTINGS_APP)


def _load_keyring():
    """Return the ``keyring`` module when installed, else None."""
    try:
        if importlib.util.find_spec("keyring") is None:
            return None
        import keyring
        return keyring
    except Exception:
        # Broken keyring installs must never take the plugin down.
        return None


class AuthStore:
    """Stores plugin configuration and the EpidBot API key."""

    def __init__(self, backend: Optional[Any] = None,
                 keyring_module: Optional[Any] = None):
        """Create a store.

        :param backend: object exposing the ``QSettings`` subset used here
            (``value``, ``setValue``, ``remove``, ``sync``). Defaults to
            ``QSettings``.
        :param keyring_module: the ``keyring`` module to use for the secret,
            or None to auto-detect. The sentinel string ``"disabled"``
            forces the QSettings fallback (used by tests).
        """
        self._backend = backend if backend is not None else _default_backend()
        self._keyring = (keyring_module if keyring_module != "disabled"
                         else None) if keyring_module is not None or \
            keyring_module == "disabled" else _load_keyring()
        self.keyring_fallback_used = False
        self._cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Plain settings
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        """Expose plain settings as attributes with typed defaults."""
        if name in _PLAIN_KEYS:
            return self.get_plain(name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PLAIN_KEYS:
            # Only reached after normal __init__ has run (instance dict has
            # _cache by then); keeps attribute-style assignment working.
            if "_cache" in self.__dict__:
                self._cache.pop(name, None)
                self.set_plain(name, value)
                return
        object.__setattr__(self, name, value)

    def get_plain(self, name: str) -> Any:
        """Read a plain setting with in-memory caching."""
        if name in self._cache:
            return self._cache[name]
        default = _PLAIN_KEYS[name]
        value = self._backend.value(name, default)
        # QSettings returns 'true/false' strings for bools on some platforms.
        if isinstance(default, bool) and isinstance(value, str):
            value = value.strip().lower() in ("true", "1", "yes")
        if isinstance(default, float):
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = default
        self._cache[name] = value
        return value

    def set_plain(self, name: str, value: Any) -> None:
        """Persist a plain setting."""
        self._cache[name] = value
        self._backend.setValue(name, value)
        self._backend.sync()

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------
    def _store_secret(self, key: str, value: Optional[str]) -> None:
        if value is None or value == "":
            self._backend.remove("secrets/{}".format(key))
            if self._keyring is not None:
                # Nothing stored (or backend unavailable) - fine.
                with suppress(Exception):
                    self._keyring.delete_password(KEYRING_SERVICE, key)
            self._backend.sync()
            return
        if self._keyring is not None:
            try:
                self._keyring.set_password(KEYRING_SERVICE, key, value)
                self._backend.remove("secrets/{}".format(key))
                self._backend.sync()
                return
            except Exception:
                self.keyring_fallback_used = True
        # Fallback: keep the secret in QSettings (documented trade-off).
        self._backend.setValue("secrets/{}".format(key), value)
        self._backend.sync()

    def _read_secret(self, key: str) -> Optional[str]:
        if self._keyring is not None:
            try:
                value = self._keyring.get_password(KEYRING_SERVICE, key)
                if value:
                    return value
            except Exception:
                self.keyring_fallback_used = True
        value = self._backend.value("secrets/{}".format(key), None)
        return value if isinstance(value, str) and value else None

    def get_api_key(self) -> Optional[str]:
        """Return the stored EpidBot API key, if any."""
        return self._read_secret("api_key")

    def set_api_key(self, key: Optional[str]) -> None:
        """Store or clear the EpidBot API key."""
        self._store_secret("api_key", key)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def clear_all(self) -> None:
        """Reset every setting and secret."""
        self.set_api_key(None)
        for key in _PLAIN_KEYS:
            self._backend.remove(key)
        self._cache.clear()
        self._backend.sync()

    def has_credentials(self) -> bool:
        """True when an API key is available."""
        return bool(self.get_api_key())
