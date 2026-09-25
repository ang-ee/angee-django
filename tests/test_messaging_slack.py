"""Slack poll bridge tests: identity, paging, media, ingest policy, and connect."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from email.message import Message as HttpHeaders
from io import BytesIO
from time import monotonic
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from urllib.error import HTTPError
from urllib.request import Request

import httpx
import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry import RetryHandler

from angee.integrate.credentials import CredentialKind
from angee.integrate.http import HttpClient
from angee.integrate.live import PairingState
from angee.integrate.streams import StreamPage, advance_stream
from angee.messaging.backends import ChannelBackend, ParsedMessage, body_part
from angee.messaging.session import LiveChannelSession
from angee.messaging_integrate_imap.backend import ImapChannelBackend
from angee.messaging_integrate_slack.backend import SlackChannelBackend, SlackRateLimitError
from angee.messaging_integrate_slack.identity import parsed_message
from tests.conftest import Credential, Vendor, _clear_model_tables, _create_missing_tables, make_integration
from tests.integrate_models import RECORD_SYNC_TEST_MODELS, RecordLink, SyncStream
from tests.stream_adapters import AdapterPages
from tests.test_messaging import MESSAGING_TEST_MODELS, Message, Part, Thread
from tests.test_messaging_graphql import Channel, _platform_admin

SLACK_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel, *RECORD_SYNC_TEST_MODELS)


class _CredentialStub:
    def secret_value(self) -> str:
        return "xoxp-user-token"


class _BridgeStub:
    def __init__(self, *, config: dict[str, Any] | None = None, cursor: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.cursor: dict[str, Any] = cursor if cursor is not None else {}
        self.credential = _CredentialStub()
        self._state = SimpleNamespace(adding=False, db="default")
        self.subscription_state = {"team_id": "T1", "own_id": "U0"}

    def fresh_credential(self) -> Any:
        """Supply the integration credential contract without a database."""

        return self.credential


class FakeWebClient:
    """Paginated Slack fixture whose history contains parents, never replies."""

    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    def __init__(self, *, token: str, retry_handlers: list[RetryHandler] | None = None) -> None:
        assert token == "xoxp-user-token"

    def users_conversations(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("users.conversations", kwargs))
        if kwargs.get("cursor") == "conversations-2":
            return {
                "ok": True,
                "channels": [{"id": "D1", "is_im": True, "user": "U2"}],
                "response_metadata": {"next_cursor": ""},
            }
        return {
            "ok": True,
            "channels": [{"id": "C1", "name": "general", "is_channel": True}],
            "response_metadata": {"next_cursor": "conversations-2"},
        }

    def conversations_history(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("conversations.history", kwargs))
        channel = kwargs["channel"]
        if channel == "D1":
            return {
                "ok": True,
                "messages": [{"ts": "200.000001", "user": "U0", "text": "Own DM"}],
                "response_metadata": {"next_cursor": ""},
            }
        if kwargs.get("cursor") == "history-2":
            return {
                "ok": True,
                "messages": [
                    {
                        "ts": "100.000001",
                        "thread_ts": "100.000001",
                        "reply_count": 2,
                        "user": "U1",
                        "text": "Parent",
                    }
                ],
                "response_metadata": {"next_cursor": ""},
            }
        return {
            "ok": True,
            "messages": [
                {"ts": "104.000001", "user": "U1", "subtype": "channel_join", "text": "joined"},
                {"ts": "103.000001", "user": "U1", "text": "After thread"},
            ],
            "response_metadata": {"next_cursor": "history-2"},
        }

    def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("conversations.replies", kwargs))
        if kwargs.get("cursor") == "replies-2":
            return {
                "ok": True,
                "messages": [{"ts": "102.000001", "thread_ts": "100.000001", "user": "U2", "text": "Reply two"}],
                "response_metadata": {"next_cursor": ""},
            }
        return {
            "ok": True,
            "messages": [
                {
                    "ts": "100.000001",
                    "thread_ts": "100.000001",
                    "reply_count": 2,
                    "user": "U1",
                    "text": "Parent",
                },
                {"ts": "101.000001", "thread_ts": "100.000001", "user": "U2", "text": "Reply one"},
            ],
            "response_metadata": {"next_cursor": "replies-2"},
        }

    def users_list(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("users.list", kwargs))
        if kwargs.get("cursor") == "users-2":
            return {
                "ok": True,
                "members": [{"id": "U2", "name": "grace", "profile": {"display_name": "Grace"}}],
                "response_metadata": {"next_cursor": ""},
            }
        return {
            "ok": True,
            "members": [
                {"id": "U0", "name": "ada", "profile": {"display_name": "Ada"}},
                {"id": "U1", "name": "linus", "profile": {"real_name": "Linus"}},
            ],
            "response_metadata": {"next_cursor": "users-2"},
        }


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    client_class: type[Any] = FakeWebClient,
    *,
    bridge: _BridgeStub | None = None,
    config: dict[str, Any] | None = None,
) -> SlackChannelBackend:
    monkeypatch.setattr(SlackChannelBackend, "client_class", client_class)
    backend = SlackChannelBackend(bridge or _BridgeStub(config=config))
    backend.test_pages = AdapterPages(backend)
    return backend


def test_slack_serial_drain_discovers_and_caches_workspace_lists_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One serial backend reuses discovery/users while paging history and replies."""

    FakeWebClient.calls = []
    backend = _backend(monkeypatch, config={"batch_size": 2})
    assert backend.sync_parallelism == 1
    assert FakeWebClient.calls == []

    batches: list[list[ParsedMessage]] = []
    while batch := backend.test_pages.next_batch():
        batches.append(batch)

    messages = [message for batch in batches for message in batch]
    assert all(len(batch) <= 2 for batch in batches)
    assert [message.external_id for message in messages] == [
        "C1/103.000001",
        "C1/100.000001",
        "C1/101.000001",
        "C1/102.000001",
        "D1/200.000001",
    ]
    assert {message.thread.external_id for message in messages[:4] if message.thread} == {"C1"}
    assert {message.in_reply_to for message in messages} == {""}
    direct = messages[-1]
    assert direct.direction == "outbound"
    assert direct.thread is not None
    assert direct.thread.modality == "direct"
    assert direct.thread.title == "Grace"
    parent = messages[1]
    assert parent.sender is not None
    assert parent.sender.external_id == "T1:U1"
    assert parent.sender.display_name == "Linus"
    assert messages[2].metadata["thread_ts"] == "100.000001"
    assert backend.test_pages.cursors == {
        "C1": {"conversation": {"last_ts": "104.000001"}, "threads": {"100.000001": "102.000001"}},
        "D1": {"conversation": {"last_ts": "200.000001"}},
    }
    assert backend.bridge.cursor == {}

    names = [name for name, _kwargs in FakeWebClient.calls]
    assert names.count("users.conversations") == 2
    assert names.count("conversations.history") == 3
    assert names.count("conversations.replies") == 2
    assert names.count("users.list") == 2  # one paginated cache, reused by D1
    for name, kwargs in FakeWebClient.calls:
        if name in {"users.conversations", "conversations.history", "conversations.replies", "users.list"}:
            assert int(kwargs["limit"]) <= 200
        if name == "conversations.history":
            assert kwargs["oldest"]


