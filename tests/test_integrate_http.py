"""Tests for the SSRF-pinned outbound HTTP client (``integrate.http``).

These exercise the real ``PinnedTransport`` / ``_PinnedBackend`` over httpx2. The
backend tests stub only ``socket.getaddrinfo`` (resolution) and httpcore2's raw
dial, so the address gate, the resolve-then-pin (DNS-rebind) protection, the
dial-all fallback, and the not-sent-vs-``ValidationError`` distinction are
asserted against the production path. End-to-end tests confirm those exceptions
survive httpx2. Request and download tests script the network with
``angee.integrate.testing.http.mock_transport`` to assert redirect handling, byte
budgets and the URL's Host header. No external network or database is
used.
"""

from __future__ import annotations

import gzip
import json
import socket
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, assert_type

import httpcore2
import httpx2
import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from pydantic import BaseModel

from angee.base.serialization import MAX_JSON_DOCUMENT_DEPTH
from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import (
    HttpClient,
    HttpRefusal,
    OutboundBudget,
    OutboundBudgetState,
    PinnedTransport,
    ResponseTooLargeError,
    _PinnedBackend,
    _request_headers,
    retry_after,
)
from angee.integrate.net import AddressNotAllowedError, AddressResolutionError, resolved_addresses
from angee.integrate.testing.http import mock_transport

URL = "https://dav.example.test/path?x=1"


def _resolve_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    """Make DNS resolution return the given address(es) for every hostname."""

    def fake_getaddrinfo(hostname: str, port: int | None, *, type: int) -> list[Any]:
        del hostname, type
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, port or 443)) for address in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _record_dials(monkeypatch: pytest.MonkeyPatch, *, behaviors: list[Any] | None = None) -> list[str]:
    """Stub httpcore2's raw dial; record the host dialled and replay behaviors.

    Each call pops one behavior: an ``Exception`` is raised (connection failure),
    anything else returns a stand-in stream (tests do not perform real I/O).
    """

    dialled: list[str] = []
    queue = list(behaviors or [])

    def fake_connect(self: Any, host: str, port: int, **kwargs: Any) -> Any:
        dialled.append(host)
        behavior = queue.pop(0) if queue else "ok"
        if isinstance(behavior, BaseException):
            raise behavior
        return object()

    monkeypatch.setattr(httpcore2.SyncBackend, "connect_tcp", fake_connect)
    return dialled


