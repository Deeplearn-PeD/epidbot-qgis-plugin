"""Preview/confirm dialog for EpidBot attribute edit operations.

Nothing is written until the user presses Apply here — this dialog is the
mandatory human-in-the-loop step for attribute write-back.
"""

from typing import Any, Dict, List

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                                 QLabel, QTextBrowser, QVBoxLayout)

from ..core import layer_editor


class EditPreviewDialog(QDialog):
    """Review an EpidBot edit operation against candidate target layers."""

    def __init__(self, candidates: List[Any], op: Dict[str, Any], parent=None):
        """Build the preview.

        :param candidates: QgsVectorLayer objects whose name matches the
            operation's ``layer`` hint (may be empty -> user picks nothing).
        :param op: raw edit operation dict (validated here).
        """
        super().__init__(parent)
        self.setWindowTitle(self.tr("EpidBot wants to edit a layer"))
        self.setMinimumWidth(420)
        try:
            self.op = layer_editor.validate_edit_op(op)
        except layer_editor.EditOpError as exc:
            # Surfaced before the dialog opens by the caller; kept as a
            # safety net.
            self.op = None
            self._error = str(exc)
            self.candidates = []
            self._build_error_ui(str(exc))
            return
        self.candidates = candidates
        self._build_ui()
        self._refresh_stats()

    # ------------------------------------------------------------------
    def _build_error_ui(self, message: str):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("This edit operation is invalid:"),
                                self))
        details = QTextBrowser(self)
        details.setPlainText(message)
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(self.tr(
            "EpidBot proposes the following change to your layer data. "
            "Review it carefully - it will modify the attribute table."),
                       self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.layer_combo = QComboBox(self)
        for layer in self.candidates:
            self.layer_combo.addItem(layer.name(), layer)
        if len(self.candidates) <= 1:
            self.layer_combo.setEnabled(False)
        self.layer_combo.currentIndexChanged.connect(self._refresh_stats)
        layout.addWidget(self.layer_combo)

        field = self.op["field"]
        summary = QLabel(self.tr(
            "<b>{op}</b> field <b>{name}</b> ({type}) — join by "
            "<b>{key}</b>{confidence}").format(
                op=(layer_editor.OP_ADD_FIELD if self.op["op"]
                    == layer_editor.OP_ADD_FIELD
                    else self.tr("Update")),
                name=field["name"], type=field["type"],
                key=self.op["key"],
                confidence=self._confidence_text()), self)
        summary.setWordWrap(True)
        summary.setTextFormat(Qt.RichText)
        layout.addWidget(summary)

        self.stats_label = QLabel("", self)
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        self.sample_view = QTextBrowser(self)
        self.sample_view.setMaximumHeight(160)
        layout.addWidget(self.sample_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Apply |
                                   QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Apply).setText(self.tr("Apply"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _confidence_text(self) -> str:
        confidence = self.op.get("confidence")
        if confidence == "fetched":
            return self.tr(" (values fetched from a data source)")
        if confidence == "estimated":
            return self.tr(
                " (⚠ values ESTIMATED by the model - verify before use)")
        return ""

    # ------------------------------------------------------------------
    def _selected_layer(self):
        return self.layer_combo.currentData()

    def _refresh_stats(self):
        layer = self._selected_layer()
        if layer is None:
            self.stats_label.setText(self.tr("No matching layer found."))
            self.sample_view.setPlainText("")
            return
        try:
            keys = layer_editor.build_layer_keys(layer, self.op["key"])
            plan = layer_editor.plan_edit(self.op["values"], keys,
                                          self.op["field"]["type"])
        except layer_editor.EditOpError as exc:
            self.stats_label.setText(
                self.tr("Cannot join by this key: {}").format(exc))
            self.sample_view.setPlainText("")
            return
        self.plan = plan
        matched = len(plan["matched"])
        unmatched = len(plan["unmatched"])
        invalid = len(plan["invalid"])
        text = self.tr("Matches {matched} of {total} values "
                       "({unmatched} unmatched, {invalid} invalid).") \
            .format(matched=matched, total=len(self.op["values"]),
                    unmatched=unmatched, invalid=invalid)
        if unmatched:
            shown = ", ".join(plan["unmatched"][:5])
            more = "…" if unmatched > 5 else ""
            text += "\n" + self.tr("Unmatched keys: {}{}").format(
                shown, more)
        self.stats_label.setText(text)

        sample_rows = list(plan["matched"].items())[:8]
        lines = ["{} -> {}".format(fid, value) for fid, value in sample_rows]
        if len(plan["matched"]) > len(sample_rows):
            lines.append(self.tr("… and {} more").format(
                len(plan["matched"]) - len(sample_rows)))
        self.sample_view.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    @property
    def layer(self):
        """The layer chosen for the edit (None when nothing selected)."""
        return self._selected_layer()

    @property
    def op_with_echoed_key(self) -> Dict[str, Any]:
        """The normalized op (values already validated against the layer)."""
        return self.op