def test_extract_persists_page_resume_before_history_watermark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded newest-first page resumes after a fresh backend without skipping older history."""

    FakeWebClient.calls = []
    bridge = _BridgeStub(config={"batch_size": 2})
    first_backend = _backend(monkeypatch, bridge=bridge)

    first = first_backend.test_pages.next_batch()

    assert [message.external_id for message in first] == ["C1/103.000001"]
    stream = first_backend.test_pages.rows["C1"]
    history_cursor = stream.cursor["conversation"]["history"]
    assert history_cursor["cursor"] == "history-2"
    assert history_cursor["oldest"]
    assert history_cursor["last_ts"] == "104.000001"

    resumed_backend = _backend(monkeypatch, bridge=bridge)
    page = resumed_backend.extract(stream, 200)
    second = page.records

    assert [message.external_id for message in second] == ["C1/100.000001"]
    assert page.cursor["conversation"] == {"last_ts": "104.000001"}
    c1_history = [
        kwargs for name, kwargs in FakeWebClient.calls if name == "conversations.history" and kwargs["channel"] == "C1"
    ]
    assert c1_history[-1]["cursor"] == "history-2"


@pytest.mark.django_db(transaction=True)
def test_invalid_history_cursor_retains_watermarks_across_generation(
    slack_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One expired page resumes at its committed history and thread watermarks."""

    class ExpiredHistoryClient(FakeWebClient):
        calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []

        def conversations_history(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("conversations.history", kwargs))
            if kwargs.get("cursor") == "expired-page":
                response = SimpleNamespace(status_code=200, data={"ok": False, "error": "invalid_cursor"})
                raise SlackApiError("History page expired", response)
            return {"messages": []}

        def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("conversations.replies", kwargs))
            return {"messages": []}

    monkeypatch.setattr(SlackChannelBackend, "client_class", ExpiredHistoryClient)
    monkeypatch.setattr(SlackChannelBackend, "_backfill_floor", lambda self: "0")
    channel = _slack_channel("slack-invalid-history-cursor")
    cursor = {
        "conversation": {
            "last_ts": "100.000001",
            "history": {"cursor": "expired-page", "oldest": "100.000001", "last_ts": "104.000001"},
        },
        "threads": {"99.000001": "101.000001"},
    }
    with system_context(reason="test slack invalid page cursor"):
        stream = SyncStream.objects.current(channel, "messages", "C1", cursor=cursor)
        other = SyncStream.objects.current(
            channel,
            "messages",
            "D1",
            cursor={"conversation": {"last_ts": "200.000001"}, "threads": {"199.000001": "201.000001"}},
        )
        other_cursor = other.cursor
        backend = SlackChannelBackend(channel)
        try:
            reset = advance_stream(stream, backend)
            assert reset.stream.generation == stream.generation + 1
            assert reset.stream.cursor == {
                "conversation": {"last_ts": "100.000001"},
                "threads": {"99.000001": "101.000001"},
            }
            resumed = advance_stream(reset.stream, backend)
            assert resumed.exhausted is True
            assert resumed.stream.cursor == reset.stream.cursor
        finally:
            backend.close()
        other.refresh_from_db()
        assert other.cursor == other_cursor
        assert other.generation == stream.generation
        stream.refresh_from_db()
        assert stream.cursor == cursor
    history = [kwargs for name, kwargs in ExpiredHistoryClient.calls if name == "conversations.history"]
    replies = [kwargs for name, kwargs in ExpiredHistoryClient.calls if name == "conversations.replies"]
    assert history[-1]["cursor"] is None
    assert history[-1]["oldest"] == "100.000001"
    assert replies[-1]["oldest"] == "101.000001"


