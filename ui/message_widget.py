"""Chat message widget: role header, rendered content, plot thumbnails."""

import os
from typing import List, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                             QToolButton, QVBoxLayout, QWidget)

from ..core.markdown_lite import markdown_to_html

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
    """One chat bubble with optional inline plot previews.

    Plot previews expose three actions each, reported via
    :attr:`plotActionRequested` (``filename``, ``action``):

    - ``save`` - save a copy to a user-chosen file;
    - ``layout`` - embed the image in a "EpidBot Plots" print layout;
    - ``viewer`` - open in the system image viewer.
    """

    plotActionRequested = pyqtSignal(str, str)

    def __init__(self, role: str, text: str,
                 plot_files: Optional[List[str]] = None, parent=None):
        """Build the bubble.

        :param role: ``user`` / ``assistant`` / ``system``.
        :param text: markdown text of the message.
        :param plot_files: local paths of downloaded plot images.
        """
        super().__init__(parent)
        self.role = role
        self.plot_files = list(plot_files or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        header_text = _ROLE_LABELS.get(role, role)
        if header_text:
            header = QLabel(header_text)
            header.setStyleSheet("font-weight: bold; color: #0b6e6e;")
            layout.addWidget(header)

        body = QLabel()
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setText(markdown_to_html(text))
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction |
                                     Qt.LinksAccessibleByMouse)
        layout.addWidget(body)

        for path in self.plot_files:
            layout.addWidget(self._build_plot_preview(path))

        if role == ROLE_SYSTEM:
            self.setStyleSheet("color: #8a6d3b; background: #fcf8e3; "
                               "border-radius: 4px;")
        else:
            self.setStyleSheet("background: rgba(11, 110, 110, 0.06); "
                               "border-radius: 4px;")

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