def test_public_address_is_dialled_at_the_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public host is dialled at the resolved IP (pinned), not a re-resolved host."""

    _resolve_to(monkeypatch, "93.184.216.34")
    dialled = _record_dials(monkeypatch)

    _PinnedBackend(allow_private=False).connect_tcp("dav.example.test", 443)

    assert dialled == ["93.184.216.34"]


@pytest.mark.parametrize("allow_private", [False, True])
@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",  # AWS/GCP metadata
        "169.254.1.1",  # link-local generally
        "100.100.100.200",  # Alibaba metadata (RFC 6598 shared range)
        "224.0.0.1",  # multicast
        "0.0.0.0",  # unspecified
    ],
)
def test_metadata_and_escapes_blocked_in_both_modes(
    monkeypatch: pytest.MonkeyPatch, address: str, allow_private: bool
) -> None:
    """Metadata, link-local, the CGN range, multicast, and unspecified are always rejected — before any dial."""

    _resolve_to(monkeypatch, address)
    dialled = _record_dials(monkeypatch)

    with pytest.raises(AddressNotAllowedError):
        _PinnedBackend(allow_private=allow_private).connect_tcp("h", 443)
    assert dialled == []


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.0.1", "172.16.0.1", "127.0.0.1"])
def test_private_and_loopback_rejected_in_public_mode(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Default (public) mode rejects RFC-1918 and loopback before any dial."""

    _resolve_to(monkeypatch, address)
    dialled = _record_dials(monkeypatch)

    with pytest.raises(AddressNotAllowedError):
        _PinnedBackend(allow_private=False).connect_tcp("h", 443)
    assert dialled == []


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.0.1", "127.0.0.1"])
def test_private_and_loopback_permitted_in_private_mode(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """``allow_private=True`` permits RFC-1918 / loopback (self-hosted connections)."""

    _resolve_to(monkeypatch, address)
    dialled = _record_dials(monkeypatch)

    _PinnedBackend(allow_private=True).connect_tcp("h", 443)

    assert dialled == [address]


def test_dial_falls_back_to_the_next_validated_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first validated IP is unreachable, the next one is tried."""

    _resolve_to(monkeypatch, "93.184.216.34", "93.184.216.35")
    dialled = _record_dials(monkeypatch, behaviors=[httpcore2.ConnectError("down"), "ok"])

    _PinnedBackend(allow_private=False).connect_tcp("h", 443)

    assert dialled == ["93.184.216.34", "93.184.216.35"]


def test_all_addresses_unreachable_are_provably_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every dial failed before any HTTP request bytes were written."""

    _resolve_to(monkeypatch, "93.184.216.34", "93.184.216.35")
    _record_dials(monkeypatch, behaviors=[httpcore2.ConnectError("a"), httpcore2.ConnectError("b")])

    with pytest.raises(RequestNotSentError):
        _PinnedBackend(allow_private=False).connect_tcp("h", 443)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_httpclient_refuses_an_unsafe_host_permanently_before_dialling(
    monkeypatch: pytest.MonkeyPatch, method: str,
) -> None:
    """The pre-connect SSRF refusal is a permanent gate refusal and never dials."""

    _resolve_to(monkeypatch, "169.254.169.254")
    dialled = _record_dials(monkeypatch)

    with pytest.raises(AddressNotAllowedError) as refused:
        HttpClient().request(method, URL)
    assert isinstance(refused.value, ValidationError)
    assert not isinstance(refused.value, RequestNotSentError)
    assert dialled == []


def test_httpclient_surfaces_not_sent_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The typed connect refusal survives the production HTTP stack."""

    _resolve_to(monkeypatch, "93.184.216.34")
    _record_dials(monkeypatch, behaviors=[httpcore2.ConnectError("down")])

    with pytest.raises(RequestNotSentError):
        HttpClient().get(URL)


def test_caller_host_header_is_stripped() -> None:
    """A caller-supplied Host header is removed (case-insensitively) so httpx2 sets the URL host."""

    assert dict(_request_headers({"Host": "evil.example.com", "X-Test": "1"}, capped=False)) == {"x-test": "1"}
    assert dict(_request_headers({"host": "evil.example.com"}, capped=False)) == {}
    assert dict(_request_headers(None, capped=False)) == {}


def test_pinned_transport_installs_the_pinned_backend() -> None:
    """The SSRF pin is actually wired into the transport — a direct guard so a future
    httpx2/httpcore2 rename of the private backend attribute fails the suite loudly rather
    than silently dialling un-pinned."""

    assert isinstance(PinnedTransport(allow_private=False)._pool._network_backend, _PinnedBackend)


@pytest.mark.parametrize("operation", ["request", "download"])
def test_capped_read_stops_streaming_after_the_byte_cap(
    operation: str,
) -> None:
    """An unknown-length response is closed after the cap without reading its tail."""

    reads = 0
    closed = False

    class CountingStream(httpx2.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal reads
            for _index in range(20):
                reads += 1
                yield b"abcd"

        def close(self) -> None:
            nonlocal closed
            closed = True

    headers = {"Authorization": "Bearer token"}
    with mock_transport(lambda request: httpx2.Response(200, stream=CountingStream())) as requests:
        if operation == "request":
            with pytest.raises(ResponseTooLargeError, match="byte limit"):
                HttpClient().request("REPORT", URL, max_bytes=5, headers=headers)
        else:
            assert HttpClient().download_capped(URL, cap=5, headers=headers) is None
    assert requests[0].headers["authorization"] == "Bearer token"
    assert reads < 20
    assert closed


@pytest.mark.parametrize("operation", ["request", "download"])
def test_capped_read_rejects_declared_oversize_before_reading(
    operation: str,
) -> None:
    """Content-Length rejects an oversized response before its stream is consumed."""

    reads = 0

    class CountingStream(httpx2.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal reads
            reads += 1
            yield b"body"

    with mock_transport(lambda request: httpx2.Response(200, headers={"Content-Length": "6"}, stream=CountingStream())):
        if operation == "request":
            with pytest.raises(ResponseTooLargeError, match="byte limit"):
                HttpClient().request("PROPFIND", URL, max_bytes=5)
        else:
            assert HttpClient().download_capped(URL, cap=5) is None
    assert reads == 0


@pytest.mark.parametrize("compressed", [False, True])
def test_capped_request_returns_decoded_body_and_response_facts(
    compressed: bool,
) -> None:
    body = b"<multistatus/>"
    headers = {"ETag": '"version"', "Content-Type": "application/xml"}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    with mock_transport(lambda request: httpx2.Response(
        207,
        headers=headers,
        stream=httpx2.ByteStream(gzip.compress(body) if compressed else body),
        extensions={"http_version": b"HTTP/1.1"},
    )):
        response = HttpClient().request("REPORT", URL, body=b"<sync/>", max_bytes=len(body))
    assert response.content == body
    assert response.status_code == 207
    assert response.headers["etag"] == '"version"'
    assert response.headers["content-type"] == "application/xml"
    assert response.url == URL
    assert response.request.method == "REPORT"
    assert response.request.content == b"<sync/>"
    assert response.http_version == "HTTP/1.1"
    assert response.is_closed
    assert response.elapsed.total_seconds() >= 0


@pytest.mark.parametrize("operation", ["request", "download"])
def test_capped_read_refuses_decoded_compressed_overflow(
    operation: str,
) -> None:
    wire = gzip.compress(b" " * 1000)
    assert len(wire) < 100

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx2.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(wire))},
            stream=httpx2.ByteStream(wire),
        )

    with mock_transport(handler):
        if operation == "request":
            with pytest.raises(ResponseTooLargeError, match="byte limit") as refused:
                HttpClient().request("REPORT", URL, max_bytes=100)
            assert isinstance(refused.value, ValidationError)
        else:
            assert HttpClient().download_capped(URL, cap=100) is None


@pytest.mark.parametrize("operation", ["request", "bounded", "download"])
@pytest.mark.parametrize("encoding_header", [None, "Accept-Encoding", "accept-encoding"])
def test_capped_reads_request_identity_encoding_without_changing_caller_headers(
    operation: str, encoding_header: str | None,
) -> None:
    headers = {"X-Test": "1", "Host": "other.example"}
    if encoding_header is not None:
        headers[encoding_header] = "gzip"
    original_headers = dict(headers)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["host"] == "dav.example.test"
        assert request.headers["x-test"] == "1"
        return httpx2.Response(200, content=b"ok")

    with mock_transport(handler):
        client = HttpClient()
        if operation == "request":
            assert client.request("REPORT", URL, max_bytes=10, headers=headers).content == b"ok"
        elif operation == "bounded":
            result = client.download_bounded(URL, budget=OutboundBudget(bytes=10), headers=headers)
            assert result is not None and result.content == b"ok"
        else:
            assert client.download_capped(URL, cap=10, headers=headers) == b"ok"
        assert headers == original_headers


def test_uncapped_request_preserves_requested_content_encoding() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["accept-encoding"] == "gzip"
        return httpx2.Response(200, content=b"ok")

    with mock_transport(handler):
        assert HttpClient().request("GET", URL, headers={"accept-encoding": "gzip"}).content == b"ok"


@pytest.mark.parametrize("method,status", [("HEAD", 200), ("GET", 204), ("GET", 304)])
def test_capped_request_ignores_declared_length_for_bodyless_responses(
    method: str, status: int,
) -> None:
    with mock_transport(lambda request: httpx2.Response(
        status, headers={"Content-Length": "1000"}, stream=httpx2.ByteStream(b""),
    )):
        response = HttpClient().request(method, URL, max_bytes=5)
    assert response.status_code == status
    assert response.content == b""
    assert response.headers["content-length"] == "1000"


def test_capped_download_retains_shared_byte_accounting() -> None:
    budget = OutboundBudget(bytes=5)
    state = OutboundBudgetState(budget)
    with mock_transport(lambda request: httpx2.Response(200, content=b"abc")):
        result = HttpClient().download_bounded(URL, budget=budget, budget_state=state)
        assert result is not None and result.content == b"abc"
        assert state.bytes == 3
        assert HttpClient().download_bounded(URL, budget=budget, budget_state=state) is None
    assert state.requests == 2
    assert state.bytes == 3


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_capped_request_requires_positive_limit(max_bytes: int) -> None:
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        HttpClient().request("REPORT", URL, max_bytes=max_bytes)


class _Chunks(httpx2.SyncByteStream):
    """A 320-byte body in 16-byte chunks that counts the chunks read and records its closing."""

    def __init__(self) -> None:
        self.reads, self.closed = 0, False

    def __iter__(self) -> Iterator[bytes]:
        for _index in range(20):
            self.reads += 1
            yield b"x" * 16

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("follow_redirects", [False, True])
def test_capped_request_follows_a_redirect_without_reading_its_body_past_the_cap(
    follow_redirects: bool,
) -> None:
    """A redirect hop's body over the cap is absent, as a refusing answer's is: unread past the cap, the hop taken."""

    hop = _Chunks()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/target":
            return httpx2.Response(200, content=b"ok")
        return httpx2.Response(302, headers={"Location": "/target"}, stream=hop)

    with mock_transport(handler) as requests:
        response = HttpClient().request(
            "REPORT", URL, max_bytes=5,
            follow_redirects=follow_redirects,
            same_origin_redirects=0 if follow_redirects else 3,
        )

    assert (response.status_code, response.content) == (200, b"ok")
    assert [request.url.path for request in requests] == ["/path", "/target"]
    assert hop.closed and hop.reads < 20


def test_redirect_to_an_unsafe_host_is_rejected_at_the_hop() -> None:
    """Following a redirect re-enters the pinned backend, so a 30x to metadata is rejected."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "127.0.0.1":
            return httpx2.Response(302, headers={"Location": "http://169.254.169.254/"})
        raise AddressNotAllowedError()  # what the pinned backend raises for this hop

    with mock_transport(handler) as requests, pytest.raises(ValidationError):
        HttpClient().get("http://127.0.0.1:8123/", allow_private=True, follow_redirects=True)

    assert [str(request.url) for request in requests] == ["http://127.0.0.1:8123/", "http://169.254.169.254/"]


def test_redirect_not_followed_by_default_and_host_is_the_url_host() -> None:
    """Without ``follow_redirects`` the 30x is returned as-is, and the sent Host is the URL host."""

    redirect = httpx2.Response(302, headers={"Location": "http://169.254.169.254/"})
    with mock_transport(lambda request: redirect) as requests:
        response = HttpClient().get("http://127.0.0.1:8123/", headers={"Host": "evil.example.com"}, allow_private=True)

    assert response.status_code == 302
    assert [request.headers["host"] for request in requests] == ["127.0.0.1:8123"]


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.parametrize("method", ["PROPFIND", "REPORT", "PUT", "DELETE"])
@pytest.mark.parametrize("max_bytes", [None, 5])
def test_same_origin_redirect_preserves_request(
    status: int, method: str, max_bytes: int | None,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return (
            httpx2.Response(status, headers={"Location": "https://dav.example:443/target"})
            if len(requests) == 1
            else httpx2.Response(207)
        )

    with mock_transport(handler):
        response = HttpClient().request(
            method,
            "https://dav.example/start",
            body=b"<propfind/>",
            headers={"Authorization": "Basic test", "Depth": "1", "If-Match": '"v1"'},
            same_origin_redirects=3,
            max_bytes=max_bytes,
        )
        assert response.status_code == 207
        assert [str(request.url) for request in requests] == ["https://dav.example/start", "https://dav.example/target"]
        assert all(request.method == method and request.content == b"<propfind/>" for request in requests)
        assert all(request.headers["authorization"] == "Basic test" for request in requests)
        assert all(request.headers["depth"] == "1" and request.headers["if-match"] == '"v1"' for request in requests)


@pytest.mark.parametrize("destination", ["https://other.example/", "http://dav.example/", "https://dav.example:8443/"])
def test_same_origin_redirect_rejects_changed_origin(destination: str) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(302, headers={"Location": destination})

    with mock_transport(handler):
        with pytest.raises(ValidationError, match="request origin"):
            HttpClient().request("PROPFIND", "https://dav.example/start", same_origin_redirects=3)
        assert len(requests) == 1


@pytest.mark.parametrize("status,location,count", [(302, "/again", 4), (302, "", 1), (303, "/again", 1)])
def test_same_origin_redirect_stops_at_bound_or_non_preserving_status(
    status: int, location: str, count: int,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status, headers={"Location": location})

    with mock_transport(handler):
        response = HttpClient().request("REPORT", "https://dav.example/start", same_origin_redirects=3)
        assert response.status_code == status
        assert len(requests) == count



def test_real_client_refused_local_port_is_not_sent_and_retryable() -> None:
    # Keep a port bound without listening: no race with another process taking it.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        with pytest.raises(RequestNotSentError) as raised:
            HttpClient().post(f"http://127.0.0.1:{port}/deliver", body=b"payload", allow_private=True, timeout=1)
    assert raised.value.transient and raised.value.retry_after is None
    assert "not sent" in raised.value.public_message


@pytest.mark.parametrize("failure", [
    socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure"),
    socket.gaierror(socket.EAI_SYSTEM, "resolver system failure"),
    socket.gaierror(socket.EAI_FAIL, "resolver refused the query"),
    socket.gaierror(socket.EAI_MEMORY, "resolver out of memory"),
    TimeoutError(),
])
def test_temporary_dns_failure_is_not_sent(monkeypatch: pytest.MonkeyPatch, failure: OSError) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(RequestNotSentError) as raised:
        HttpClient().post("https://unresolved.example/deliver", body=b"payload")
    assert raised.value.transient


@pytest.mark.parametrize("errno", [socket.EAI_NONAME, socket.EAI_NODATA])
def test_nonexistent_dns_host_is_a_permanent_target_error(monkeypatch: pytest.MonkeyPatch, errno: int) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise socket.gaierror(errno, "no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    dialled = _record_dials(monkeypatch)
    with pytest.raises(AddressResolutionError):
        resolved_addresses("absent.example", 443)
    with pytest.raises(AddressResolutionError):
        HttpClient().post("https://absent.example/deliver", body=b"payload")
    assert dialled == []


def test_tls_handshake_failure_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: Any, request: httpx2.Request) -> Any:
        raise httpx2.ConnectError("TLS handshake diagnostic", request=request)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", fail)
    with PinnedTransport() as transport, pytest.raises(RequestNotSentError):
        transport.handle_request(httpx2.Request("POST", URL, content=b"payload"))


def test_write_failure_remains_uncertain() -> None:
    def fail(request: httpx2.Request) -> Any:
        raise httpx2.WriteError("write diagnostic", request=request)

    with mock_transport(fail):
        with pytest.raises(httpx2.WriteError):
            HttpClient().post(URL, body=b"payload")


@pytest.mark.parametrize("native_redirects", [False, True])
def test_connect_failure_after_a_redirect_remains_uncertain(native_redirects: bool):
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/first":
            return httpx2.Response(307, headers={"Location": "/second"})
        raise RequestNotSentError()

    with mock_transport(respond):
        with pytest.raises(httpx2.TransportError):
            if native_redirects:
                HttpClient().post("https://provider.example/first", body=b"payload", follow_redirects=True)
            else:
                HttpClient().request("POST", "https://provider.example/first", body=b"payload", same_origin_redirects=1)


def test_bounded_download_connect_failure_after_redirect_remains_uncertain():
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/first":
            return httpx2.Response(307, headers={"Location": "/second"})
        raise RequestNotSentError()

    with mock_transport(respond):
        with pytest.raises(httpx2.TransportError):
            HttpClient().download_bounded("https://provider.example/first")


def test_empty_dns_result_is_a_typed_resolution_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])
    with pytest.raises(AddressResolutionError):
        resolved_addresses("unresolved.example", 443)
    with pytest.raises(AddressResolutionError):
        HttpClient().get("https://unresolved.example/")


