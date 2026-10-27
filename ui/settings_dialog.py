"""Settings dialog: server URL, API key, locale, polling."""

from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QVBoxLayout)

from ..core import api_client
from ..core.api_client import DEFAULT_BASE_URL


class SettingsDialog(QDialog):
    """Configure the EpidBot connection and persist it in the AuthStore."""

    def __init__(self, auth_store, client: api_client.EpidBotApiClient,
                 parent=None):
        """Load current values from ``auth_store`` into the form."""
        super().__init__(parent)
        self.setWindowTitle(self.tr("EpidBot Settings"))
        self.auth_store = auth_store
        self.client = client
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(DEFAULT_BASE_URL)
        form.addRow(self.tr("Server URL"), self.url_edit)

        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(self.tr("ek_... / mk_..."))
        form.addRow(self.tr("API key"), self.api_key_edit)

        self.locale_combo = QComboBox(self)
        self.locale_combo.addItem("English", "en")
        self.locale_combo.addItem("Português (Brasil)", "pt-BR")
        form.addRow(self.tr("Answer language"), self.locale_combo)

        self.poll_spin = QDoubleSpinBox(self)
        self.poll_spin.setRange(0.5, 10.0)
        self.poll_spin.setSingleStep(0.5)
        self.poll_spin.setSuffix(" s")
        form.addRow(self.tr("Status poll interval"), self.poll_spin)

        connection_box = QGroupBox(self.tr("Connection test"), self)
        connection_layout = QHBoxLayout(connection_box)
        self.test_button = QPushButton(self.tr("Test connection"), connection_box)
        self.test_button.clicked.connect(self._on_test)
        connection_layout.addWidget(self.test_button)
        self.test_label = QLabel("", connection_box)
        connection_layout.addWidget(self.test_label, 1)
        layout.addWidget(connection_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self):
        self.url_edit.setText(str(self.auth_store.get_plain("base_url") or ""))
        stored_key = self.auth_store.get_api_key() or ""
        self.api_key_edit.setText(stored_key)
        locale_index = self.locale_combo.findData(
            str(self.auth_store.get_plain("locale")))
        self.locale_combo.setCurrentIndex(locale_index if locale_index >= 0 else 0)
        self.poll_spin.setValue(float(self.auth_store.get_plain("poll_interval")))

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------
    def _on_test(self):
        """Try GET /auth/me with the values currently in the form."""
        self.test_button.setEnabled(False)
        self.test_label.setText(self.tr("Testing..."))
        probe = api_client.EpidBotApiClient(
            base_url=self.url_edit.text() or DEFAULT_BASE_URL,
            poll_interval=0.5, request_timeout=15.0)

        class _TempStore:
            """Minimal AuthStore stand-in for the probe client."""

            @staticmethod
            def get_api_key():
                key = self.api_key_edit.text().strip()
                if key:
                    return key
                return self.auth_store.get_api_key()

        probe.auth_store = _TempStore
        try:
            user = probe.test_connection()
            self.test_label.setText(self.tr("Connected as {}").format(
                user.get("username", "?")))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.test_label.setText(self.tr("Failed: {}").format(exc))
        finally:
            self.test_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def apply(self):
        """Write the form values into the AuthStore and reconfigure the
        live client."""
        self.auth_store.set_plain(
            "base_url", self.url_edit.text().strip() or DEFAULT_BASE_URL)
        self.auth_store.set_plain("locale",
                                  str(self.locale_combo.currentData()))
        self.auth_store.set_plain("poll_interval", float(self.poll_spin.value()))
        key = self.api_key_edit.text().strip()
        if key:
            self.auth_store.set_api_key(key)

        self.client.configure(str(self.auth_store.get_plain("base_url")))
        self.client.poll_interval = float(
            self.auth_store.get_plain("poll_interval"))
        return True
