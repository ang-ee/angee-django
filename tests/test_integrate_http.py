"""Tests for the SSRF-pinned outbound HTTP client (``integrate.http``).

These exercise the real ``PinnedTransport`` / ``_PinnedBackend`` over httpx2. The
backend tests stub only ``socket.getaddrinfo`` (resolution) and httpcore2's raw
dial, so the address gate, the resolve-then-pin (DNS-rebind) protection, the
dial-all fallback, and the ``OSError``-vs-``ValidationError`` distinction are
asserted against the production path. End-to-end tests confirm those exceptions
survive httpx2. Request and download tests inject httpx2's MockTransport through
HttpClient's transport factory to assert redirect handling, byte budgets, address
policy forwarding and the URL's Host header. No external network or database is
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

from angee.integrate.http import (
    HttpClient,
    OutboundBudget,
    OutboundBudgetState,
    PinnedTransport,
    ResponseTooLargeError,
    _PinnedBackend,
    _request_headers,
)

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

    with pytest.raises(ValidationError):
        _PinnedBackend(allow_private=allow_private).connect_tcp("h", 443)
    assert dialled == []


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.0.1", "172.16.0.1", "127.0.0.1"])
def test_private_and_loopback_rejected_in_public_mode(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Default (public) mode rejects RFC-1918 and loopback before any dial."""

    _resolve_to(monkeypatch, address)
    dialled = _record_dials(monkeypatch)

    with pytest.raises(ValidationError):
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


def test_all_addresses_unreachable_raises_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If every validated address fails, a transport ``OSError`` surfaces — not ``ValidationError``."""

    _resolve_to(monkeypatch, "93.184.216.34", "93.184.216.35")
    _record_dials(monkeypatch, behaviors=[httpcore2.ConnectError("a"), httpcore2.ConnectError("b")])

    with pytest.raises(OSError):
        _PinnedBackend(allow_private=False).connect_tcp("h", 443)


def test_httpclient_surfaces_validation_error_for_an_unsafe_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSRF gate's ``ValidationError`` survives httpx2 end-to-end."""

    _resolve_to(monkeypatch, "169.254.169.254")

    with pytest.raises(ValidationError):
        HttpClient().get(URL)


