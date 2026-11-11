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
import socket
from collections.abc import Iterator
from typing import Any

import httpcore2
import httpx2
import pytest
from django.core.exceptions import ValidationError

from angee.integrate.errors import RequestNotSentError
from angee.integrate.http import (
    HttpClient,
    OutboundBudget,
    OutboundBudgetState,
    PinnedTransport,
    ResponseTooLargeError,
    _PinnedBackend,
    _request_headers,
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


@pytest.mark.parametrize("follow_redirects", [False, True])
def test_capped_request_refuses_oversized_redirect_before_next_hop(
    follow_redirects: bool,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "/target"}, stream=httpx2.ByteStream(b"oversized"))

    with mock_transport(handler):
        with pytest.raises(ResponseTooLargeError, match="byte limit"):
            HttpClient().request(
                "REPORT", URL, max_bytes=5,
                follow_redirects=follow_redirects,
                same_origin_redirects=0 if follow_redirects else 3,
            )
        assert len(requests) == 1


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


@pytest.mark.parametrize(("answer", "outcome"), [
    (httpx2.Response(200, json={"ready": True}), {"ready": True}),
    (httpx2.Response(503), httpx2.HTTPStatusError),
    (httpx2.Response(200, text="bad json"), ValueError),
], ids=["json", "http-error", "decode-error"])
def test_json_get_uses_shared_headers_and_preserves_http_and_decode_errors(answer: Any, outcome: Any) -> None:
    with mock_transport(lambda request: answer) as requests:
        if isinstance(outcome, dict):
            assert HttpClient().get_json(URL) == outcome
        else:
            with pytest.raises(outcome):
                HttpClient().get_json(URL)
    assert requests[0].headers["Accept"] == "application/json"