@pytest.mark.parametrize("destination", ["https://cdn.example/file", "https://other.example:8443/file"])
def test_bounded_download_uses_native_redirect_credentials(destination):
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(302, headers={"Location": destination})
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert request.headers["host"] == httpx2.URL(destination).netloc.decode()
        return httpx2.Response(200, content=b"ok")
    with mock_transport(respond):
        result = HttpClient().download_bounded(
            URL, headers={"Authorization": "Bearer test-secret", "Cookie": "session=test-secret"},
        )
        assert result is not None and result.content == b"ok"
        assert len(calls) == 2
        assert calls[0].headers["authorization"] == "Bearer test-secret"


@pytest.mark.parametrize("fail", [False, True])
def test_stream_retains_native_response_until_context_exit(fail: bool) -> None:
    closed = False

    class Body(httpx2.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"first\n"
            yield b"second\n"

        def close(self) -> None:
            nonlocal closed
            closed = True

    try:
        with mock_transport(lambda request: httpx2.Response(200, stream=Body())) as requests, HttpClient().stream(
            "POST", "https://example.com/checkout", body=b"request", allow_private=True,
            headers={"Host": "spoof.invalid", "Authorization": "Bearer test-token"},
        ) as response:
            assert not response.is_closed and not closed
            lines = response.iter_lines()
            assert next(lines) == "first"
            assert not response.is_closed and not closed
            if fail:
                raise ValueError("consumer failed")
    except ValueError:
        assert fail
    assert response.is_closed and closed
    [request] = requests
    assert (request.headers["host"], request.headers["authorization"]) == ("example.com", "Bearer test-token")
    assert request.content == b"request"


@pytest.mark.parametrize(
    ("header", "delay"),
    [
        pytest.param("120", timedelta(seconds=120), id="delay-seconds"),
        pytest.param("Sat, 10 Oct 2026 12:02:00 GMT", timedelta(minutes=2), id="http-date"),
        pytest.param(None, None, id="absent"),
        pytest.param("0", None, id="zero"),
        pytest.param("Sat, 10 Oct 2026 11:59:00 GMT", None, id="past-date"),
        pytest.param("soon", None, id="malformed"),
        pytest.param("1.5", None, id="fractional-seconds-are-not-delay-seconds"),
        pytest.param("86401", None, id="past-the-limit"),
    ],
)
def test_retry_after_reads_delay_seconds_or_a_future_http_date(header: str | None, delay: timedelta | None) -> None:
    """RFC 9110 §10.2.3: one parser for every backend's Retry-After hint."""

    response = httpx2.Response(429, headers={} if header is None else {"Retry-After": header})
    assert retry_after(response, now=datetime(2026, 10, 10, 12, 0, tzinfo=UTC)) == delay


class _VendorError(IntegrationError):
    """A vendor refusal; ``refusal`` keeps the facts its factory saw before the owner chained the failure."""

    refusal: HttpRefusal


def _vendor_error(refusal: HttpRefusal) -> _VendorError:
    """The vendor's one factory, through the one hand-off: its own words for a 401, its own wait for a 423 (which
    makes that status transient), the refusal's facts otherwise."""

    own = "Example API rejected this credential." if refusal.status == 401 else None
    wait = (refusal.retry_after or timedelta(seconds=2)) if refusal.status == 423 else None
    error = refusal.as_error(_VendorError, "Example API", public_message=own, retry_after=wait)
    error.refusal = refusal
    return error


class _Page(BaseModel):
    items: list[str]


def _answer(answer: httpx2.Response | Exception) -> Any:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    return respond


@pytest.mark.parametrize(
    ("answer", "cause", "status", "transient", "wait", "message"),
    [
        pytest.param(
            httpx2.Response(429, headers={"Retry-After": "30"}), "status", 429, True, timedelta(seconds=30),
            "Example API is rate limiting requests (HTTP 429).", id="rate-limit-waits-for-retry-after",
        ),
        pytest.param(
            httpx2.Response(503), "status", 503, True, None,
            "Example API is temporarily unavailable (HTTP 503).", id="server-error",
        ),
        pytest.param(
            httpx2.Response(404, json={"message": "secret vendor text"}), "status", 404, False, None,
            "Example API refused the request (HTTP 404).", id="client-error",
        ),
        pytest.param(
            httpx2.Response(401), "status", 401, False, None,
            "Example API rejected this credential.", id="vendor-words-its-own-case",
        ),
        pytest.param(
            httpx2.Response(423), "status", 423, True, timedelta(seconds=2),
            "Example API refused the request (HTTP 423).", id="vendor-adds-a-transient-status-and-its-wait",
        ),
        pytest.param(
            httpx2.Response(200, json={"items": "not a list"}), "unreadable", None, False, None,
            "Example API returned an unreadable response.", id="fails-validation",
        ),
        pytest.param(
            httpx2.Response(200, text="not json"), "unreadable", None, False, None,
            "Example API returned an unreadable response.", id="not-json",
        ),
        pytest.param(
            httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx2.ByteStream(b"not gzip")),
            "unreadable", None, False, None, "Example API returned an unreadable response.",
            id="a-body-httpx2-cannot-decode",
        ),
        pytest.param(
            httpx2.Response(200, content=b"x" * 65), "oversized", None, False, None,
            "Example API returned an oversized response.", id="oversized-is-permanent",
        ),
        pytest.param(
            httpx2.Response(408), "status", 408, True, None,
            "Example API is temporarily unavailable (HTTP 408).", id="a-request-timeout-may-be-repeated",
        ),
        pytest.param(
            httpx2.Response(501), "status", 501, False, None,
            "Example API refused the request (HTTP 501).", id="a-method-not-implemented-is-permanent",
        ),
        pytest.param(
            httpx2.Response(204), "unreadable", None, False, None,
            "Example API returned an unreadable response.", id="a-typed-request-answered-with-no-content",
        ),
        pytest.param(
            httpx2.ReadTimeout("slow"), "unreachable", None, True, None,
            "Example API did not answer.", id="a-transport-failure-after-sending",
        ),
        pytest.param(
            httpx2.ConnectError("refused"), "not_sent", None, True, None,
            "Example API could not be reached.", id="a-connect-failure-sent-nothing",
        ),
        pytest.param(
            RequestNotSentError(), "not_sent", None, True, None,
            "Example API could not be reached.", id="not-sent",
        ),
    ],
)
def test_the_typed_request_raises_the_callers_error_from_the_facts_of_each_refusal(
    answer: httpx2.Response | Exception, cause: str, status: int | None, transient: bool,
    wait: timedelta | None, message: str,
) -> None:
    """One status rule; the caller composes the message and never sees the answer's text."""

    with mock_transport(_answer(answer)), pytest.raises(_VendorError) as refused:
        HttpClient().request_model("GET", URL, model=_Page, error=_vendor_error, max_bytes=64)

    facts = refused.value.refusal
    assert (facts.cause, facts.status, refused.value.transient, refused.value.retry_after) == (
        cause, status, transient, wait,
    )
    assert str(refused.value) == message
    assert facts.body == ({"message": "secret vendor text"} if status == 404 else None)
    # The factory saw the failure the owner then chained, so it can tell a request not sent from an uncertain one.
    assert facts.failure is refused.value.__cause__
    if isinstance(answer, Exception):
        assert facts.failure is answer