def test_media_bounded_history_slice_resumes_on_a_fresh_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted partial history page retains a stable within-page boundary."""

    class MediaPageClient(FakeWebClient):
        def conversations_history(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "messages": [
                    {"ts": "101.000001", "user": "U1", "text": "First", "files": [{"size": 4}]},
                    {"ts": "102.000001", "user": "U1", "text": "Second", "files": [{"size": 4}]},
                ]
            }

    backend = _backend(monkeypatch, MediaPageClient, config={"max_batch_bytes": 4})
    monkeypatch.setattr(SlackChannelBackend, "_media", lambda *args, **kwargs: ((), 4))
    first = backend.test_pages.next_batch()
    stream = backend.test_pages.rows["C1"]
    assert [message.external_id for message in first] == ["C1/101.000001"]
    assert stream.cursor["conversation"]["history"]["after_ts"] == "101.000001"
    resumed = _backend(monkeypatch, MediaPageClient, bridge=backend.bridge)
    page = resumed.extract(stream, 200)
    assert [message.external_id for message in page.records] == ["C1/102.000001"]


def test_initial_backfill_uses_bounded_configured_window() -> None:
    """A new workspace starts at 90 days, while an explicit zero starts now."""

    now = datetime.now(tz=UTC).timestamp()
    default_backend = SlackChannelBackend(_BridgeStub())
    current_only_backend = SlackChannelBackend(_BridgeStub(config={"backfill_days": 0}))

    assert now - float(default_backend._oldest()) == pytest.approx(90 * 86_400, abs=2)
    assert now - float(current_only_backend._oldest()) == pytest.approx(0, abs=2)


@pytest.mark.parametrize(
    "subtype",
    ["channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name"],
)
def test_channel_noise_subtypes_are_skipped(subtype: str) -> None:
    """Membership and channel-metadata noise never becomes a chat message."""

    assert (
        parsed_message(
            {"ts": "1.1", "user": "U1", "subtype": subtype, "text": "noise"},
            conversation={"id": "C1", "name": "general"},
            team_id="T1",
            own_id="U0",
            users={},
        )
        is None
    )


@pytest.mark.parametrize("subtype", ["", "me_message", "thread_broadcast", "file_share"])
def test_supported_message_subtypes_ingest(subtype: str) -> None:
    """Normal and the three Slack message-bearing subtypes cross the identity boundary."""

    raw = {"ts": "1.1", "user": "U1", "text": "message"}
    if subtype:
        raw["subtype"] = subtype
    message = parsed_message(
        raw,
        conversation={"id": "C1", "name": "general"},
        team_id="T1",
        own_id="U0",
        users={},
    )
    assert message is not None
    assert message.external_id == "C1/1.1"


def test_edited_message_uses_current_text() -> None:
    """A message_changed wrapper lands the nested current revision, not old copy."""

    message = parsed_message(
        {
            "subtype": "message_changed",
            "ts": "2.0",
            "message": {
                "ts": "1.1",
                "user": "U1",
                "text": "current text",
                "edited": {"user": "U1", "ts": "2.0"},
            },
            "previous_message": {"ts": "1.1", "user": "U1", "text": "old text"},
        },
        conversation={"id": "C1", "name": "general"},
        team_id="T1",
        own_id="U0",
        users={},
    )
    assert message is not None
    assert message.body is not None
    assert message.body.text == "current text"
    assert message.metadata["subtype"] == "message_changed"
    assert message.metadata["edited"] == {"user": "U1", "ts": "2.0"}


def test_media_download_uses_bearer_and_failed_files_get_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only accepted messages resolve files; missing files remain visible markers."""

    calls: list[str] = []

    class MediaWebClient:
        def __init__(self, *, token: str, retry_handlers: list[RetryHandler] | None = None) -> None:
            assert token == "xoxp-user-token"

        def users_conversations(self, **_kwargs: Any) -> dict[str, Any]:
            return {"channels": [{"id": "C1", "name": "files"}]}

        def conversations_history(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "messages": [
                    {
                        "ts": "1.0",
                        "user": "U1",
                        "subtype": "channel_join",
                        "text": "joined",
                        "files": [
                            {
                                "name": "noise.bin",
                                "size": 3,
                                "url_private": "https://files.slack.com/noise.bin",
                            }
                        ],
                    },
                    {
                        "ts": "1.1",
                        "user": "U1",
                        "subtype": "file_share",
                        "files": [
                            {
                                "name": "report.pdf",
                                "mimetype": "application/pdf",
                                "size": 3,
                                "url_private": "https://files.slack.com/report.pdf",
                            },
                            {"name": "missing.png", "mimetype": "image/png", "size": 4},
                        ],
                    },
                ]
            }

        def users_list(self, **_kwargs: Any) -> dict[str, Any]:
            return {"members": [{"id": "U1", "name": "linus"}]}

    backend = _backend(monkeypatch, MediaWebClient)

    def download(file: Mapping[str, Any], *, cap: int | None = None) -> bytes | None:
        del cap
        url = str(file.get("url_private") or "")
        calls.append(url)
        return b"PDF" if url.endswith("report.pdf") else None

    monkeypatch.setattr(backend, "_download_file", download)

    messages = backend.test_pages.next_batch()

    assert len(messages) == 1
    assert calls == ["https://files.slack.com/report.pdf", ""]
    assert "https://files.slack.com/noise.bin" not in calls
    body = messages[0].body
    assert body is not None and body.type == "multipart/mixed"
    assert [part.content for part in body.children] == [b"PDF", None]
    assert body.children[1].text == "[media unavailable: missing.png]"
    assert "_media_facts" not in messages[0].metadata


