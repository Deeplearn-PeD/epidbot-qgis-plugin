"""Chat message widget: role header, rendered content, plot thumbnails."""

import html
import os
from typing import List, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                             QToolButton, QVBoxLayout, QWidget)

from ..core.markdown_lite import markdown_blocks

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"

_ROLE_LABELS = {
    ROLE_USER: "You",
    ROLE_ASSISTANT: "EpidBot",
    ROLE_SYSTEM: "",
}

_THUMBNAIL_SIZE = 220


class MessageWidget(QWidget):
    """One chat bubble with optional inline plot previews and geodata rows.

    Plot previews expose three actions each, reported via
    :attr:`plotActionRequested` (``filename``, ``action``):

    - ``save`` - save a copy to a user-chosen file;
    - ``layout`` - embed the image in a "EpidBot Plots" print layout;
    - ``viewer`` - open in the system image viewer.

    Geodata sources (GeoJSON blocks, dataset URLs) are reported via
    :attr:`geoActionRequested` (the source dict).
    """

    plotActionRequested = pyqtSignal(str, str)
    geoActionRequested = pyqtSignal(object)

    def __init__(self, role: str, text: str,
                 plot_files: Optional[List[str]] = None,
                 geo_sources: Optional[List[dict]] = None, parent=None):
        """Build the bubble.

        :param role: ``user`` / ``assistant`` / ``system``.
        :param text: markdown text of the message.
        :param plot_files: local paths of downloaded plot images.
        :param geo_sources: geodata sources advertised by the message.
        """
        super().__init__(parent)
        self.role = role
        self.plot_files = list(plot_files or [])
        self.geo_sources = list(geo_sources or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        header_text = _ROLE_LABELS.get(role, role)
        if header_text:
            header = QLabel(header_text)
            header.setStyleSheet("font-weight: bold; color: #0b6e6e;")
            layout.addWidget(header)

        for block in markdown_blocks(text):
            if block["kind"] == "code":
                layout.addWidget(self._build_code_box(block["lang"],
                                                      block["code"]))
            else:
                layout.addWidget(self._build_body_label(block["html"]))

        for path in self.plot_files:
            layout.addWidget(self._build_plot_preview(path))

        if role == ROLE_ASSISTANT:
            for source in self.geo_sources:
                layout.addWidget(self._build_geo_row(source))

        if role == ROLE_SYSTEM:
            self.setStyleSheet("color: #8a6d3b; background: #fcf8e3; "
                               "border-radius: 4px;")
        else:
            self.setStyleSheet("background: rgba(11, 110, 110, 0.06); "
                               "border-radius: 4px;")

    def _build_body_label(self, html_text: str) -> QLabel:
        """Rich-text label for one rendered markdown block."""
        body = QLabel(html_text, self)
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction |
                                     Qt.LinksAccessibleByMouse)
        return body

    def _build_code_box(self, lang: str, code: str) -> QWidget:
        """Framed code block with a copy-to-clipboard button."""
        from qgis.PyQt.QtWidgets import QFrame

        box = QFrame(self)
        box.setStyleSheet("background: #f6f8fa; border: 1px solid #dddddd; "
                          "border-radius: 4px;")
        column = QVBoxLayout(box)
        column.setContentsMargins(6, 4, 6, 4)
        column.setSpacing(2)

        top = QHBoxLayout()
        if lang:
            tag = QLabel(html.escape(lang), box)
            tag.setStyleSheet("color: #6a737d; font-size: 10px;")
            top.addWidget(tag)
        top.addStretch(1)
        copy_button = QToolButton(box)
        copy_button.setText("\U0001F4CB")
        copy_button.setToolTip(self.tr("Copy code to clipboard"))
        copy_button.clicked.connect(
            lambda _=False, c=code, b=copy_button: self._copy_code(c, b))
        top.addWidget(copy_button)
        column.addLayout(top)

        code_label = QLabel("<pre><code>{}</code></pre>".format(code), box)
        code_label.setTextFormat(Qt.RichText)
        code_label.setWordWrap(True)
        code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        column.addWidget(code_label)
        return box

    @staticmethod
    def _copy_code(code: str, button) -> None:
        """Put code on the clipboard and acknowledge with a brief tooltip."""
        from qgis.PyQt.QtWidgets import QApplication

        QApplication.clipboard().setText(code)
        if button is not None:
            button.setToolTip("\u2713")
            from qgis.PyQt.QtCore import QTimer

            QTimer.singleShot(1200, lambda: button.setToolTip(
                MessageWidget.tr("Copy code to clipboard")))

    def _build_plot_preview(self, path: str) -> QWidget:
        """Thumbnail plus action buttons for one downloaded plot."""
        box = QWidget(self)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 4, 0, 0)

        thumb = QLabel()
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            thumb.setPixmap(pixmap.scaled(
                _THUMBNAIL_SIZE, _THUMBNAIL_SIZE,
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            thumb.setText(os.path.basename(path))
        row.addWidget(thumb)

        column = QVBoxLayout()
        for action, icon, tooltip in (
                ("save", "\U0001F4BE", self.tr("Save plot to disk...")),
                ("layout", "\U0001F5FA", self.tr("Add to 'EpidBot Plots' layout")),
                ("viewer", "\U0001F441", self.tr("Open in image viewer"))):
            button = QToolButton(box)
            button.setText(icon)
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda _=False, a=action, p=path: self._emit_action(a, p))
            column.addWidget(button)
        column.addStretch(1)
        row.addLayout(column)
        row.addStretch(1)
        return box

    def _build_geo_row(self, source: dict) -> QWidget:
        """One-line row advertising a loadable geodata source."""
        box = QWidget(self)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 2, 0, 2)
        row.addWidget(QLabel("\U0001F5FA " + str(source.get("label", "?")), box))
        button = QToolButton(box)
        button.setText(self.tr("Add to map"))
        button.setToolTip(self.tr("Load this geodata as a map layer"))
        button.clicked.connect(
            lambda _=False, s=source: self.geoActionRequested.emit(s))
        row.addWidget(button)
        row.addStretch(1)
        return box

    def _emit_action(self, action: str, path: str) -> None:
        """Handle save/viewer locally; delegate the rest to the dock."""
        if action == "save":
            target, _ = QFileDialog.getSaveFileName(
                self, self.tr("Save plot"), os.path.basename(path),
                "PNG image (*.png);;All files (*)")
            if target:
                try:
                    with open(path, "rb") as src, open(target, "wb") as dst:
                        dst.write(src.read())
                except OSError as exc:
                    QMessageBox.warning(self, self.tr("EpidBot"),
                                        self.tr("Could not save plot: {}")
                                        .format(exc))
            return
        if action == "viewer":
            from qgis.PyQt.QtGui import QDesktopServices
            from qgis.PyQt.QtCore import QUrl

            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        self.plotActionRequested.emit(os.path.basename(path), action)
