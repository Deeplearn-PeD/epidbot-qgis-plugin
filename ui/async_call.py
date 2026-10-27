"""Run quick API calls off the GUI thread with Qt-signal completion.

``AsyncCall`` executes a callable in a daemon ``threading.Thread`` and emits
``done``/``error`` signals when it finishes. Receivers must be QObject
methods (e.g. dock/dialog methods) so Qt delivers the result as a queued
call on the GUI thread - never connect plain functions to these signals.
"""

import threading
from typing import Any, Callable, Optional

from qgis.PyQt.QtCore import QObject, pyqtSignal


class AsyncCall(QObject):
    """One-shot background call with signal-based completion."""

    done = pyqtSignal(object)  # callable's return value
    error = pyqtSignal(str)    # error message

    def __init__(self, fn: Callable[[], Any], parent=None):
        """Store the callable; call :meth:`start` to run it."""
        super().__init__(parent)
        self._fn = fn
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.error.emit(str(exc))