def _answer_with(status: int, body: str, headers: dict[str, str] | None = None) -> tuple[Any, _Chunks]:
    """Answer ``status`` with a body httpx2 cannot decode (``"undecodable"``), or with :class:`_Chunks`.

    The chunks are over the tests' 64-byte cap.
    """

    chunks = _Chunks()

    def respond(request: httpx2.Request) -> httpx2.Response:
        del request
        if body == "undecodable":
            return httpx2.Response(
                status, headers={"Content-Encoding": "gzip", **(headers or {})}, stream=httpx2.ByteStream(b"not gzip"),
            )
        return httpx2.Response(status, headers=headers, stream=chunks)

    return respond, chunks


@pytest.mark.parametrize("body", ["undecodable", "over-the-cap"])
@pytest.mark.parametrize(
    ("status", "headers", "transient", "wait", "message"),
    [
        pytest.param(
            503, {"Retry-After": "30"}, True, timedelta(seconds=30),
            "Example API is temporarily unavailable (HTTP 503).", id="an-outage-is-transient-and-waits",
        ),
        pytest.param(
            429, {"Retry-After": "30"}, True, timedelta(seconds=30),
            "Example API is rate limiting requests (HTTP 429).", id="a-rate-limit-is-transient-and-waits",
        ),
        pytest.param(404, {}, False, None, "Example API refused the request (HTTP 404).", id="a-refusal-is-permanent"),
    ],
)
def test_a_refusing_status_decides_before_its_body_does(
    status: int, headers: dict[str, str], transient: bool, wait: timedelta | None, message: str, body: str,
) -> None:
    """The status is the stronger fact: the refusal keeps it and its wait, and a body httpx2 cannot decode, or one
    over the cap, is absent. Reading still stops at the cap.

    Only a success's such body is an unreadable or oversized answer (``a-body-httpx2-cannot-decode`` and
    ``oversized-is-permanent`` above).
    """

    respond, chunks = _answer_with(status, body, headers)
    with mock_transport(respond), pytest.raises(_VendorError) as refused:
        HttpClient().request_model("GET", URL, model=_Page, error=_vendor_error, max_bytes=64)

    facts = refused.value.refusal
    assert (facts.cause, facts.status, facts.body, facts.failure) == ("status", status, None, None)
    assert (refused.value.transient, refused.value.retry_after, str(refused.value)) == (transient, wait, message)
    if body == "over-the-cap":
        assert chunks.closed and chunks.reads < 20


