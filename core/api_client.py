"""HTTP client for the EpidBot REST API.

Pure Python (no Qt) so it can be unit-tested outside QGIS. Uses
``requests``, which is bundled with QGIS on all supported platforms.

Authentication is API-key only: the key is sent on every request via the
``X-API-Key`` header (personal keys are created in the EpidBot web UI).

Talks to the documented EpidBot surface on the production API host
(``https://api.epidbot.kwar-ai.com.br``):

- ``GET /api/v1/auth/me`` - connection test.
- ``POST /api/v1/chat`` / ``GET /api/v1/chat/{job_id}`` - submit + poll chat.
- ``GET/POST /api/v1/sessions``, ``GET /api/v1/sessions/{id}/messages``.
- ``GET /api/v1/sessions/plots/{filename}`` - authenticated plot download.
"""

import time
from typing import Any, Callable, Dict, List, Optional

import requests

API_PREFIX = "/api/v1"

# Default EpidBot API host (production).
DEFAULT_BASE_URL = "https://api.epidbot.kwar-ai.com.br"

# Chat job statuses (mirrors epidbot.api.schemas.JobStatus)
JOB_PENDING = "pending"
JOB_PROCESSING = "processing"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

# Transient server failures worth an automatic retry.
_RETRYABLE_STATUS_CODES = {502, 503, 504}


