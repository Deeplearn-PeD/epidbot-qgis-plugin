"""Dockable chat panel: sessions, message list, map-context toggle."""

import os
import re
import tempfile
from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QPlainTextEdit, QPushButton, QScrollArea,
                             QToolButton, QVBoxLayout, QWidget)

from ..core import map_context_qgis
from ..core.api_client import EpidBotApiClient
from ..core.chat_worker import ChatWorker
from .async_call import AsyncCall
from .message_widget import MessageWidget

_PLOT_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")

_PLOTS_DIR = os.path.join(tempfile.gettempdir(), "epidbot-qgis", "plots")


def plots_dir() -> str:
    """Local cache directory for downloaded plots."""
    os.makedirs(_PLOTS_DIR, exist_ok=True)
    return _PLOTS_DIR


class ChatDock(QWidget):
    """Full-height chat panel hosted in a QGIS dock widget.

    Owns the client/session state and the background workers; the plugin
    object only creates it and forwards actions (e.g. "add to layout").
    """

    settingsRequested = pyqtSignal()

    def __init__(self, iface, client: EpidBotApiClient, auth_store,
                 parent=None):
        """Build the panel.

        :param iface: ``QgisInterface`` (for map-context collection and
            layout insertion).
        :param client: configured :class:`EpidBotApiClient`.
        :param auth_store: :class:`core.auth_store.AuthStore`.
        """
        super().__init__(parent)
        self.iface = iface
        self.client = client
        self.auth_store = auth_store
        self.session_id: Optional[int] = None
        self._worker: Optional[ChatWorker] = None
        self._pending_downloads: List[AsyncCall] = []
        self._history_call: Optional[AsyncCall] = None
        self._sessions_call: Optional[AsyncCall] = None
        self._build_ui()
        self.refresh_sessions()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Top bar: sessions + settings
        top = QHBoxLayout()
        self.session_combo = QComboBox(self)
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        top.addWidget(QLabel(self.tr("Session:"), self), 0)
        top.addWidget(self.session_combo, 1)
        refresh_button = QToolButton(self)
        refresh_button.setText("\U0001F504")
        refresh_button.setToolTip(self.tr("Refresh session list"))
        refresh_button.clicked.connect(self.refresh_sessions)
        top.addWidget(refresh_button)
        new_button = QToolButton(self)
        new_button.setText("\u2795")
        new_button.setToolTip(self.tr("Start a new session"))
        new_button.clicked.connect(self.start_new_session)
        top.addWidget(new_button)
        settings_button = QToolButton(self)
        settings_button.setText("\u2699")
        settings_button.setToolTip(self.tr("EpidBot settings"))
        settings_button.clicked.connect(self.settingsRequested.emit)
        top.addWidget(settings_button)
        layout.addLayout(top)

        # Message list
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.messages_host = QWidget(self.scroll_area)
        self.messages_layout = QVBoxLayout(self.messages_host)
        self.messages_layout.setAlignment(Qt.AlignTop)
        self.messages_layout.setSpacing(2)
        self.scroll_area.setWidget(self.messages_host)
        layout.addWidget(self.scroll_area, 1)

        # Context toggle
        self.context_check = QCheckBox(
            self.tr("Include map context (layers, extent, CRS)"), self)
        self.context_check.setChecked(
            bool(self.auth_store.get_plain("include_map_context")))
        self.context_check.toggled.connect(self._on_context_toggled)
        layout.addWidget(self.context_check)

        # Status line
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Input area
        self.input_edit = QPlainTextEdit(self)
        self.input_edit.setPlaceholderText(
            self.tr("Ask EpidBot... (Enter to send, Shift+Enter for a new "
                    "line)"))
        self.input_edit.setFixedHeight(72)
        layout.addWidget(self.input_edit)

        buttons = QHBoxLayout()
        self.send_button = QPushButton(self.tr("Send"), self)
        self.send_button.clicked.connect(self.send_current_text)
        buttons.addWidget(self.send_button)
        self.cancel_button = QPushButton(self.tr("Cancel"), self)
        self.cancel_button.clicked.connect(self.cancel_current)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    def refresh_sessions(self):
        """Reload the session combo from the API (background thread)."""
        if self._sessions_call is not None:
            return
        self._sessions_call = AsyncCall(self.client.list_sessions, self)
        self._sessions_call.done.connect(self._on_sessions_loaded)
        self._sessions_call.error.connect(self._on_sessions_error)
        self._sessions_call.start()

    def _on_sessions_loaded(self, sessions: list):
        self._sessions_call = None
        current = self.session_id
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        self.session_combo.addItem(self.tr("(new session)"), None)
        for session in sessions or []:
            label = "{} ({})".format(
                session.get("name") or self.tr("Untitled"),
                session.get("id"))
            self.session_combo.addItem(label, session.get("id"))
        if current is not None:
            index = self.session_combo.findData(current)
            if index >= 0:
                self.session_combo.setCurrentIndex(index)
        self.session_combo.blockSignals(False)

    def _on_sessions_error(self, message: str):
        self._sessions_call = None
        self._set_status(self.tr("Could not load sessions: {}").format(message))

    def start_new_session(self):
        """Forget the current session; the next message creates one."""
        self.session_id = None
        self.session_combo.blockSignals(True)
        self.session_combo.setCurrentIndex(0)
        self.session_combo.blockSignals(False)
        self._clear_messages()

    def _on_session_changed(self, index: int):
        session_id = self.session_combo.itemData(index)
        if session_id == self.session_id:
            return
        self.session_id = session_id
        self._clear_messages()
        if session_id is not None:
            self._load_history(session_id)

    def _load_history(self, session_id: int):
        """Fetch and render a session's message history."""
        self._history_call = AsyncCall(
            lambda: self.client.get_messages(session_id), self)
        self._history_call.done.connect(self._on_history_loaded)
        self._history_call.error.connect(self._on_history_error)
        self._history_call.start()

    def _on_history_loaded(self, payload: dict):
        self._history_call = None
        for message in payload.get("messages") or []:
            text = message.get("text_content") or ""
            if not text:
                continue
            plot_files = self._resolve_plot_refs(self._extract_plot_refs(text))
            self._add_message(message.get("role", "assistant"), text,
                              plot_files=plot_files)

    def _on_history_error(self, message: str):
        self._history_call = None
        self._set_status(self.tr("Could not load history: {}").format(message))

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------
    def send_current_text(self):
        """Send the input-box content (the Send button entry point)."""
        if self._worker is not None:
            self._set_status(self.tr("Please wait for the current answer."))
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if not self.auth_store.has_credentials():
            self._set_status(self.tr(
                "Not signed in. Open the settings dialog to configure "
                "access."))
            self.settingsRequested.emit()
            return
        self.input_edit.clear()
        self._add_message("user", text)

        payload = text
        if self.context_check.isChecked():
            context = self._collect_context()
            if context:
                payload = "{}\n\n---\n\n{}".format(context, text)
        self._set_status(self.tr("Submitting to EpidBot..."))
        self._set_busy(True)

        self._worker = ChatWorker(
            self.client, payload,
            session_id=self.session_id,
            locale=str(self.auth_store.get_plain("locale")) or "en",
            parent=self)
        self._worker.submitted.connect(self._on_submitted)
        self._worker.statusChanged.connect(self._set_status)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _collect_context(self) -> str:
        """Build the [QGIS context] preamble from the current canvas."""
        from ..core.map_context import build_context_text

        try:
            layers, extent, project_crs = map_context_qgis.collect_map_context(
                self.iface)
            return build_context_text(layers, extent, project_crs)
        except Exception as exc:  # noqa: BLE001 - context is best-effort
            self._set_status(
                self.tr("Could not collect map context: {}").format(exc))
            return ""

    def _on_submitted(self, job_id: str, session_id: int):
        """Adopt the server-created session when none was selected."""
        if self.session_id is None and session_id:
            self.session_id = session_id

    def _on_completed(self, result: dict):
        self._worker = None
        self._set_busy(False)
        self._set_status("")
        if result.get("session_id"):
            self.session_id = result["session_id"]
        content = result.get("content") or ""
        plot_refs = self._extract_plot_refs(content)
        plot_files = self._resolve_plot_refs(plot_refs)
        self._add_message("assistant", content, plot_files=plot_files)
        self.refresh_sessions()

    def _on_failed(self, message: str):
        self._worker = None
        self._set_busy(False)
        self._set_status("")
        self._add_message("system", self.tr("Error: {}").format(message))

    def _on_cancelled(self):
        self._worker = None
        self._set_busy(False)
        self._set_status("")
        self._add_message("system", self.tr("Request cancelled."))

    def cancel_current(self):
        """Stop waiting for the current job (EpidBot keeps processing)."""
        if self._worker is not None:
            self._worker.cancel()
            self._set_status(self.tr("Cancelling..."))

    # ------------------------------------------------------------------
    # Plot handling
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_plot_refs(text: str) -> List[str]:
        """Markdown image refs that point at EpidBot plots."""
        refs = []
        for ref in _PLOT_REF_RE.findall(text or ""):
            lower = ref.lower()
            if lower.endswith(_IMAGE_EXTS) or "plots/" in ref:
                refs.append(ref)
        return refs

    def _resolve_plot_refs(self, refs: List[str]) -> List[str]:
        """Download plot refs to the local cache; return local paths."""
        paths = []
        for ref in refs:
            filename = self.client.extract_plot_filename(ref)
            if not filename:
                continue
            target = os.path.join(plots_dir(), filename)
            try:
                self.client.download_plot(filename, target)
                paths.append(target)
            except Exception:
                continue  # Skip broken plots rather than failing the turn.
        return paths

    def add_plot_to_layout(self, filename: str, local_path: str):
        """Embed a plot image in the 'EpidBot Plots' print layout."""
        from qgis.core import (QgsLayoutItemPicture, QgsLayoutPoint,
                               QgsLayoutSize, QgsPrintLayout, QgsProject,
                               QgsUnitTypes)

        project = QgsProject.instance()
        manager = project.layoutManager()
        layout = manager.layoutByName("EpidBot Plots")
        if layout is None:
            layout = QgsPrintLayout(project)
            layout.initializeDefaults()
            layout.setName("EpidBot Plots")
            manager.addLayout(layout)
        count = sum(1 for item in layout.items()
                    if isinstance(item, QgsLayoutItemPicture))
        picture = QgsLayoutItemPicture(layout)
        picture.setPicturePath(local_path)
        picture.attemptMove(QgsLayoutPoint(
            15, 15 + (count % 4) * 75, QgsUnitTypes.LayoutMillimeters))
        picture.attemptResize(QgsLayoutSize(
            120, 65, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(picture)
        self.iface.messageBar().pushMessage(
            self.tr("EpidBot"),
            self.tr("Plot added to layout 'EpidBot Plots'."),
            level=0, duration=4)

    # ------------------------------------------------------------------
    # Message list helpers
    # ------------------------------------------------------------------
    def _add_message(self, role: str, text: str, plot_files=None):
        widget = MessageWidget(role, text, plot_files=plot_files,
                               parent=self.messages_host)
        widget.plotActionRequested.connect(self._on_plot_action)
        self.messages_layout.addWidget(widget)
        self._scroll_to_bottom()

    def _on_plot_action(self, filename: str, action: str):
        if action != "layout":
            return
        local_path = os.path.join(plots_dir(), filename)
        if os.path.exists(local_path):
            self.add_plot_to_layout(filename, local_path)

    def _clear_messages(self):
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        from PyQt5.QtCore import QTimer

        QTimer.singleShot(50, lambda: bar.setValue(bar.maximum()))

    # ------------------------------------------------------------------
    # Misc state helpers
    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool):
        self.send_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.session_combo.setEnabled(not busy)

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _on_context_toggled(self, checked: bool):
        self.auth_store.set_plain("include_map_context", bool(checked))
