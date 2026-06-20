"""Shared SSRF-pinned outbound HTTP client for integration backends.

The single owner of "make one outbound HTTP request from a backend." It resolves
the host once and dials the validated IP — closing the resolve-then-connect SSRF
gap — while verifying the original TLS hostname, over ``net.py``'s address
judgement. A backend composes :class:`HttpClientMixin` to reach it as
``self.http``; the webhook delivery layer composes it and adds only its
signature, and the github backend composes it for read GETs — so the pinning
lives in exactly one place.

By default only public addresses are dialled. A backend whose connection URL is
an operator-configured server — e.g. a self-hosted CardDAV host on a private
network — passes ``allow_private=True``: that still rejects cloud-metadata,
multicast, and unspecified addresses, but permits RFC-1918 / loopback /
link-local hosts so self-hosted connections work.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from functools import cached_property
from typing import Any
from urllib.parse import SplitResult, urlunsplit

from django.core.exceptions import ValidationError

from .net import METADATA_IPS, canonical_address, is_unsafe_address, parse_http_url

HTTP_TIMEOUT_SECONDS = 10
"""Default timeout (seconds) for one outbound request."""

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One outbound HTTP response: the status code and the raw body bytes."""

    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        """Return whether the status is a 2xx success."""

        return 200 <= self.status < 300

    def json(self) -> Any:
        """Return the body parsed as JSON (``None`` for an empty body)."""

        return json.loads(self.body or b"null")


def _address_blocked(address: _IpAddress, *, allow_private: bool) -> bool:
    """Return whether ``address`` must not be dialled under the chosen policy."""

    address = canonical_address(address)
    if allow_private:
        # Operator-configured connection URL: permit private/loopback/link-local,
        # but never cloud-metadata or multicast/unspecified.
        return address in METADATA_IPS or address.is_multicast or address.is_unspecified
    return is_unsafe_address(address)


@dataclass(frozen=True)
class _PinnedAddress:
    """One resolver answer validated for an outbound call."""

    family: socket.AddressFamily
    socktype: socket.SocketKind
    proto: int
    address: str
    sockaddr: Any

    def open_socket(self, *, timeout: int) -> socket.socket:
        """Open one socket to this previously validated resolver answer."""

        sock = socket.socket(self.family, self.socktype, self.proto)
        try:
            sock.settimeout(timeout)
            sock.connect(self.sockaddr)
        except OSError:
            sock.close()
            raise
        return sock


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials the validated IP instead of resolving host again."""

    def __init__(self, host: str, *, port: int, timeout: int, pinned_address: _PinnedAddress) -> None:
        """Store the pinned resolver answer while preserving the original host."""

        super().__init__(host, port=port, timeout=timeout)
        self._pinned_address = pinned_address
        self._dial_timeout = timeout

    def connect(self) -> None:
        """Open the TCP connection to the pinned resolver answer."""

        self.sock = self._pinned_address.open_socket(timeout=self._dial_timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials the validated IP but verifies the original host."""

    def __init__(
        self,
        host: str,
        *,
        port: int,
        timeout: int,
        pinned_address: _PinnedAddress,
        tls_hostname: str,
        context: ssl.SSLContext,
    ) -> None:
        """Store the pinned address and TLS hostname for certificate validation."""

        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_address = pinned_address
        self._dial_timeout = timeout
        self._tls_hostname = tls_hostname
        self._tls_context = context

    def connect(self) -> None:
        """Open TLS to the pinned IP while checking the original hostname."""

        sock = self._pinned_address.open_socket(timeout=self._dial_timeout)
        try:
            self.sock = self._tls_context.wrap_socket(sock, server_hostname=self._tls_hostname)
        except OSError:
            sock.close()
            raise