@pytest.mark.parametrize("max_bytes", [64, None], ids=["capped", "uncapped"])
def test_a_raw_request_returns_a_refusing_answer_whose_body_httpx2_cannot_decode(max_bytes: int | None) -> None:
    """Capped or not, with a factory or without, a refusing answer comes back with no body.

    A success's undecodable body is still the factory's unreadable refusal.
    """

    with mock_transport(_answer_with(503, "undecodable")[0]):
        response = HttpClient().request("GET", URL, max_bytes=max_bytes)
    with mock_transport(_answer_with(200, "undecodable")[0]), pytest.raises(_VendorError) as unreadable:
        HttpClient().request("GET", URL, max_bytes=max_bytes, error=_vendor_error)

    assert (response.status_code, response.content) == (503, b"")
    assert unreadable.value.refusal.cause == "unreadable"
    assert isinstance(unreadable.value.__cause__, httpx2.DecodingError)


@pytest.mark.parametrize("declared", [False, True], ids=["streamed", "declared-length"])
def test_a_raw_request_returns_a_refusing_answer_over_the_cap_without_reading_past_it(declared: bool) -> None:
    """A 502 whose error page is over a small cap is a 502 with no body, retried like any other 502.

    A 200 over the cap is still oversized. Neither body is read past the cap; a declared length is not read at all.
    """

    headers = {"Content-Length": "320"} if declared else {}
    gateway, page = _answer_with(502, "over-the-cap", headers)
    with mock_transport(gateway):
        response = HttpClient().request("GET", URL, max_bytes=64, error=_vendor_error)
    with mock_transport(_answer_with(200, "over-the-cap", headers)[0]), pytest.raises(_VendorError) as oversized:
        HttpClient().request("GET", URL, max_bytes=64, error=_vendor_error)

    assert (response.status_code, response.content) == (502, b"")
    assert HttpRefusal.of(response).transient
    assert page.closed and page.reads < (1 if declared else 20)
    assert oversized.value.refusal.cause == "oversized"
    assert isinstance(oversized.value.__cause__, ResponseTooLargeError)