def test_download_file_stops_streaming_after_the_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown-length response is closed after the cap, without reading its tail."""

    reads = 0

    class CountingStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal reads
            for _index in range(20):
                reads += 1
                yield b"abcd"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer xoxp-user-token"
        return httpx.Response(200, stream=CountingStream())

    def transport(*, allow_private: bool) -> httpx.MockTransport:
        assert allow_private is False
        return httpx.MockTransport(handler)

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(transport))
    backend = SlackChannelBackend(_BridgeStub(config={"max_media_bytes": 5}))
    backend._credential = backend.bridge.credential

    assert backend._download_file({"url_private": "https://files.slack.com/file.bin"}) is None
    assert 0 < reads < 20


@pytest.fixture
def slack_http(monkeypatch: pytest.MonkeyPatch) -> tuple[deque[Exception], list[Request]]:
    """Serve raw HTTP responses through the real SDK's retry machinery."""

    responses: deque[Exception] = deque()
    requests: list[Request] = []

    def urlopen(request: Request, **_kwargs: Any) -> HTTPError:
        requests.append(request)
        response = responses.popleft()
        if not isinstance(response, HTTPError) or response.code >= 400:
            raise response
        return response

    for variable in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("slack_sdk.web.base_client.urlopen", urlopen)
    return responses, requests


def _slack_http_response(status: int, *, retry_after: str = "60") -> HTTPError:
    headers = HttpHeaders()
    headers["Content-Type"] = "application/json"
    headers["Retry-After"] = retry_after
    body = b'{"ok": true, "channels": []}' if status == 200 else b'{"ok": false, "error": "ratelimited"}'
    return HTTPError("https://slack.com/api/users.conversations", status, "Slack response", headers, BytesIO(body))


@pytest.mark.parametrize(("retry_after", "delay"), [("700", 60.0), ("invalid", 1.0), ("-1", 0.0)])
def test_rate_limit_honors_retry_after_then_retries(
    monkeypatch: pytest.MonkeyPatch,
    slack_http: tuple[deque[Exception], list[Request]],
    retry_after: str,
    delay: float,
) -> None:
    """The SDK retries 429 responses with the poll's bounded Retry-After delay."""

    responses, requests = slack_http
    responses.extend([_slack_http_response(429, retry_after=retry_after), _slack_http_response(200)])
    delays: list[float] = []
    monkeypatch.setattr("angee.messaging_integrate_slack.backend.sleep", delays.append)
    backend = _backend(monkeypatch, WebClient)

    assert backend.test_pages.next_batch() == []
    assert len(requests) == 2
    assert requests[0].get_header("Authorization") == "Bearer xoxp-user-token"
    assert delays == [delay]