class HttpClient:
    """A reusable SSRF-pinned outbound HTTP client.

    Stateless — one instance per backend is fine. Each call gates the URL, resolves
    once, pins the validated IP, and dials it; a DNS rebind between check and
    connect cannot redirect the request.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_private: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """GET ``url`` and return the response."""

        return self.request("GET", url, headers=headers, allow_private=allow_private, timeout=timeout)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        allow_private: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """POST ``body`` to ``url`` and return the response."""

        return self.request(
            "POST", url, headers=headers, body=body, allow_private=allow_private, timeout=timeout
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        allow_private: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """Send one request to ``url`` over a pinned connection and return the response."""

        parsed = parse_http_url(url)
        pinned = self._resolve(parsed, allow_private=allow_private)[0]
        connection = self._connection_for(parsed, pinned, timeout=timeout)
        request_headers = {"Host": _host_header(parsed), **(headers or {})}
        try:
            connection.request(method, _request_target(parsed), body=body, headers=request_headers)
            response = connection.getresponse()
            return HttpResponse(status=_response_status(response), body=response.read())
        finally:
            connection.close()

    def _resolve(self, parsed: SplitResult, *, allow_private: bool) -> tuple[_PinnedAddress, ...]:
        """Resolve the host once and return the validated resolver answers."""

        host = str(parsed.hostname)
        port = _port(parsed)
        try:
            results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValidationError("URL host could not be resolved.") from error

        addresses: list[_PinnedAddress] = []
        seen: set[tuple[int, str, int]] = set()
        for family, socktype, proto, _canonname, sockaddr in results:
            address = ipaddress.ip_address(sockaddr[0])
            if _address_blocked(address, allow_private=allow_private):
                raise ValidationError("URL host resolves to an address that is not allowed.")
            key = (int(family), str(address), int(proto))
            if key in seen:
                continue
            seen.add(key)
            addresses.append(
                _PinnedAddress(
                    family=socket.AddressFamily(family),
                    socktype=socket.SocketKind(socktype),
                    proto=proto,
                    address=str(address),
                    sockaddr=sockaddr,
                )
            )
        if not addresses:
            raise ValidationError("URL host could not be resolved.")
        return tuple(addresses)

    def _connection_for(
        self, parsed: SplitResult, pinned_address: _PinnedAddress, *, timeout: int
    ) -> http.client.HTTPConnection:
        """Return a connection that dials ``pinned_address`` and preserves URL host semantics."""

        host = str(parsed.hostname)
        if parsed.scheme == "https":
            return _PinnedHTTPSConnection(
                host,
                port=_port(parsed),
                timeout=timeout,
                pinned_address=pinned_address,
                tls_hostname=host,
                context=ssl.create_default_context(),
            )
        return _PinnedHTTPConnection(
            host,
            port=_port(parsed),
            timeout=timeout,
            pinned_address=pinned_address,
        )


def _port(parsed: SplitResult) -> int:
    """Return the effective port for a parsed URL."""

    if parsed.port is not None:
        return parsed.port
    return http.client.HTTPS_PORT if parsed.scheme == "https" else http.client.HTTP_PORT


def _request_target(parsed: SplitResult) -> str:
    """Return the origin-form request target (path + query) for a parsed URL."""

    return urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


def _host_header(parsed: SplitResult) -> str:
    """Return the Host header value for a parsed URL, bracketing IPv6 and adding a non-default port."""

    hostname = str(parsed.hostname)
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    if parsed.port is None or parsed.port == _port(parsed):
        return hostname
    return f"{hostname}:{parsed.port}"


def _response_status(response: Any) -> int:
    """Return the integer HTTP status from a stdlib response or test double."""

    status = getattr(response, "status", None)
    if status is not None:
        return int(status)
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        return int(getcode())
    return 200


class HttpClientMixin:
    """Gives an integration backend the shared SSRF-pinned client as ``self.http``.

    Compose it into a backend that makes outbound calls (alongside its
    ``BridgeImpl`` / ``Client`` base) so it calls ``self.http.get(url, headers=…)``
    rather than opening its own connection. HTTP stays opt-in this way — an
    implementation that does no I/O carries no client.
    """

    @cached_property
    def http(self) -> HttpClient:
        """Return this backend's shared outbound HTTP client."""

        return HttpClient()