def test_a_refusal_has_a_status_or_a_failure() -> None:
    with pytest.raises(ValueError):
        HttpRefusal()
    with pytest.raises(ValueError):
        HttpRefusal(status=503, failure=RequestNotSentError())


def test_a_refusal_keeps_no_body_nested_past_the_document_bound() -> None:
    """An error body a level past ``MAX_JSON_DOCUMENT_DEPTH`` is malformed JSON, which the refusal keeps none of."""

    def body(depth: int) -> bytes:
        levels = depth - 1
        return b'{"error": ' + b"[" * levels + b"0" + b"]" * levels + b"}"

    kept = HttpRefusal.of(httpx2.Response(502, content=body(MAX_JSON_DOCUMENT_DEPTH)))
    dropped = HttpRefusal.of(httpx2.Response(502, content=body(MAX_JSON_DOCUMENT_DEPTH + 1)))

    assert kept.body == json.loads(body(MAX_JSON_DOCUMENT_DEPTH))
    assert (dropped.status, dropped.body) == (502, None)


@pytest.mark.parametrize(
    ("header", "own", "wait"),
    [
        pytest.param(None, timedelta(minutes=1), timedelta(minutes=1), id="a-wait-only-the-vendor-states"),
        pytest.param("30", timedelta(minutes=1), timedelta(minutes=1), id="the-vendors-own-wait-when-given"),
        pytest.param("30", None, timedelta(seconds=30), id="else-the-answers-retry-after"),
    ],
)
def test_the_hand_off_takes_the_vendors_own_wait_and_the_refusal_keeps_the_answers(
    header: str | None, own: timedelta | None, wait: timedelta,
) -> None:
    """A vendor whose error body names its wait states it at the hand-off; the refusal claims no header it lacked."""

    refusal = HttpRefusal.of(httpx2.Response(429, headers={} if header is None else {"Retry-After": header}))
    error = refusal.as_error(_VendorError, "Example API", retry_after=own)

    assert (error.transient, error.retry_after) == (True, wait)
    assert str(error) == "Example API is rate limiting requests (HTTP 429)."
    assert refusal.retry_after == (None if header is None else timedelta(seconds=30))