@pytest.mark.parametrize("discovery", [True, False])
def test_rate_limit_stops_before_the_sync_deadline(
    monkeypatch: pytest.MonkeyPatch,
    slack_http: tuple[deque[Exception], list[Request]],
    discovery: bool,
) -> None:
    """A retry that cannot fit before the drain deadline becomes a transient error."""

    responses, requests = slack_http
    responses.append(_slack_http_response(429))
    delays: list[float] = []
    monkeypatch.setattr("angee.messaging_integrate_slack.backend.sleep", delays.append)
    backend = _backend(monkeypatch, WebClient)
    stream = SimpleNamespace(partition="C1", generation=1, cursor={})

    with pytest.raises(SlackRateLimitError, match="time budget exhausted"):
        if discovery:
            backend.test_pages.next_batch(deadline=monotonic() + 1)
        else:
            backend.extract(stream, 200, deadline=monotonic() + 1)
    assert len(requests) == 1
    assert delays == []


def test_rate_limit_stops_after_retry_budget(
    monkeypatch: pytest.MonkeyPatch, slack_http: tuple[deque[Exception], list[Request]]
) -> None:
    responses, requests = slack_http
    responses.extend(_slack_http_response(429) for _ in range(6))
    delays: list[float] = []
    monkeypatch.setattr("angee.messaging_integrate_slack.backend.sleep", delays.append)
    backend = _backend(monkeypatch, WebClient)

    with pytest.raises(SlackRateLimitError, match="retry budget exhausted"):
        backend.test_pages.next_batch()
    assert len(requests) == 6
    assert delays == [60.0] * 5


@pytest.mark.parametrize("connection_first", [True, False])
def test_rate_limit_and_connection_retries_share_the_sdk_budget(
    monkeypatch: pytest.MonkeyPatch,
    slack_http: tuple[deque[Exception], list[Request]],
    connection_first: bool,
) -> None:
    """Mixed failures consume one native attempt count, reset for each request."""

    responses, requests = slack_http
    connection_error = ConnectionResetError("Connection reset")
    if connection_first:
        responses.append(connection_error)
        responses.extend(_slack_http_response(429) for _ in range(5))
    else:
        responses.extend([_slack_http_response(429), connection_error])
    delays: list[float] = []
    connection_delays: list[float] = []
    monkeypatch.setattr("angee.messaging_integrate_slack.backend.sleep", delays.append)
    monkeypatch.setattr("slack_sdk.http_retry.handler.time.sleep", connection_delays.append)
    backend = _backend(monkeypatch, WebClient)

    with pytest.raises(SlackRateLimitError if connection_first else ConnectionResetError):
        backend.streams()
    assert len(requests) == (6 if connection_first else 2)
    assert delays == [60.0] * (4 if connection_first else 1)
    assert len(connection_delays) == (1 if connection_first else 0)

    responses.extend([_slack_http_response(429), _slack_http_response(200)])
    assert backend.streams() == ()
    assert delays[-1] == 60.0


def test_retry_deadline_survives_client_access_and_is_restored(
    monkeypatch: pytest.MonkeyPatch, slack_http: tuple[deque[Exception], list[Request]]
) -> None:
    """Reading the client preserves an explicit deadline, which ends with the scope."""

    responses, requests = slack_http
    responses.append(_slack_http_response(429))
    delays: list[float] = []
    monkeypatch.setattr("angee.messaging_integrate_slack.backend.sleep", delays.append)
    backend = _backend(monkeypatch, WebClient)
    backend._load_credentials()

    with pytest.raises(SlackRateLimitError, match="time budget exhausted"):
        with backend._rate_limit_retry.deadline(monotonic() + 1):
            backend._client_or_create().users_conversations()
    assert len(requests) == 1
    assert delays == []

    responses.extend([_slack_http_response(429), _slack_http_response(200)])
    assert backend._client_or_create().users_conversations()["ok"] is True
    assert len(requests) == 3
    assert delays == [60.0]


