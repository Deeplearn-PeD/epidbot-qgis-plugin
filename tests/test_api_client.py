"""Tests for :mod:`core.api_client` (EpidBotApiClient).

All HTTP traffic is intercepted by patching ``requests.Session`` inside the
module, so no network access happens. Covers API-key authentication, the
chat submit+poll cycle, cancellation, and plot downloads.
"""

import threading
from unittest import mock
from unittest.mock import MagicMock

import pytest

from core import api_client
from core.api_client import (DEFAULT_BASE_URL, EpidBotApiError,
                             EpidBotApiClient, EpidBotAuthError, ChatCancelled)
from core.auth_store import AuthStore


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, json_data=None, content=b"ok",
                 text="", body_chunks=None):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content if content is not None else b"ok"
        self.text = text or (str(json_data) if json_data else "")
        self._body_chunks = body_chunks

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data

    def iter_content(self, chunk_size=65536):
        if self._body_chunks is not None:
            yield from self._body_chunks
        else:
            yield self.content


class FakeSession:
    """Scriptable requests.Session double."""

    def __init__(self, responses, loop_last=False):
        # responses: list of FakeResponse OR callables (method, url, kwargs).
        # With loop_last=True the final response repeats forever, useful for
        # "processing" polls that only end via the client deadline.
        self.responses = list(responses)
        self.loop_last = loop_last
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected request: {} {}".format(
                method, url))
        item = self.responses.pop(0)
        if self.loop_last and not self.responses:
            self.responses.append(item)
        if callable(item):
            return item(method, url, kwargs)
        return item


class FakeBackend:
    """Dict-based QSettings double."""

    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


def make_client(store=None, **kwargs):
    """Build a client with an in-memory AuthStore-backed fake."""
    if store is None:
        store = AuthStore(backend=FakeBackend(), keyring_module="disabled")
    client = EpidBotApiClient(base_url=DEFAULT_BASE_URL, auth_store=store,
                              **kwargs)
    return client, store


class TestUrlAndHeaders:
    """Base URL normalization and API-key header construction."""

    def test_normalize_strips_trailing_slash(self):
        assert EpidBotApiClient._normalize_base_url(
            "https://host.com/") == "https://host.com"

    def test_normalize_adds_scheme(self):
        assert EpidBotApiClient._normalize_base_url(
            " host.com ") == "https://host.com"

    def test_default_base_url_is_the_production_api_host(self):
        assert DEFAULT_BASE_URL == "https://api.epidbot.kwar-ai.com.br"
        client, _ = make_client()
        assert client.base_url == DEFAULT_BASE_URL

    def test_api_key_sent_via_x_api_key_header(self):
        client, store = make_client()
        store.set_api_key("ek_test123")
        headers = client._auth_headers()
        assert headers == {"X-API-Key": "ek_test123"}

    def test_every_request_carries_the_api_key(self):
        fake = FakeSession([
            FakeResponse(200, json_data=[]),
            FakeResponse(200, json_data={}),
        ])
        client, store = make_client()
        store.set_api_key("ek_abc")
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            client.list_sessions()
            client.test_connection()
        for call in fake.calls:
            assert call["headers"]["X-API-Key"] == "ek_abc"


class TestAuthenticationErrors:
    """Missing or invalid API keys surface as auth errors."""

    def test_missing_api_key_returns_no_header(self):
        client, _ = make_client()
        assert client._auth_headers() == {}

    def test_401_invalid_key_raises_auth_error(self):
        fake = FakeSession([FakeResponse(401, json_data={"detail": "no"})])
        client, store = make_client()
        store.set_api_key("ek_bad")
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            with pytest.raises(EpidBotAuthError):
                client.list_sessions()
        # No retry machinery: a single request must have been made.
        assert len(fake.calls) == 1

    def test_connection_error_is_wrapped(self):
        import requests as requests_lib

        fake = MagicMock()
        fake.request.side_effect = requests_lib.exceptions.ConnectionError(
            "boom")
        client, _ = make_client()
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            with pytest.raises(EpidBotApiError, match="Could not reach"):
                client.list_sessions()


