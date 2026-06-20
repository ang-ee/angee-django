"""Tests for the SSRF-pinned outbound HTTP client (``integrate.http``).

These exercise the real :class:`HttpClient` — only ``socket.getaddrinfo`` and the
pinned connection classes are stubbed, so the address gate, the resolve-then-pin
(DNS-rebind) protection, the dial-all fallback, and the un-overridable Host header
are all asserted against the production code path. No network or database is used.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from angee.integrate.http import HttpClient, _response_status

URL = "https://dav.example.test/path?x=1"


def _resolve_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    """Make DNS resolution return the given address(es) for every hostname."""

    def fake_getaddrinfo(hostname: str, port: int | None, *, type: int) -> list[Any]:
        del hostname, type
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, port or 443)) for address in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


class _FakeResponse:
    """Response double exposing the status http.client responses carry."""

    def __init__(self, status: int) -> None:
        self.status = status

    def read(self) -> bytes:
        return b""

    def getheaders(self) -> list[tuple[str, str]]:
        return []


def _record_connections(monkeypatch: pytest.MonkeyPatch, *, behaviors: list[Any]) -> list[Any]:
    """Stub the pinned connections; each instance pops one behavior (status int or Exception)."""

    connections: list[Any] = []
    queue = list(behaviors)

    class RecordingConnection:
        """Connection double recording the pinned address and request, no socket opened."""

        def __init__(self, host: str, *, port: int, timeout: int, pinned_address: Any, **kwargs: Any) -> None:
            del kwargs
            self.host = host
            self.port = port
            self.pinned_address = pinned_address
            self.requests: list[dict[str, Any]] = []
            self.closed = False
            self.behavior = queue.pop(0) if queue else 200
            connections.append(self)

        def request(self, method: str, url: str, *, body: bytes | None, headers: dict[str, str]) -> None:
            if isinstance(self.behavior, Exception):
                raise self.behavior
            self.requests.append({"method": method, "url": url, "body": body, "headers": headers})

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse(int(self.behavior))

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("angee.integrate.http._PinnedHTTPConnection", RecordingConnection)
    monkeypatch.setattr("angee.integrate.http._PinnedHTTPSConnection", RecordingConnection)
    return connections


def test_public_address_is_dialled_at_the_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public host is dialled at the resolved IP (pinned), with the URL host preserved."""

    _resolve_to(monkeypatch, "93.184.216.34")
    connections = _record_connections(monkeypatch, behaviors=[200])

    response = HttpClient().get(URL)

    assert response.status == 200
    assert len(connections) == 1
    # The connection dials the validated IP (the pin), not a re-resolved host.
    assert connections[0].pinned_address.address == "93.184.216.34"
    request = connections[0].requests[0]
    assert request["headers"]["Host"] == "dav.example.test"
    assert request["url"] == "/path?x=1"
    assert connections[0].closed


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
    """Metadata, link-local, the CGN range, multicast, and unspecified are always rejected."""

    _resolve_to(monkeypatch, address)
    _record_connections(monkeypatch, behaviors=[200])

    with pytest.raises(ValidationError):
        HttpClient().get(URL, allow_private=allow_private)


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.0.1", "172.16.0.1", "127.0.0.1"])
def test_private_and_loopback_rejected_in_public_mode(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Default (public) mode rejects RFC-1918 and loopback."""

    _resolve_to(monkeypatch, address)
    _record_connections(monkeypatch, behaviors=[200])

    with pytest.raises(ValidationError):
        HttpClient().get(URL)


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.0.1", "127.0.0.1"])
def test_private_and_loopback_permitted_in_private_mode(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """``allow_private=True`` permits RFC-1918 / loopback (self-hosted connections)."""

    _resolve_to(monkeypatch, address)
    connections = _record_connections(monkeypatch, behaviors=[200])

    response = HttpClient().get(URL, allow_private=True)

    assert response.status == 200
    assert connections[0].pinned_address.address == address


def test_caller_cannot_override_the_pinned_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied Host header cannot displace the URL's real host."""

    _resolve_to(monkeypatch, "93.184.216.34")
    connections = _record_connections(monkeypatch, behaviors=[200])

    HttpClient().get(URL, headers={"Host": "evil.example.com", "X-Test": "1"})

    headers = connections[0].requests[0]["headers"]
    assert headers["Host"] == "dav.example.test"
    assert headers["X-Test"] == "1"


def test_dial_falls_back_to_the_next_validated_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first validated IP is unreachable, the next one is tried."""

    _resolve_to(monkeypatch, "93.184.216.34", "93.184.216.35")
    connections = _record_connections(monkeypatch, behaviors=[ConnectionRefusedError("down"), 200])

    response = HttpClient().get(URL)

    assert response.status == 200
    assert len(connections) == 2
    assert connections[0].closed and connections[1].closed


def test_all_addresses_unreachable_raises_the_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If every validated address fails, the transport error (OSError) surfaces.

    A connection failure is distinct from the gate's ValidationError so callers
    (e.g. webhook telemetry) can record it as a transport failure.
    """

    _resolve_to(monkeypatch, "93.184.216.34", "93.184.216.35")
    _record_connections(
        monkeypatch, behaviors=[ConnectionRefusedError("a"), ConnectionRefusedError("b")]
    )

    with pytest.raises(OSError):
        HttpClient().get(URL)


def test_response_without_status_raises_rather_than_defaulting_to_200() -> None:
    """A response carrying no status is an error, not a silent success."""

    class Bare:
        pass

    class WithGetcode:
        def getcode(self) -> int:
            return 204

    with pytest.raises(ValueError):
        _response_status(Bare())
    assert _response_status(WithGetcode()) == 204