def test_poll_and_live_paths_read_backend_ingest_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll drain and live ingest read backend quote policy; the kind stays owner-derived.

    ``quote_edges`` is the one per-backend ingest declaration left; the functional
    kind is decided inside ``Message.objects.ingest`` from structural facts, so
    neither path may pass a kind.
    """

    calls: list[dict[str, Any]] = []
    edge_batches: list[list[Any]] = []

    class IngestManager:
        def ingest(self, batch: list[ParsedMessage], **kwargs: Any) -> list[ParsedMessage]:
            calls.append(kwargs)
            return batch

        def resolve_ingest_edges(self, messages: list[Any]) -> None:
            edge_batches.append(messages)

    message_model = SimpleNamespace(objects=IngestManager())
    monkeypatch.setattr("angee.messaging.backends.apps.get_model", lambda *_args: message_model)

    def drain(backend_class: type[ChannelBackend]) -> None:
        backend = backend_class(_BridgeStub())
        page = StreamPage(records=[ParsedMessage(external_id="one", platform="test", body=body_part("one"))], cursor={})
        outcomes = tuple(backend.apply_record(None, record) for record in page.records)
        assert len(outcomes) == 1
        backend.finish_page(None, page, outcomes)

    drain(SlackChannelBackend)
    drain(ImapChannelBackend)

    assert calls[0]["quote_edges"] is False
    assert calls[1]["quote_edges"] is False
    assert len(edge_batches) == 1
    assert all("message_kind" not in call for call in calls)

    calls.clear()
    monkeypatch.setattr("angee.messaging.session.apps.get_model", lambda *_args: message_model)

    class TestLiveSession(LiveChannelSession):
        def _with_media(self, message: Any, _payload: Any) -> Any:
            return message

        def _after_ingest(self, _batch: list[tuple[Any, Any]], _landed: list[Any]) -> None:
            pass

        def _report(self, state: PairingState, **pairing: Any) -> None:
            del state, pairing

        def _still_wanted(self) -> bool:
            return True

    session = TestLiveSession.__new__(TestLiveSession)
    session.live_impl = cast(
        Any,
        SimpleNamespace(
            quote_edges=True,
            parse_live_message=lambda message: message,
        ),
    )
    session.bridge = object()
    session.landed = 0
    session.pairing = PairingState.PAIRED

    assert session._ingest([(ParsedMessage(external_id="live", platform="test"), None)]) is True
    assert calls[0]["quote_edges"] is True
    assert "message_kind" not in calls[0]


@pytest.fixture
def slack_tables() -> Iterator[None]:
    """Create the concrete messaging graph and Slack Channel child on demand."""

    created_models = _create_missing_tables(SLACK_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(SLACK_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


class IncrementalWebClient:
    """Real-shaped Slack fake: history has roots; replies come only from replies."""

    history_calls: ClassVar[list[dict[str, Any]]] = []
    history_responses: ClassVar[list[list[dict[str, Any]]]] = []
    reply_calls: ClassVar[list[dict[str, Any]]] = []
    top_level_messages: ClassVar[list[dict[str, Any]]] = [
        {"ts": "1784700000.000001", "user": "U1", "text": "Parent"},
        {"ts": "1784700002.000001", "user": "U1", "text": "Newer root"},
    ]
    replies: ClassVar[list[dict[str, Any]]] = [
        {
            "ts": "1784700001.000001",
            "thread_ts": "1784700000.000001",
            "user": "U2",
            "text": "Reply",
        },
    ]

    def __init__(self, *, token: str, retry_handlers: list[RetryHandler] | None = None) -> None:
        assert token == "xoxp-user-token"

    def users_conversations(self, **_kwargs: Any) -> dict[str, Any]:
        return {"channels": [{"id": "C1", "name": "general", "is_channel": True}]}

    def conversations_history(self, **kwargs: Any) -> dict[str, Any]:
        self.history_calls.append(kwargs)
        oldest = Decimal(str(kwargs["oldest"]))
        messages: list[dict[str, Any]] = []
        for raw in self.top_level_messages:
            if Decimal(raw["ts"]) <= oldest:
                continue
            item = dict(raw)
            if item["ts"] == "1784700000.000001":
                item.update(thread_ts=item["ts"], reply_count=len(self.replies))
            messages.append(item)
        messages.sort(key=lambda item: Decimal(item["ts"]), reverse=True)
        self.history_responses.append(messages)
        return {"messages": messages}

    def conversations_replies(self, **kwargs: Any) -> dict[str, Any]:
        self.reply_calls.append(kwargs)
        oldest = Decimal(str(kwargs["oldest"]))
        parent = {
            "ts": "1784700000.000001",
            "thread_ts": "1784700000.000001",
            "reply_count": len(self.replies),
            "user": "U1",
            "text": "Parent",
        }
        replies = [item for item in self.replies if Decimal(item["ts"]) > oldest]
        return {"messages": [parent, *replies]}

    def users_list(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "members": [
                {"id": "U1", "name": "linus", "profile": {"display_name": "Linus"}},
                {"id": "U2", "name": "grace", "profile": {"display_name": "Grace"}},
            ]
        }


def _slack_channel(slug: str = "slack") -> Any:
    return make_integration(
        slug,
        kind=CredentialKind.STATIC_TOKEN,
        material={"api_key": "xoxp-user-token"},
        model=Channel,
        backend_class="slack",
        subscription_state={"team_id": "T1", "own_id": "U0"},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["rotate", "repoint"])
def test_extract_reloads_credential_and_reused_client_between_pages(slack_tables: None, change: str) -> None:
    """Secret rotation and credential replacement both update the retained client."""

    with system_context(reason="tests.slack.credential_freshness"):
        channel = _slack_channel("slack-freshness")
        backend = SlackChannelBackend(channel)
        stream = SimpleNamespace(partition="C1", generation=1)
        backend._stream_identity = (stream.partition, stream.generation)
        backend._work = deque()
        assert backend.extract(stream, 1).exhausted
        cached = backend.bridge.credential
        client = backend._client_or_create()
        assert client.token == "xoxp-user-token"

        if change == "rotate":
            current = Credential.objects.get(pk=cached.pk)
            current.update_material(api_key="xoxp-rotated-token")
        else:
            current = Credential.objects.create_local_credential(
                channel.owner,
                kind=CredentialKind.STATIC_TOKEN,
                name="Replacement Slack credential",
                material={"api_key": "xoxp-rotated-token"},
            )
            Channel._base_manager.filter(pk=channel.pk).update(credential=current)
        assert backend.bridge.credential is cached
        assert cached.secret_value() == "xoxp-user-token"
        assert client.token == "xoxp-user-token"

        assert backend.extract(stream, 1).exhausted
        assert backend._credential.pk == current.pk
        assert backend._credential.secret_value() == "xoxp-rotated-token"
        assert backend._client_or_create() is client
        assert client.token == "xoxp-rotated-token"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("existing_stream", [False, True])
def test_legacy_slack_position_seeds_only_an_empty_stream(
    slack_tables: None, monkeypatch: pytest.MonkeyPatch, existing_stream: bool
) -> None:
    """The first stream continues the retained conversation watermark without re-import."""

    monkeypatch.setattr(SlackChannelBackend, "client_class", IncrementalWebClient)
    channel = _slack_channel("slack-legacy")
    seeds: list[str] = []
    seed_cursor = SlackChannelBackend.seed_cursor

    def track_seed(self: Any, stream: Any, legacy_cursor: Any) -> Any:
        seeds.append(stream.partition)
        return seed_cursor(self, stream, legacy_cursor)

    monkeypatch.setattr(SlackChannelBackend, "seed_cursor", track_seed)
    with system_context(reason="test slack legacy stream position"):
        channel.cursor = {"conversations": {"C1": {"last_ts": "1784700000.000001"}}, "threads": {}}
        channel.save(update_fields=["cursor"])
        if existing_stream:
            SyncStream.objects.current(
                channel,
                "messages",
                "C1",
                cursor={
                    "conversation": {"last_ts": "1784700002.000001"},
                    "threads": {},
                },
            )
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == (0 if existing_stream else 1)
        assert seeds == ([] if existing_stream else ["C1"])
        channel.cursor = {"conversations": {"C1": {"last_ts": "1784600000.000001"}}, "threads": {}}
        channel.save(update_fields=["cursor"])
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 0
        assert seeds == ([] if existing_stream else ["C1"])
    assert Message._base_manager.count() == (0 if existing_stream else 1)


@pytest.mark.django_db(transaction=True)
def test_late_reply_below_history_watermark_lands_on_the_next_poll(
    slack_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active thread is rescanned even after its parent falls below history oldest."""

    del slack_tables
    IncrementalWebClient.history_calls = []
    IncrementalWebClient.history_responses = []
    IncrementalWebClient.reply_calls = []
    IncrementalWebClient.replies = [
        {
            "ts": "1784700001.000001",
            "thread_ts": "1784700000.000001",
            "user": "U2",
            "text": "Reply",
        }
    ]
    monkeypatch.setattr(SlackChannelBackend, "client_class", IncrementalWebClient)
    channel = _slack_channel("slack-incremental")

    with system_context(reason="test slack incremental sync"):
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 0, tzinfo=UTC)) == 3
        IncrementalWebClient.replies.append(
            {
                "ts": "1784700003.000001",
                "thread_ts": "1784700000.000001",
                "user": "U2",
                "text": "Late reply",
            }
        )
        assert channel.run_sync(now=datetime(2026, 7, 22, 10, 5, tzinfo=UTC)) == 1

    assert Message._base_manager.count() == 4
    assert Thread._base_manager.count() == 1
    thread = Thread._base_manager.get()
    assert thread.external_id == f"chat:{channel.pk}:C1"
    assert thread.modality == Thread.Modality.GROUP
    assert set(Message._base_manager.values_list("message_type", flat=True)) == {Message.MessageKind.CHAT}
    assert Part._base_manager.filter(role=Part.PartRole.BODY).count() == 4
    channel.refresh_from_db()
    assert SyncStream.objects.current(channel, "messages", "C1").cursor == {
        "conversation": {"last_ts": "1784700002.000001"},
        "threads": {"1784700000.000001": "1784700003.000001"},
    }
    assert RecordLink._base_manager.count() == 0
    assert IncrementalWebClient.history_calls[-1]["oldest"] == "1784700002.000001"
    assert IncrementalWebClient.history_responses[-1] == []
    assert IncrementalWebClient.reply_calls[-1]["oldest"] == "1784700001.000001"