def test_httpclient_surfaces_os_error_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure surfaces as ``OSError`` end-to-end (webhook telemetry relies on it)."""

    _resolve_to(monkeypatch, "93.184.216.34")
    _record_dials(monkeypatch, behaviors=[httpcore2.ConnectError("down")])

    with pytest.raises(OSError):
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
    monkeypatch: pytest.MonkeyPatch, operation: str,
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

    def transport(*, allow_private: bool) -> httpx2.MockTransport:
        assert allow_private is False

        def handler(request: httpx2.Request) -> httpx2.Response:
            assert request.headers["authorization"] == "Bearer token"
            return httpx2.Response(200, stream=CountingStream())

        return httpx2.MockTransport(handler)

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(transport))

    headers = {"Authorization": "Bearer token"}
    if operation == "request":
        with pytest.raises(ResponseTooLargeError, match="byte limit"):
            HttpClient().request("REPORT", URL, max_bytes=5, headers=headers)
    else:
        assert HttpClient().download_capped(URL, cap=5, headers=headers) is None
    assert reads < 20
    assert closed


@pytest.mark.parametrize("operation", ["request", "download"])
def test_capped_read_rejects_declared_oversize_before_reading(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """Content-Length rejects an oversized response before its stream is consumed."""

    reads = 0

    class CountingStream(httpx2.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal reads
            reads += 1
            yield b"body"

    def transport(*, allow_private: bool) -> httpx2.MockTransport:
        assert allow_private is False
        return httpx2.MockTransport(
            lambda _request: httpx2.Response(
                200,
                headers={"Content-Length": "6"},
                stream=CountingStream(),
            )
        )

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(transport))

    if operation == "request":
        with pytest.raises(ResponseTooLargeError, match="byte limit"):
            HttpClient().request("PROPFIND", URL, max_bytes=5)
    else:
        assert HttpClient().download_capped(URL, cap=5) is None
    assert reads == 0


@pytest.mark.parametrize("compressed", [False, True])
def test_capped_request_returns_decoded_body_and_response_facts(
    monkeypatch: pytest.MonkeyPatch, compressed: bool,
) -> None:
    body = b"<multistatus/>"
    headers = {"ETag": '"version"', "Content-Type": "application/xml"}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    monkeypatch.setattr(
        HttpClient, "transport_factory",
        staticmethod(lambda **_: httpx2.MockTransport(lambda request: httpx2.Response(
            207,
            headers=headers,
            stream=httpx2.ByteStream(gzip.compress(body) if compressed else body),
            extensions={"http_version": b"HTTP/1.1"},
        ))),
    )
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
    monkeypatch: pytest.MonkeyPatch, operation: str,
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

    monkeypatch.setattr(
        HttpClient, "transport_factory",
        staticmethod(lambda **_: httpx2.MockTransport(handler)),
    )
    if operation == "request":
        with pytest.raises(ResponseTooLargeError, match="byte limit") as refused:
            HttpClient().request("REPORT", URL, max_bytes=100)
        assert isinstance(refused.value, ValidationError)
    else:
        assert HttpClient().download_capped(URL, cap=100) is None


@pytest.mark.parametrize("operation", ["request", "bounded", "download"])
@pytest.mark.parametrize("encoding_header", [None, "Accept-Encoding", "accept-encoding"])
def test_capped_reads_request_identity_encoding_without_changing_caller_headers(
    monkeypatch: pytest.MonkeyPatch, operation: str, encoding_header: str | None,
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

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(handler)))
    client = HttpClient()
    if operation == "request":
        assert client.request("REPORT", URL, max_bytes=10, headers=headers).content == b"ok"
    elif operation == "bounded":
        result = client.download_bounded(URL, budget=OutboundBudget(bytes=10), headers=headers)
        assert result is not None and result.content == b"ok"
    else:
        assert client.download_capped(URL, cap=10, headers=headers) == b"ok"
    assert headers == original_headers


def test_uncapped_request_preserves_requested_content_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["accept-encoding"] == "gzip"
        return httpx2.Response(200, content=b"ok")

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(handler)))
    assert HttpClient().request("GET", URL, headers={"accept-encoding": "gzip"}).content == b"ok"


@pytest.mark.parametrize("method,status", [("HEAD", 200), ("GET", 204), ("GET", 304)])
def test_capped_request_ignores_declared_length_for_bodyless_responses(
    monkeypatch: pytest.MonkeyPatch, method: str, status: int,
) -> None:
    monkeypatch.setattr(
        HttpClient, "transport_factory",
        staticmethod(lambda **_: httpx2.MockTransport(lambda request: httpx2.Response(
            status, headers={"Content-Length": "1000"}, stream=httpx2.ByteStream(b""),
        ))),
    )
    response = HttpClient().request(method, URL, max_bytes=5)
    assert response.status_code == status
    assert response.content == b""
    assert response.headers["content-length"] == "1000"


def test_capped_download_retains_shared_byte_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = OutboundBudget(bytes=5)
    state = OutboundBudgetState(budget)
    monkeypatch.setattr(
        HttpClient, "transport_factory",
        staticmethod(lambda **_: httpx2.MockTransport(lambda request: httpx2.Response(200, content=b"abc"))),
    )
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
    monkeypatch: pytest.MonkeyPatch, follow_redirects: bool,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "/target"}, stream=httpx2.ByteStream(b"oversized"))

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(handler)))
    with pytest.raises(ResponseTooLargeError, match="byte limit"):
        HttpClient().request(
            "REPORT", URL, max_bytes=5,
            follow_redirects=follow_redirects,
            same_origin_redirects=0 if follow_redirects else 3,
        )
    assert len(requests) == 1


def test_redirect_to_an_unsafe_host_is_rejected_at_the_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Following a redirect re-enters the pinned backend, so a 30x to metadata is rejected."""

    requests: list[httpx2.Request] = []

    def transport(*, allow_private: bool) -> httpx2.MockTransport:
        assert allow_private is True

        def handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx2.Response(302, headers={"Location": "http://169.254.169.254/"})
            raise ValidationError("URL host resolves to an address that is not allowed.")

        return httpx2.MockTransport(handler)

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(transport))

    with pytest.raises(ValidationError):
        HttpClient().get("http://127.0.0.1:8123/", allow_private=True, follow_redirects=True)

    assert [str(request.url) for request in requests] == ["http://127.0.0.1:8123/", "http://169.254.169.254/"]


def test_redirect_not_followed_by_default_and_host_is_the_url_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``follow_redirects`` the 30x is returned as-is, and the sent Host is the URL host."""

    received_host = ""

    def transport(*, allow_private: bool) -> httpx2.MockTransport:
        assert allow_private is True

        def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal received_host
            received_host = request.headers["host"]
            return httpx2.Response(302, headers={"Location": "http://169.254.169.254/"})

        return httpx2.MockTransport(handler)

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(transport))
    response = HttpClient().get("http://127.0.0.1:8123/", headers={"Host": "evil.example.com"}, allow_private=True)

    assert response.status_code == 302
    assert received_host == "127.0.0.1:8123"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.parametrize("method", ["PROPFIND", "REPORT", "PUT", "DELETE"])
@pytest.mark.parametrize("max_bytes", [None, 5])
def test_same_origin_redirect_preserves_request(
    monkeypatch: pytest.MonkeyPatch, status: int, method: str, max_bytes: int | None,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return (
            httpx2.Response(status, headers={"Location": "https://dav.example:443/target"})
            if len(requests) == 1
            else httpx2.Response(207)
        )

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(handler)))
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
def test_same_origin_redirect_rejects_changed_origin(monkeypatch: pytest.MonkeyPatch, destination: str) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(302, headers={"Location": destination})

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(handler)))
    with pytest.raises(ValidationError, match="request origin"):
        HttpClient().request("PROPFIND", "https://dav.example/start", same_origin_redirects=3)
    assert len(requests) == 1


@pytest.mark.parametrize("status,location,count", [(302, "/again", 4), (302, "", 1), (303, "/again", 1)])
def test_same_origin_redirect_stops_at_bound_or_non_preserving_status(
    monkeypatch: pytest.MonkeyPatch, status: int, location: str, count: int,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status, headers={"Location": location})

    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(handler)))
    response = HttpClient().request("REPORT", "https://dav.example/start", same_origin_redirects=3)
    assert response.status_code == status
    assert len(requests) == count
