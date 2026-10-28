"""Dockable chat panel: sessions, message list, map-context toggle."""

import os
import re
import tempfile
from contextlib import suppress
from typing import Any, List, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox,
                              QHBoxLayout, QInputDialog, QLabel, QMenu,
                              QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                              QToolButton, QVBoxLayout, QWidget)

from ..core import layer_editor
from ..core import layer_export
from ..core import layer_loader
from ..core import map_context_qgis
from ..core.api_client import EpidBotApiClient
from ..core.chat_worker import ChatWorker
from ..core.geo_extract import extract_edit_ops, extract_geo_sources
from .async_call import AsyncCall
from .edit_preview_dialog import EditPreviewDialog
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
        self._geo_call: Optional[AsyncCall] = None
        self._attach_call: Optional[AsyncCall] = None
        self._attachments: List[dict] = []
        self._thread: List[dict] = []
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
        export_button = QToolButton(self)
        export_button.setText("\U0001F4E4")
        export_button.setToolTip(self.tr("Export conversation to Markdown"))
        export_button.clicked.connect(self.export_conversation)
        top.addWidget(export_button)
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

        # Quick prompts
        self.quick_combo = QComboBox(self)
        self.quick_combo.addItem(self.tr("Quick prompts..."), None)
        for preset in (
                self.tr("Summarize the latest dengue season in Brazil"),
                self.tr("Which datasets cover malaria in the Amazon?"),
                self.tr("Explain the active layers on my map"),
                self.tr("How do I download SINAN data for my state?"),
                self.tr("What is the population at risk in my map extent?")):
            self.quick_combo.addItem(preset, preset)
        self.quick_combo.activated.connect(self._on_quick_prompt)
        layout.addWidget(self.quick_combo)

        # Pending layer attachments (chips) + attach button
        attach_row = QHBoxLayout()
        attach_button = QToolButton(self)
        attach_button.setText("\U0001F4CE")
        attach_button.setToolTip(self.tr("Attach layer data to the message"))
        menu = QMenu(attach_button)
        menu.addAction(self.tr("Attach selected features"),
                       lambda: self._on_attach(layer_export.SCOPE_SELECTION))
        menu.addAction(self.tr("Attach visible features in extent"),
                       lambda: self._on_attach(layer_export.SCOPE_EXTENT))
        menu.addAction(self.tr("Attach entire layer…"),
                       lambda: self._on_attach(layer_export.SCOPE_ALL))
        menu.addSeparator()
        menu.addAction(self.tr("Attach identifiers only (for edits)"),
                       lambda: self._on_attach(layer_export.SCOPE_IDENTIFIERS))
        attach_button.setMenu(menu)
        attach_button.setPopupMode(QToolButton.InstantPopup)
        attach_row.addWidget(attach_button)
        attach_row.addStretch(1)
        layout.addLayout(attach_row)

        self.attach_chips_host = QWidget(self)
        self.attach_chips_layout = QHBoxLayout(self.attach_chips_host)
        self.attach_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.attach_chips_host.hide()
        layout.addWidget(self.attach_chips_host)

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

        attachment_blocks = self._attachment_blocks()
        artifact_ids = [attachment["artifact_id"]
                        for attachment in self._attachments
                        if attachment.get("artifact_id") is not None]
        self._clear_attachments()

        payload = text
        if self.context_check.isChecked():
            context = self._collect_context()
            if context:
                payload = "{}\n\n---\n\n{}".format(context, text)
        if attachment_blocks:
            payload = "\n\n".join(attachment_blocks + [payload])
        self._set_status(self.tr("Submitting to EpidBot..."))
        self._set_busy(True)

        self._worker = ChatWorker(
            self.client, payload,
            session_id=self.session_id,
            locale=str(self.auth_store.get_plain("locale")) or "en",
            artifact_ids=artifact_ids,
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

    # ------------------------------------------------------------------
    # Spatial prompts
    # ------------------------------------------------------------------
    def reveal_and_prefill(self, text: str):
        """Show the dock and put ``text`` in the input box, ready to edit."""
        parent = self.parentWidget()
        if parent is not None:
            parent.show()
            parent.raise_()
        self.input_edit.setPlainText(text)
        self.input_edit.setFocus()

    def prompt_about_extent(self):
        """Pre-fill a prompt focused on the current canvas extent."""
        # The extent prompt is pointless without the context block.
        if not self.context_check.isChecked():
            self.context_check.setChecked(True)
        self.reveal_and_prefill(self.tr(
            "Focus on the current map extent. Describe the spatial patterns "
            "of the active layers within this extent and suggest the next "
            "analysis steps."))

    def prompt_about_selection(self):
        """Pre-fill a prompt carrying a summary of the selected features."""
        try:
            summary = map_context_qgis.collect_selection_summary(self.iface)
        except Exception as exc:  # noqa: BLE001 - context is best-effort
            summary = None
            self._set_status(
                self.tr("Could not collect selection: {}").format(exc))
        if not summary:
            self._set_status(self.tr(
                "No features selected on the map. Select features first."))
            return
        self.reveal_and_prefill(self.tr(
            "Analyze the features I selected on the map (details below) and "
            "summarize their spatial pattern:\n\n[selection]\n{}\n"
            "[/selection]").format(summary))


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
            # Skip broken plots rather than failing the turn.
            with suppress(Exception):
                self.client.download_plot(filename, target)
                paths.append(target)
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
    # Conversation export
    # ------------------------------------------------------------------
    def export_conversation(self):
        """Write the visible thread to a user-chosen Markdown file."""
        from qgis.PyQt.QtWidgets import QFileDialog

        if not self._thread:
            self._set_status(self.tr("Nothing to export yet."))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export conversation"), "epidbot-chat.md",
            "Markdown (*.md);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# EpidBot conversation\n\n")
                for message in self._thread:
                    title = {"user": self.tr("You"),
                             "assistant": "EpidBot"}.get(
                        message["role"], self.tr("System"))
                    fh.write("## {}\n\n{}\n".format(title, message["text"]))
                    for plot in message["plot_files"]:
                        fh.write("![plot]({})\n".format(plot))
                    fh.write("\n")
        except OSError as exc:
            self._set_status(
                self.tr("Could not export conversation: {}").format(exc))
            return
        self._set_status(self.tr("Conversation exported to {}.").format(path))

    # ------------------------------------------------------------------
    # Layer attachments
    # ------------------------------------------------------------------
    def _inline_budget_bytes(self) -> int:
        kb = float(self.auth_store.get_plain("inline_budget_kb") or 64.0)
        return int(kb * 1024)

    def _vector_layers(self) -> List[Any]:
        """Currently visible vector layers, canvas order."""
        from qgis.core import QgsMapLayerType

        try:
            layers = self.iface.mapCanvas().layers() or []
        except Exception:
            return []
        return [layer for layer in layers
                if layer.type() == QgsMapLayerType.VectorLayer]

    def _pick_layer(self):
        """Choose one vector layer; None when there is nothing to pick."""
        layers = self._vector_layers()
        if not layers:
            self._set_status(self.tr("No vector layers on the map."))
            return None
        if len(layers) == 1:
            return layers[0]
        names = [layer.name() for layer in layers]
        choice, ok = QInputDialog.getItem(
            self, self.tr("Attach layer"),
            self.tr("Choose a layer:"), names, 0, False)
        if not ok:
            return None
        return layers[names.index(choice)]

    def _on_attach(self, scope: str):
        layer = self._pick_layer()
        if layer is None:
            return
        if scope == layer_export.SCOPE_SELECTION \
                and not layer.selectedFeatureIds():
            self._set_status(self.tr(
                "No features selected on the map. Select features first."))
            return
        extent = map_context_qgis.canvas_extent_4326(self.iface)
        self._start_attachment_build(layer, scope, extent, precision=6)

    def _start_attachment_build(self, layer, scope, extent, precision: int):
        """Serialize off-thread; reductions continue on the UI thread."""
        if self._attach_call is not None:
            self._set_status(self.tr("Still preparing the previous "
                                     "attachment..."))
            return
        self._set_status(self.tr("Preparing attachment..."))
        self._attach_call = AsyncCall(
            lambda: layer_export.collect_geojson(
                layer, scope, extent=extent,
                budget_bytes=self._inline_budget_bytes(),
                precision=precision),
            self)
        self._attach_call.done.connect(
            lambda result, layer=layer, scope=scope, extent=extent,
            precision=precision: self._on_attachment_prepared(
                layer, scope, extent, precision, result))
        self._attach_call.error.connect(self._on_attachment_error)

    def _on_attachment_error(self, message: str):
        self._attach_call = None
        self._set_status("")
        choice = self._ask_stats_or_cancel(message)
        if choice == "stats":
            self._fall_back_to_stats()

    def _on_attachment_prepared(self, layer, scope, extent, precision: int,
                                result: dict):
        self._attach_call = None
        self._set_status("")
        if not result["over_budget"]:
            self._add_attachment(result)
            return
        choice = self._ask_reduction(scope, bool(layer.selectedFeatureIds()),
                                     result)
        if choice == "extent":
            self._start_attachment_build(layer, layer_export.SCOPE_EXTENT,
                                         map_context_qgis.canvas_extent_4326(
                                             self.iface),
                                         precision)
        elif choice == "selection":
            self._start_attachment_build(layer, layer_export.SCOPE_SELECTION,
                                         None, precision)
        elif choice == "precision":
            self._start_attachment_build(layer, scope, extent,
                                         4 if precision > 4 else 3)
        elif choice == "artifact":
            self._upload_as_artifact(layer, scope, extent, result)
        elif choice == "stats":
            self._fall_back_to_stats()

    def _ask_reduction(self, scope: str, has_selection: bool,
                       result: dict) -> Optional[str]:
        """Interactive reduction menu; returns a choice token or None."""
        size_kb = result["size_bytes"] // 1024 + 1
        budget_kb = self._inline_budget_bytes() // 1024
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Attachment too large"))
        box.setText(self.tr(
            "'{label}' is {size} KB, above the {budget} KB inline budget.")
            .format(label=result["label"], size=size_kb, budget=budget_kb))
        tokens = []
        options = []
        if scope == layer_export.SCOPE_ALL:
            options.append((self.tr("Limit to current extent"), "extent"))
        if scope == layer_export.SCOPE_EXTENT and has_selection:
            options.append((self.tr("Narrow to selected features"),
                            "selection"))
        options.append((self.tr("Reduce coordinate precision"), "precision"))
        if bool(self.auth_store.get_plain("artifact_uploads")):
            options.append((self.tr("Upload as artifact (server)"),
                            "artifact"))
        options.append((self.tr("Send statistics only"), "stats"))
        options.append((self.tr("Cancel"), None))
        for label, token in options:
            button = box.addButton(label, QMessageBox.ActionRole
                                   if token else QMessageBox.RejectRole)
            tokens.append((button, token))
        box.exec()
        clicked = box.clickedButton()
        for button, token in tokens:
            if button is clicked:
                return token
        return None

    def _ask_stats_or_cancel(self, message: str) -> Optional[str]:
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Attachment failed"))
        box.setText(message)
        stats = box.addButton(self.tr("Send statistics only"),
                              QMessageBox.ActionRole)
        box.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        return "stats" if box.clickedButton() is stats else None

    def _fall_back_to_stats(self):
        """Stats-only: the context block already carries layer summaries."""
        if not self.context_check.isChecked():
            self.context_check.setChecked(True)
        self._set_status(self.tr(
            "Attachment dropped - the map context (field names, sample "
            "values, counts) will be sent instead."))

    def _upload_as_artifact(self, layer, scope, extent, result: dict):
        """Tier 2: upload the serialized layer as a server artifact."""
        self._set_status(self.tr("Uploading artifact..."))
        path = os.path.join(layer_loader.layers_dir(), "upload.geojson")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(result["geojson"])
        call = AsyncCall(lambda: self.client.upload_artifact(path), self)
        call.done.connect(lambda response, r=result: self.
                          _on_artifact_uploaded(response, r))
        call.error.connect(self._on_artifact_upload_error)
        self._attach_call = call
        call.start()

    def _on_artifact_uploaded(self, response: dict, result: dict):
        self._attach_call = None
        self._set_status("")
        artifact_id = response.get("id") or response.get("artifact_id")
        if artifact_id is None:
            self._set_status(self.tr(
                "Server did not return an artifact id."))
            return
        self._add_attachment(dict(result, artifact_id=int(artifact_id)))

    def _on_artifact_upload_error(self, message: str):
        self._attach_call = None
        self._set_status("")
        self._set_status(self.tr(
            "Artifact upload failed: {}").format(message))

    def _add_attachment(self, result: dict):
        self._attachments.append(result)
        self._rebuild_chips()

    def _rebuild_chips(self):
        while self.attach_chips_layout.count():
            item = self.attach_chips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, attachment in enumerate(self._attachments):
            size_kb = attachment["size_bytes"] // 1024 + 1
            if attachment.get("artifact_id") is not None:
                text = "{} \u2192 artifact {}".format(
                    attachment["label"], attachment["artifact_id"])
            else:
                text = "{} — {:,} feats, {} KB".format(
                    attachment["label"],
                    attachment["feature_count"], size_kb) \
                    .replace(",", " ")
            chip = QWidget(self.attach_chips_host)
            row = QHBoxLayout(chip)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel("\U0001F4CE " + text, chip))
            remove = QToolButton(chip)
            remove.setText("\u2715")
            remove.setToolTip(self.tr("Remove attachment"))
            remove.clicked.connect(
                lambda _=False, i=index: self._remove_attachment(i))
            row.addWidget(remove)
            row.addStretch(1)
            self.attach_chips_layout.addWidget(chip)
        self.attach_chips_host.setVisible(bool(self._attachments))

    def _remove_attachment(self, index: int):
        if 0 <= index < len(self._attachments):
            self._attachments.pop(index)
            self._rebuild_chips()

    def _clear_attachments(self):
        self._attachments = []
        self._rebuild_chips()

    def _attachment_blocks(self) -> List[str]:
        """Serialize pending attachments as prompt text blocks."""
        blocks = []
        for attachment in self._attachments:
            if attachment.get("artifact_id") is not None:
                blocks.append(
                    "[attachment: {label} -> artifact {aid}]".format(
                        label=attachment["label"],
                        aid=attachment["artifact_id"]))
                continue
            blocks.append(
                "[attachment: {label}]\n```geojson\n{geojson}\n```\n"
                "[/attachment]".format(label=attachment["label"],
                                       geojson=attachment["geojson"]))
        return blocks

    # ------------------------------------------------------------------
    # Attribute write-back
    # ------------------------------------------------------------------
    def prompt_edit_layer(self):
        """Attach identifiers of a layer and pre-fill an edit prompt."""
        layer = self._pick_layer()
        if layer is None:
            return
        self._on_attach(layer_export.SCOPE_IDENTIFIERS)
        join_hint = ""
        key = layer_export.pick_join_key(
            [field.name() for field in layer.fields()])
        if key:
            join_hint = self.tr(
                " Join the data by the '{}' field.").format(key)
        self.reveal_and_prefill(self.tr(
            "Add a new field to this layer with the data you can fetch "
            "(e.g. population counts).{hint} Use the identifiers attached "
            "above as join keys and return the result as an epidbot-edit "
            "block.").format(hint=join_hint))

    def _on_edit_action(self, op: dict):
        """Open the preview dialog for an edit op proposed by EpidBot."""
        from qgis.core import QgsProject, QgsMapLayerType

        hint = str(op.get("layer") or "")
        candidates = [layer for layer in QgsProject.instance().mapLayersByName(
            hint) if layer.type() == QgsMapLayerType.VectorLayer] \
            if hint else []
        dialog = EditPreviewDialog(candidates, op, self)
        if not dialog.exec():
            return
        layer = dialog.layer
        if layer is None or dialog.op is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report = layer_editor.apply_edit_op(layer, dialog.op)
        except layer_editor.EditOpError as exc:
            self.iface.messageBar().pushMessage(
                self.tr("EpidBot"),
                self.tr("Edit not applied: {}").format(exc),
                level=1, duration=8)
            return
        finally:
            QApplication.restoreOverrideCursor()
        unmatched = len(report["unmatched"])
        note = self.tr(" ({unmatched} keys did not match)").format(
            unmatched=unmatched) if unmatched else ""
        self.iface.messageBar().pushMessage(
            self.tr("EpidBot"),
            self.tr("{op} '{field}' on '{layer}': {matched} features{note}")
            .format(op=report["op"], field=report["field"],
                    layer=report["layer"], matched=report["matched"],
                    note=note),
            level=3, duration=6)

    # ------------------------------------------------------------------
    # Message list helpers
    # ------------------------------------------------------------------

    def _add_message(self, role: str, text: str, plot_files=None):
        geo_sources = (extract_geo_sources(text)
                       if role == "assistant" else [])
        widget = MessageWidget(role, text, plot_files=plot_files,
                               geo_sources=geo_sources,
                               parent=self.messages_host)
        widget.plotActionRequested.connect(self._on_plot_action)
        widget.geoActionRequested.connect(self._on_geo_action)
        widget.editActionRequested.connect(self._on_edit_action)
        if role == "assistant":
            edit_ops = extract_edit_ops(text)
            if edit_ops:
                widget.set_edit_ops(edit_ops)
        self.messages_layout.addWidget(widget)
        self._thread.append({"role": role, "text": text,
                             "plot_files": list(plot_files or [])})
        self._scroll_to_bottom()

    def _on_plot_action(self, filename: str, action: str):
        if action != "layout":
            return
        local_path = os.path.join(plots_dir(), filename)
        if os.path.exists(local_path):
            self.add_plot_to_layout(filename, local_path)

    def _on_geo_action(self, source: dict):
        """Load a geodata source from a message onto the map canvas."""
        if getattr(self, "_geo_call", None) is not None:
            self._set_status(self.tr("Still loading the previous dataset..."))
            return
        label = str(source.get("label", ""))
        self._set_status(self.tr("Loading '{}' onto the map...").format(label))
        self._geo_call = AsyncCall(
            lambda: layer_loader.add_source_to_canvas(
                self.iface, source, headers=self._download_headers(source)),
            self)
        self._geo_call.done.connect(self._on_geo_loaded)
        self._geo_call.error.connect(self._on_geo_error)
        self._geo_call.start()

    def _download_headers(self, source: dict) -> Optional[dict]:
        """EpidBot artifact URLs need the API key; foreign URLs stay clean."""
        url = str(source.get("url") or "")
        if source.get("artifact") or url.startswith(self.client.base_url):
            return self.client.auth_headers()
        return None

    def _on_geo_loaded(self, layer_name: str):
        self._geo_call = None
        self._set_status("")
        self.iface.messageBar().pushMessage(
            self.tr("EpidBot"),
            self.tr("Layer '{}' added to the map.").format(layer_name),
            level=3, duration=4)

    def _on_geo_error(self, message: str):
        self._geo_call = None
        self._set_status("")
        self.iface.messageBar().pushMessage(
            self.tr("EpidBot"),
            self.tr("Could not load geodata: {}").format(message),
            level=1, duration=6)


    def _clear_messages(self):
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._thread = []

    def _scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        from qgis.PyQt.QtCore import QTimer

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

    def _on_quick_prompt(self, index: int):
        """Drop a preset prompt into the input box for editing."""
        prompt = self.quick_combo.itemData(index)
        self.quick_combo.setCurrentIndex(0)  # back to the placeholder
        if not prompt:
            return
        self.input_edit.setPlainText(prompt)
        self.input_edit.setFocus()
