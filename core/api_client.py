"""HTTP client for the EpidBot REST API.

Pure Python (no Qt) so it can be unit-tested outside QGIS. Uses
``requests``, which is bundled with QGIS on all supported platforms.

Talks to the documented EpidBot surface:

- ``POST /api/v1/auth/login`` / ``POST /api/v1/auth/refresh`` - JWT auth.
- ``POST /api/v1/chat`` / ``GET /api/v1/chat/{job_id}`` - submit + poll chat.
- ``GET/POST /api/v1/sessions``, ``GET /api/v1/sessions/{id}/messages``.
- ``GET /api/v1/sessions/plots/{filename}`` - authenticated plot download.
- ``GET /api/v1/auth/me`` - connection test.

Both authentication modes accepted by the backend are supported:
``Authorization: Bearer <jwt>`` and ``X-API-Key: <key>``.
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

API_PREFIX = "/api/v1"

AUTH_MODE_PASSWORD = "password"
AUTH_MODE_API_KEY = "api_key"

# Chat job statuses (mirrors epidbot.api.schemas.JobStatus)
JOB_PENDING = "pending"
JOB_PROCESSING = "processing"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"


class EpidBotApiError(Exception):
    """Base error for all EpidBot API interactions."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 detail: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class EpidBotAuthError(EpidBotApiError):
    """Raised when authentication fails or credentials are missing."""


class ChatCancelled(Exception):
    """Raised inside :meth:`EpidBotApiClient.send_message` when the caller
    cancels a pending chat job via the cancellation event."""


class EpidBotApiClient:
    """Thread-safe client for the EpidBot REST API.

    The client is used from worker threads (one chat job at a time); a lock
    guards token refresh so concurrent 401s do not race.
    """

    def __init__(self, base_url: str, auth_store=None,
                 poll_interval: float = 1.5,
                 poll_max_interval: float = 5.0,
                 request_timeout: float = 30.0,
                 chat_timeout: float = 600.0):
        """Create a client.

        :param base_url: EpidBot base URL, e.g. ``https://kwar.example``.
        :param auth_store: optional :class:`core.auth_store.AuthStore` used
            to persist and retrieve tokens/secrets.
        :param poll_interval: seconds between job-status polls.
        :param poll_max_interval: cap for the poll backoff.
        :param request_timeout: per-HTTP-request timeout in seconds.
        :param chat_timeout: overall deadline for a chat job in seconds.
        """
        self.base_url = self._normalize_base_url(base_url)
        self.auth_store = auth_store
        self.poll_interval = poll_interval
        self.poll_max_interval = poll_max_interval
        self.request_timeout = request_timeout
        self.chat_timeout = chat_timeout
        self._session: Optional[requests.Session] = None
        self._refresh_lock = threading.Lock()

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
    # Authentication
    # ------------------------------------------------------------------
    @property
    def auth_mode(self) -> str:
        if self.auth_store is not None:
            return self.auth_store.auth_mode
        return AUTH_MODE_PASSWORD

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate with username/password and store the JWT pair.

        :returns: the parsed login response (contains ``expires_in`` and
            ``tos_acceptance_required``).
        :raises EpidBotAuthError: on invalid credentials.
        """
        data = self._request(
            "POST", "/auth/login",
            json_body={"username": username, "password": password},
            # No Authorization header on login itself.
            skip_auth=True,
        )
        if self.auth_store is not None:
            self.auth_store.set_access_token(data["access_token"])
            self.auth_store.set_refresh_token(data["refresh_token"])
            self.auth_store.username = username
        return data

    def set_api_key(self, api_key: str) -> None:
        """Switch to API-key authentication and persist the key."""
        if self.auth_store is not None:
            self.auth_store.auth_mode = AUTH_MODE_API_KEY
            self.auth_store.set_api_key(api_key)

    def _refresh_access_token(self) -> bool:
        """Try to obtain a fresh access token using the refresh token.

        :returns: True when refreshed; False when no refresh token exists.
        :raises EpidBotAuthError: when the refresh endpoint rejects the token.
        """
        if self.auth_store is None or not self.auth_store.get_refresh_token():
            return False
        with self._refresh_lock:
            # Another thread may have refreshed while we waited.
            data = self._request(
                "POST", "/auth/refresh",
                json_body={"refresh_token": self.auth_store.get_refresh_token()},
                skip_auth=True,
            )
            self.auth_store.set_access_token(data["access_token"])
        return True

    def _auth_headers(self) -> Dict[str, str]:
        """Build authentication headers for the current mode."""
        headers: Dict[str, str] = {}
        if self.auth_store is None:
            return headers
        if self.auth_mode == AUTH_MODE_API_KEY:
            api_key = self.auth_store.get_api_key()
            if api_key:
                headers["X-API-Key"] = api_key
            return headers
        access_token = self.auth_store.get_access_token()
        if access_token:
            headers["Authorization"] = "Bearer {}".format(access_token)
        return headers

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, json_body: Optional[dict] = None,
                 skip_auth: bool = False, stream_to: Optional[str] = None,
                 _retry_auth: bool = True) -> Any:
        """Perform an API request against ``{base_url}/api/v1{path}``.

        On a 401 (JWT mode only) a single refresh-and-retry is attempted so
        long chat sessions survive access-token expiry transparently.

        :param stream_to: when set, the response body is streamed to this
            file path and the path is returned instead of parsed JSON.
        :returns: parsed JSON (dict/list), or ``stream_to`` when streaming.
        """
        if not self.base_url:
            raise EpidBotApiError("EpidBot server URL is not configured.")
        url = "{}{}{}".format(self.base_url, API_PREFIX, path)
        headers = {} if skip_auth else self._auth_headers()
        try:
            response = self.session.request(
                method, url, json=json_body, headers=headers,
                timeout=self.request_timeout, stream=stream_to is not None,
            )
        except requests.exceptions.SSLError as exc:
            raise EpidBotApiError(
                "SSL error contacting EpidBot at {}: {}".format(url, exc)) from exc
        except requests.exceptions.ConnectionError as exc:
            raise EpidBotApiError(
                "Could not reach EpidBot at {}. Check the server URL and "
                "your connection.".format(url)) from exc
        except requests.exceptions.Timeout as exc:
            raise EpidBotApiError(
                "Request to EpidBot timed out: {}".format(url)) from exc

        if response.status_code == 401 and _retry_auth and not skip_auth \
                and self.auth_mode == AUTH_MODE_PASSWORD:
            try:
                refreshed = self._refresh_access_token()
            except EpidBotApiError as exc:
                raise EpidBotAuthError(
                    "Session expired and could not be renewed: {}".format(exc),
                    status_code=401) from exc
            if refreshed:
                return self._request(method, path, json_body=json_body,
                                     skip_auth=skip_auth, stream_to=stream_to,
                                     _retry_auth=False)
            raise EpidBotAuthError(
                "Authentication required. Please log in again.", status_code=401)

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
        """Verify credentials against ``GET /auth/me``; returns user info."""
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
                     locale: str = "en", cancel_event: Optional[threading.Event] = None,
                     on_status: Optional[Callable[[str], None]] = None,
                     on_submitted: Optional[Callable[[str, int], None]] = None,
                     ) -> Dict[str, Any]:
        """Send a chat message and wait for the agent's answer.

        Uses the submit + poll pattern: ``POST /chat`` returns a job id,
        then ``GET /chat/{job_id}`` is polled until completion. A missing
        ``session_id`` makes EpidBot create the session server-side.

        :param cancel_event: when set, polling stops and
            :class:`ChatCancelled` is raised.
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