class EpidBotApiError(Exception):
    """Base error for all EpidBot API interactions."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 detail: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class EpidBotAuthError(EpidBotApiError):
    """Raised when the API key is missing, unknown, or revoked."""


class ChatCancelled(Exception):
    """Raised inside :meth:`EpidBotApiClient.send_message` when the caller
    cancels a pending chat job via the cancellation event."""


class EpidBotApiClient:
    """Client for the EpidBot REST API (API-key authentication).

    Safe to use from worker threads; a single chat job runs at a time.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, auth_store=None,
                 poll_interval: float = 1.5,
                 poll_max_interval: float = 5.0,
                 request_timeout: float = 30.0,
                 chat_timeout: float = 600.0,
                 max_retries: int = 2,
                 retry_base_delay: float = 0.5):
        """Create a client.

        :param base_url: EpidBot API base URL
            (default: ``https://api.epidbot.kwar-ai.com.br``).
        :param auth_store: optional :class:`core.auth_store.AuthStore` used
            to persist and retrieve the API key.
        :param poll_interval: seconds between job-status polls.
        :param poll_max_interval: cap for the poll backoff.
        :param request_timeout: per-HTTP-request timeout in seconds.
        :param chat_timeout: overall deadline for a chat job in seconds.
        :param max_retries: automatic retries for transient failures
            (connection errors, timeouts, HTTP 502/503/504).
        :param retry_base_delay: first backoff delay; doubles each attempt.
        """
        self.base_url = self._normalize_base_url(base_url)
        self.auth_store = auth_store
        self.poll_interval = poll_interval
        self.poll_max_interval = poll_max_interval
        self.request_timeout = request_timeout
        self.chat_timeout = chat_timeout
        self.max_retries = max(int(max_retries), 0)
        self.retry_base_delay = float(retry_base_delay)
        self._session: Optional[requests.Session] = None

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """Strip trailing slashes and whitespace from the base URL."""
        url = (base_url or "").strip().rstrip("/")
        if url and "://" not in url:
            url = "https://" + url
        return url

    def configure(self, base_url: str) -> None:
        """Update the base URL and drop any cached HTTP session."""
        self.base_url = self._normalize_base_url(base_url)
        self._session = None

    @property
    def session(self) -> requests.Session:
        """Lazily created ``requests.Session`` (single choke point for tests)."""
        if self._session is None:
            self._session = requests.Session()
        return self._session

    # ------------------------------------------------------------------
    # Authentication (API key)
    # ------------------------------------------------------------------
    def set_api_key(self, api_key: str) -> None:
        """Store the EpidBot API key used on every request."""
        if self.auth_store is not None:
            self.auth_store.set_api_key(api_key)

    def _auth_headers(self) -> Dict[str, str]:
        """Build the authentication header for the configured API key."""
        if self.auth_store is None:
            return {}
        api_key = self.auth_store.get_api_key()
        if not api_key:
            return {}
        return {"X-API-Key": api_key}

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, json_body: Optional[dict] = None,
                 stream_to: Optional[str] = None) -> Any:
        """Perform an API request against ``{base_url}/api/v1{path}``.

        Transient failures (connection errors, timeouts, HTTP 502/503/504)
        are retried automatically with exponential backoff.

        :param stream_to: when set, the response body is streamed to this
            file path and the path is returned instead of parsed JSON.
        :returns: parsed JSON (dict/list), or ``stream_to`` when streaming.
        :raises EpidBotAuthError: on 401/403 (missing/invalid API key).
        :raises EpidBotApiError: on network failures and HTTP errors.
        """
        if not self.base_url:
            raise EpidBotApiError("EpidBot server URL is not configured.")
        url = "{}{}{}".format(self.base_url, API_PREFIX, path)
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method, url, json=json_body, headers=self._auth_headers(),
                    timeout=self.request_timeout, stream=stream_to is not None,
                )
            except requests.exceptions.SSLError as exc:
                # Certificate problems will not fix themselves: never retry.
                raise EpidBotApiError(
                    "SSL error contacting EpidBot at {}: {}".format(url, exc)
                ) from exc
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                if attempt + 1 < attempts:
                    self._sleep_backoff(attempt)
                    continue
                if isinstance(exc, requests.exceptions.Timeout):
                    raise EpidBotApiError(
                        "Request to EpidBot timed out: {}".format(url)
                    ) from exc
                raise EpidBotApiError(
                    "Could not reach EpidBot at {}. Check the server URL "
                    "and your connection.".format(url)) from exc
            if (response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt + 1 < attempts):
                self._sleep_backoff(attempt)
                continue
            break

        if stream_to is not None:
            if response.status_code >= 400:
                raise EpidBotApiError(
                    "Download failed with HTTP {} for {}".format(
                        response.status_code, url),
                    status_code=response.status_code)
            with open(stream_to, "wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        handle.write(chunk)
            return stream_to

        if response.status_code >= 400:
            detail = self._error_detail(response)
            exc_class = EpidBotAuthError if response.status_code in (401, 403) \
                else EpidBotApiError
            raise exc_class(
                "EpidBot API error (HTTP {}) on {} {}: {}".format(
                    response.status_code, method, path, detail),
                status_code=response.status_code, detail=detail)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise EpidBotApiError(
                "EpidBot returned a non-JSON response for {}".format(path)) from exc

    def _sleep_backoff(self, attempt: int) -> None:
        """Wait before retry ``attempt`` (0-based); doubles each time."""
        time.sleep(self.retry_base_delay * (2 ** attempt))

    @staticmethod
    def _error_detail(response) -> str:
        """Extract a human-readable message from an error response."""
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "").strip()[:300] or "unknown error"
        if isinstance(payload, dict):
            return str(payload.get("detail") or payload.get("message") or payload)
        return str(payload)

    # ------------------------------------------------------------------
    # High-level API operations
    # ------------------------------------------------------------------
    def test_connection(self) -> Dict[str, Any]:
        """Verify the API key against ``GET /auth/me``; returns user info."""
        return self._request("GET", "/auth/me")

    def list_sessions(self) -> List[dict]:
        """List the user's chat sessions (newest first per API ordering)."""
        return self._request("GET", "/sessions")

    def create_session(self, name: Optional[str] = None) -> dict:
        """Create a named chat session."""
        return self._request("POST", "/sessions", json_body={"name": name})

    def get_messages(self, session_id: int) -> Dict[str, Any]:
        """Fetch the message history of a session."""
        return self._request("GET", "/sessions/{}/messages".format(session_id))

    def send_message(self, message: str, session_id: Optional[int] = None,
                     locale: str = "en", cancel_event=None,
                     on_status: Optional[Callable[[str], None]] = None,
                     on_submitted: Optional[Callable[[str, int], None]] = None,
                     ) -> Dict[str, Any]:
        """Send a chat message and wait for the agent's answer.

        Uses the submit + poll pattern: ``POST /chat`` returns a job id,
        then ``GET /chat/{job_id}`` is polled until completion. A missing
        ``session_id`` makes EpidBot create the session server-side.

        :param cancel_event: optional ``threading.Event``; when set, polling
            stops and :class:`ChatCancelled` is raised.
        :param on_status: progress callback receiving human-readable status.
        :param on_submitted: callback with ``(job_id, session_id)`` once the
            job is accepted.
        :returns: dict with ``session_id``, ``content``, ``images`` (list of
            relative plot paths) and ``error`` (always None on success).
        :raises ChatCancelled: when cancelled by the caller.
        :raises EpidBotApiError: on submission/polling failures or when the
            job ends in the ``failed`` state.
        """
        submitted = self._request(
            "POST", "/chat",
            json_body={"message": message, "session_id": session_id,
                       "locale": locale},
        )
        job_id = submitted["job_id"]
        effective_session = submitted.get("session_id")
        if on_submitted is not None:
            on_submitted(job_id, effective_session)

        deadline = time.monotonic() + self.chat_timeout
        wait = self.poll_interval
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ChatCancelled("Chat request cancelled.")
            if time.monotonic() > deadline:
                raise EpidBotApiError(
                    "EpidBot did not answer within {} seconds.".format(
                        int(self.chat_timeout)))
            if on_status is not None:
                on_status(self._status_label(job_id))

            status = self._request("GET", "/chat/{}".format(job_id))
            state = status.get("status")
            if state == JOB_COMPLETED:
                return {
                    "session_id": status.get("session_id", effective_session),
                    "content": status.get("content") or "",
                    "images": status.get("images") or [],
                    "error": None,
                }
            if state in (JOB_FAILED, JOB_CANCELLED):
                raise EpidBotApiError(
                    status.get("error") or "The chat job failed on the server.",
                    status_code=None, detail=state)
            time.sleep(wait)
            wait = min(wait * 1.15, self.poll_max_interval)

    @staticmethod
    def _status_label(job_id: str) -> str:
        """Short status text shown while polling."""
        return "EpidBot is thinking... (job {})".format(job_id[:8])

    @staticmethod
    def extract_plot_filename(image_ref: str) -> str:
        """Turn an image reference (``plots/foo.png`` or a full URL) into the
        bare filename served by ``GET /sessions/plots/{filename}``."""
        ref = (image_ref or "").strip()
        if "://" in ref:
            ref = ref.split("?", 1)[0].rsplit("/", 1)[-1]
        for prefix in ("plots/", "/plots/", "plots\\\\"):
            if ref.startswith(prefix):
                ref = ref[len(prefix):]
        return ref.rsplit("/", 1)[-1]

    def download_plot(self, filename: str, dest_path: str) -> str:
        """Download an agent-generated plot to ``dest_path`` (auth included).

        :returns: ``dest_path`` for chaining.
        """
        return self._request(
            "GET", "/sessions/plots/{}".format(filename), stream_to=dest_path)