def test_the_typed_request_sends_native_json_and_returns_the_validated_model_or_the_parsed_json() -> None:
    with mock_transport(lambda request: httpx2.Response(200, json={"items": ["a"]})) as requests:
        page = HttpClient().request_model("POST", URL, json={"q": "ada"}, model=_Page, error=_vendor_error)
        parsed = HttpClient().request_model(
            "GET", URL, headers={"accept": "application/vnd.example+json"}, error=_vendor_error,
        )

    assert_type(page, _Page)
    assert page == _Page(items=["a"]) and parsed == {"items": ["a"]}
    assert json.loads(requests[0].content) == {"q": "ada"}
    assert requests[0].headers["content-type"] == "application/json"
    # The caller's header replaces the default whatever its case.
    assert [request.headers.get_list("accept") for request in requests] == [
        ["application/json"], ["application/vnd.example+json"],
    ]


def test_a_request_with_an_error_factory_returns_any_status_and_refuses_what_it_could_not_read() -> None:
    """The raw request keeps every status for its caller; its factory tells a request not sent from an uncertain one."""

    with mock_transport(lambda request: httpx2.Response(404, content=b"xy")):
        assert HttpClient().request("GET", URL, max_bytes=2, error=_vendor_error).status_code == 404
        over_the_cap = HttpClient().request("GET", URL, max_bytes=1, error=_vendor_error)
    with mock_transport(lambda request: httpx2.Response(200, content=b"xy")), pytest.raises(_VendorError) as oversized:
        HttpClient().request("GET", URL, max_bytes=1, error=_vendor_error)
    # Over the cap a refusing status is still the answer, with no body; only a success is oversized.
    assert (over_the_cap.status_code, over_the_cap.content) == (404, b"")
    causes = {}
    for failure in (httpx2.ReadTimeout("slow"), RequestNotSentError()):
        with mock_transport(_answer(failure)), pytest.raises(_VendorError) as refused:
            HttpClient().request("POST", URL, error=_vendor_error)
        causes[refused.value.refusal.cause] = (refused.value.refusal.failure is failure, refused.value.transient)

    assert oversized.value.refusal.cause == "oversized" and isinstance(oversized.value.__cause__, ResponseTooLargeError)
    assert causes == {"unreachable": (True, True), "not_sent": (True, True)}


def test_a_failure_the_factory_returns_is_raised_unchanged() -> None:
    """A caller that passes a request not sent on returns its failure; the owner re-raises it, its chain intact."""

    failure = RequestNotSentError()
    failure.__cause__ = ConnectionRefusedError("refused")
    with mock_transport(_answer(failure)), pytest.raises(RequestNotSentError) as raised:
        HttpClient().request("POST", URL, error=lambda refusal: refusal.failure or _VendorError())

    assert raised.value is failure and isinstance(raised.value.__cause__, ConnectionRefusedError)


_KEY = "example-key-5f3a"
"""An invented credential: no refusal may carry it in its message, its ``repr`` or its exception chain."""

_API = "https://api.example.test/items"


def _dial_the_real_http_stack(monkeypatch: pytest.MonkeyPatch, *refusals: BaseException) -> None:
    """Send through the real pinned transport, httpcore2 and h11, with only DNS and the socket scripted.

    ``mock_transport`` replaces the transport, so it never reaches h11's header validation. Here the raw dial
    returns httpcore2's own ``MockStream`` answering one JSON page, or raises each of ``refusals`` in turn.
    """

    _resolve_to(monkeypatch, "93.184.216.34")
    page = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 12\r\n\r\n{"items":[]}'
    queue = list(refusals)

    def connect(self: Any, host: str, port: int, **kwargs: Any) -> httpcore2.NetworkStream:
        if queue:
            raise queue.pop(0)
        return httpcore2.MockStream([page])

    monkeypatch.setattr(httpcore2.SyncBackend, "connect_tcp", connect)


def _texts(error: BaseException) -> Iterator[str]:
    """Yield every text ``error`` carries: its rendered traceback, as a log or an error tracker records it, and the
    ``str`` and ``repr`` of each link of its chain, a suppressed context included."""

    yield "".join(traceback.format_exception(error))
    link: BaseException | None = error
    while link is not None:
        yield str(link)
        yield repr(link)
        link = link.__cause__ or link.__context__