class TestSendMessage:
    """The submit + poll chat cycle."""

    def _completed_status(self, session_id=7):
        return FakeResponse(200, json_data={
            "job_id": "job-1", "status": "completed", "session_id": session_id,
            "content": "Dengue cases are rising.",
            "images": ["plots/chart1.png"],
            "error": None})

    def test_success_flow_returns_content_and_images(self):
        fake = FakeSession([
            FakeResponse(200, json_data={"job_id": "job-1", "session_id": 7,
                                         "status": "processing"}),
            self._completed_status(),
        ])
        client, _ = make_client(poll_interval=0.001)
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            result = client.send_message("hello", session_id=7)
        assert result == {
            "session_id": 7,
            "content": "Dengue cases are rising.",
            "images": ["plots/chart1.png"],
            "error": None,
        }
        submit = fake.calls[0]
        assert submit["url"].endswith("/api/v1/chat")
        assert submit["json"]["message"] == "hello"
        assert submit["json"]["session_id"] == 7
        poll = fake.calls[1]
        assert poll["url"].endswith("/api/v1/chat/job-1")

    def test_session_auto_creation_is_adopted(self):
        """session_id=None must send null and adopt the created session."""
        fake = FakeSession([
            FakeResponse(200, json_data={"job_id": "job-2", "session_id": 42,
                                         "status": "processing"}),
            FakeResponse(200, json_data={
                "job_id": "job-2", "status": "completed", "session_id": 42,
                "content": "ok", "images": [], "error": None}),
        ])
        client, _ = make_client(poll_interval=0.001)
        seen_submitted = {}
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            result = client.send_message(
                "hi", on_submitted=lambda job, sid:
                    seen_submitted.update(job=job, sid=sid))
        assert fake.calls[0]["json"]["session_id"] is None
        assert result["session_id"] == 42
        assert seen_submitted == {"job": "job-2", "sid": 42}

    def test_failed_job_raises_with_server_error(self):
        fake = FakeSession([
            FakeResponse(200, json_data={"job_id": "job-3", "session_id": 1,
                                         "status": "processing"}),
            FakeResponse(200, json_data={
                "job_id": "job-3", "status": "failed",
                "error": "quota exceeded"}),
        ])
        client, _ = make_client(poll_interval=0.001)
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            with pytest.raises(EpidBotApiError, match="quota exceeded"):
                client.send_message("hi", session_id=1)

    def test_cancel_event_stops_polling(self):
        fake = FakeSession([
            FakeResponse(200, json_data={"job_id": "job-4", "session_id": 1,
                                         "status": "processing"}),
            FakeResponse(200, json_data={
                "job_id": "job-4", "status": "processing"}),
        ])
        client, _ = make_client(poll_interval=10.0)
        cancel = threading.Event()
        cancel.set()
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            with pytest.raises(ChatCancelled):
                client.send_message("hi", session_id=1,
                                    cancel_event=cancel)

    def test_timeout_raises(self):
        processing = FakeResponse(200, json_data={
            "job_id": "job-5", "status": "processing"})
        fake = FakeSession([
            FakeResponse(200, json_data={"job_id": "job-5", "session_id": 1,
                                         "status": "processing"}),
            processing,
        ], loop_last=True)
        client, _ = make_client(poll_interval=0.001, chat_timeout=0.05)
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            with pytest.raises(EpidBotApiError, match="did not answer"):
                client.send_message("hi", session_id=1)


class TestPlotsAndHelpers:
    """Plot filename extraction and authenticated download."""

    def test_extract_plot_filename_variants(self):
        extract = EpidBotApiClient.extract_plot_filename
        assert extract("plots/chart1.png") == "chart1.png"
        assert extract("chart2.svg") == "chart2.svg"
        assert extract("https://host/api/v1/sessions/plots/c3.png?x=1") \
            == "c3.png"

    def test_download_plot_streams_to_file(self, tmp_path):
        payload = b"\x89PNG fake image bytes"
        fake = FakeSession([
            FakeResponse(200, content=payload, body_chunks=[payload[:8],
                                                            payload[8:]]),
        ])
        client, _ = make_client()
        target = tmp_path / "chart.png"
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            result = client.download_plot("chart.png", str(target))
        assert result == str(target)
        assert target.read_bytes() == payload
        assert fake.calls[0]["url"].endswith(
            "/api/v1/sessions/plots/chart.png")

    def test_download_plot_http_error(self, tmp_path):
        fake = FakeSession([FakeResponse(403, json_data={"detail": "denied"})])
        client, _ = make_client()
        with mock.patch.object(api_client.requests, "Session",
                               return_value=fake):
            with pytest.raises(EpidBotApiError, match="403"):
                client.download_plot("chart.png", str(tmp_path / "x.png"))