@pytest.mark.django_db(transaction=True)
def test_non_rate_limit_api_error_uses_generic_sync_telemetry(
    slack_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack API failures mark runtime telemetry without changing poll lifecycle."""

    del slack_tables

    class FailingWebClient:
        def __init__(self, *, token: str, retry_handlers: list[RetryHandler] | None = None) -> None:
            assert token == "xoxp-user-token"

        def users_conversations(self, **_kwargs: Any) -> dict[str, Any]:
            raise _slack_error(403)

    monkeypatch.setattr(SlackChannelBackend, "client_class", FailingWebClient)
    channel = _slack_channel("slack-failure")

    with system_context(reason="test slack api failure"), pytest.raises(SlackApiError):
        channel.run_sync(now=datetime(2026, 7, 22, 11, 0, tzinfo=UTC))

    channel.refresh_from_db()
    assert channel.lifecycle == "connected"
    assert channel.last_sync_status == "error"
    assert channel.sync_error == "Integration operation failed."


@pytest.mark.django_db(transaction=True)
def test_connect_probes_before_transaction_and_failed_auth_creates_nothing(
    slack_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auth.test failure runs outside atomic and leaves no credential or channel."""

    del slack_tables
    from angee.messaging_integrate_slack.connect import create_slack_channel

    admin = _platform_admin("msg-slack-probe-admin")
    with system_context(reason="test slack vendor seed"):
        Vendor.objects.create(slug="slack", display_name="Slack")
    before_credentials = Credential._base_manager.count()

    class FailingProbeClient:
        def __init__(self, *, token: str, retry_handlers: list[RetryHandler] | None = None) -> None:
            assert token == "xoxp-invalid"

        def auth_test(self) -> dict[str, Any]:
            assert connection.in_atomic_block is False
            raise _slack_error(401)

    monkeypatch.setattr(SlackChannelBackend, "client_class", FailingProbeClient)

    with pytest.raises(ValueError, match="rejected the token or it lacks the required scopes"):
        create_slack_channel(admin, name="Acme", token="xoxp-invalid")

    assert Credential._base_manager.count() == before_credentials
    assert Channel._base_manager.filter(backend_class="slack").count() == 0


@pytest.mark.django_db(transaction=True)
def test_connect_persists_verified_workspace_in_one_write_phase(
    slack_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful auth.test facts become one connected workspace channel."""

    del slack_tables
    from angee.messaging_integrate_slack.connect import create_slack_channel

    admin = _platform_admin("msg-slack-connect-admin")
    with system_context(reason="test slack vendor seed"):
        Vendor.objects.create(slug="slack", display_name="Slack")

    class ProbeClient:
        def __init__(self, *, token: str, retry_handlers: list[RetryHandler] | None = None) -> None:
            assert token == "xoxp-valid"

        def auth_test(self) -> dict[str, Any]:
            assert connection.in_atomic_block is False
            return {"ok": True, "team_id": "T123", "user_id": "U123", "team": "Verified Workspace"}

    monkeypatch.setattr(SlackChannelBackend, "client_class", ProbeClient)
    channel = create_slack_channel(admin, name="Requested label", token="xoxp-valid")

    channel.refresh_from_db()
    assert channel.display_name == "Verified Workspace"
    assert channel.subscription_state == {"team_id": "T123", "own_id": "U123"}
    assert channel.lifecycle == "connected"
    assert channel.credential.kind == CredentialKind.STATIC_TOKEN
    assert channel.credential.secret_value() == "xoxp-valid"


def _slack_error(status: int, headers: Mapping[str, str] | None = None) -> SlackApiError:
    response = SimpleNamespace(
        status_code=status,
        headers=dict(headers or {}),
        data={"ok": False, "error": "ratelimited" if status == 429 else "invalid_auth"},
    )
    return SlackApiError("Slack API failed", response)