def test_a_refusal_keeps_the_answers_headers_for_its_vendor_but_no_credential_and_never_shows_them() -> None:
    """A vendor reads its rate-limit headers from the refusal; cookies and credentials are not kept, and no header
    value reaches the refusal's ``repr`` or the vendor's error."""

    cookie = f"session={_KEY}"
    answer = httpx2.Response(403, json={}, headers={
        "X-RateLimit-Remaining": "0", "Set-Cookie": cookie, "Authorization": f"Bearer {_KEY}",
    })
    with mock_transport(lambda request: answer), pytest.raises(_VendorError) as refused:
        HttpClient().request_model("GET", URL, error=_vendor_error)

    refusal = refused.value.refusal
    assert refusal.headers.get("x-ratelimit-remaining") == "0"
    assert "set-cookie" not in refusal.headers and "authorization" not in refusal.headers
    assert "x-ratelimit" not in repr(refusal).lower()
    assert not [text for text in _texts(refused.value) if _KEY in text or "X-RateLimit" in text]
    # A copy: nothing a reader does to it reaches the answer.
    assert answer.headers.get("set-cookie") == cookie


@pytest.mark.parametrize(
    ("status", "transient", "retry_after", "expected"),
    [
        pytest.param(403, True, None, (True, None), id="a-vendors-own-transient-refusal"),
        pytest.param(403, None, timedelta(minutes=10), (True, timedelta(minutes=10)), id="its-wait-implies-transience"),
        pytest.param(501, False, None, (False, None), id="a-vendor-may-call-a-status-permanent"),
    ],
)
def test_the_hand_off_states_a_vendors_own_transience_and_wait(
    status: int, transient: bool | None, retry_after: timedelta | None, expected: tuple[bool, timedelta | None],
) -> None:
    """The vendor's keywords replace the refusal's facts; a wait it states implies transience."""

    refusal = HttpRefusal.of(httpx2.Response(status))
    error = refusal.as_error(
        _VendorError, "Example API", public_message="Example API is busy.",
        transient=transient, retry_after=retry_after,
    )

    assert (error.transient, error.retry_after, str(error)) == (*expected, "Example API is busy.")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(f"Bearer {_KEY} ", id="trailing-space-h11-will-not-send"),
        pytest.param(f"Bearer {_KEY}\t", id="trailing-tab-h11-will-not-send"),
        pytest.param(f"Bearer {_KEY}​", id="zero-width-space-that-cannot-be-encoded"),
        pytest.param(f"Bearer {_KEY}", id="no-break-space-that-cannot-be-encoded"),
    ],
)
def test_a_request_the_library_will_not_build_is_a_permanent_not_sent_refusal_that_keeps_no_header_value(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    """The request could not be built: not sent, permanent, worded so, and none of the library's text is kept."""

    _dial_the_real_http_stack(monkeypatch)
    legal = {"Authorization": f"Bearer {_KEY}"}
    # The path is real: the same request with a legal value is sent and answered.
    assert HttpClient().request_model("GET", _API, headers=legal, model=_Page, error=_vendor_error) == _Page(items=[])

    headers = {"Authorization": value}
    with pytest.raises(_VendorError) as refused:
        HttpClient().request_model("GET", _API, headers=headers, model=_Page, error=_vendor_error)
    with pytest.raises(RequestNotSentError) as raw:
        HttpClient().request("GET", _API, headers=headers)
    with pytest.raises(RequestNotSentError) as streamed, HttpClient().stream("GET", _API, headers=headers):
        pass

    error, facts = refused.value, refused.value.refusal
    assert (facts.cause, facts.status, error.transient, error.retry_after) == ("not_sent", None, False, None)
    assert str(error) == "The request to Example API could not be sent as configured."
    assert error.__cause__ is None
    for failure in (facts.failure, raw.value, streamed.value):
        assert isinstance(failure, RequestNotSentError) and not failure.transient
        assert failure.__cause__ is None and failure.__context__ is None
    for carrier in (error, raw.value, streamed.value):
        assert not [text for text in _texts(carrier) if _KEY in text]
    assert _KEY not in repr(facts)


def test_a_refused_connection_stays_a_transient_not_sent_refusal_that_chains_its_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection that could not be made can be outlived: transient, the usual words, its failure chained."""

    _dial_the_real_http_stack(monkeypatch, ConnectionRefusedError("refused"))
    with pytest.raises(_VendorError) as refused:
        HttpClient().request_model("GET", _API, headers={"Authorization": f"Bearer {_KEY}"}, error=_vendor_error)

    error = refused.value
    assert (error.refusal.cause, error.transient, str(error)) == ("not_sent", True, "Example API could not be reached.")
    assert error.__cause__ is error.refusal.failure
    assert isinstance(error.__cause__, RequestNotSentError) and error.__cause__.transient


def test_retry_after_dates_reach_the_refusal_of_a_bounded_download() -> None:
    """``DownloadResult`` keeps native headers, so the one ``Retry-After`` parser reads them."""

    later = format_datetime(timezone.now() + timedelta(minutes=5), usegmt=True)
    with mock_transport(lambda request: httpx2.Response(503, headers={"Retry-After": later})):
        result = HttpClient().download_bounded(URL, statuses={503})

    assert result is not None
    refusal = HttpRefusal.of(result)
    assert (refusal.cause, refusal.status, refusal.transient, refusal.body) == ("status", 503, True, None)
    assert refusal.retry_after is not None and timedelta(minutes=4) < refusal.retry_after <= timedelta(minutes=5)
