"""QThread worker that runs a chat request off the QGIS main thread.

Thin glue between :class:`core.api_client.EpidBotApiClient` and the UI: the
worker runs the blocking submit + poll cycle in its own thread and reports
progress through Qt signals (which are automatically queued across threads).
"""

import threading

from PyQt5.QtCore import QThread, pyqtSignal

from .api_client import ChatCancelled


class ChatWorker(QThread):
    """Executes one :meth:`EpidBotApiClient.send_message` call."""

    submitted = pyqtSignal(str, int)   # job_id, session_id
    statusChanged = pyqtSignal(str)    # human-readable progress text
    completed = pyqtSignal(dict)       # send_message() result payload
    failed = pyqtSignal(str)           # error message
    cancelled = pyqtSignal()

    def __init__(self, client, message: str, session_id=None, locale: str = "en",
                 parent=None):
        """Create the worker; call :meth:`start` to begin the request.

        :param client: an :class:`EpidBotApiClient` (or test double).
        :param message: the user's message text.
        :param session_id: optional EpidBot session id (None creates one).
        :param locale: response language, e.g. ``"en"`` or ``"pt-BR"``.
        """
        super().__init__(parent)
        self.client = client
        self.message = message
        self.session_id = session_id
        self.locale = locale
        self._cancel_event = threading.Event()

    def run(self):  # noqa: D102 - QThread override
        try:
            result = self.client.send_message(
                self.message,
                session_id=self.session_id,
                locale=self.locale,
                cancel_event=self._cancel_event,
                on_status=self.statusChanged.emit,
                on_submitted=lambda job_id, session_id:
                    self.submitted.emit(job_id, session_id or 0),
            )
            self.completed.emit(result)
        except ChatCancelled:
            self.cancelled.emit()
        except Exception as exc:  # EpidBotApiError + safety net
            self.failed.emit(str(exc))

    def cancel(self):
        """Ask the poll loop to stop (cooperative, client-side)."""
        self._cancel_event.set()
