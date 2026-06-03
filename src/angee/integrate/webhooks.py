"""Outbound webhook HTTP transport: a signed POST to a public URL, IP-pinned.

The delivery *orchestration* (which subscriptions receive an event) lives on
``WebhookSubscription``'s manager, and a subscription delivers itself via
``WebhookSubscription.deliver``; this module owns only the SSRF-safe HTTP
mechanics, so it imports no models and breaks no cycle.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlunsplit

from django.core.exceptions import ValidationError

from angee.base.net import is_unsafe_address, parse_http_url

HTTP_TIMEOUT_SECONDS = 10
"""Timeout used for outbound webhook POSTs."""

SIGNATURE_HEADER = "X-Angee-Signature"
"""Header carrying the HMAC-SHA256 signature for outbound webhook bodies."""


class WebhookDeliveryError(Exception):
    """Raised when a webhook endpoint returns a non-success HTTP status."""

    def __init__(self, message: str, *, status: str = "") -> None:
        """Record the delivery status when one is available."""

        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class _PinnedAddress:
    """One resolver answer validated for webhook delivery."""

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
        self._delivery_timeout = timeout

    def connect(self) -> None:
        """Open the TCP connection to the pinned resolver answer."""

        self.sock = self._pinned_address.open_socket(timeout=self._delivery_timeout)


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
        self._delivery_timeout = timeout
        self._tls_hostname = tls_hostname
        self._tls_context = context

    def connect(self) -> None:
        """Open TLS to the pinned IP while checking the original hostname."""

        sock = self._pinned_address.open_socket(timeout=self._delivery_timeout)
        try:
            self.sock = self._tls_context.wrap_socket(sock, server_hostname=self._tls_hostname)
        except OSError:
            sock.close()
            raise


class PinnedWebhookClient:
    """Posts a signed body to one public HTTP(S) URL, pinning the resolved IP.

    Resolves the host once, rejects any non-public answer, then dials the pinned
    address so a DNS rebind between check and connect cannot redirect the POST.
    """

    def __init__(self, url: str) -> None:
        """Parse and gate the delivery URL (scheme + host) up front."""

        self._parsed = parse_http_url(url)

    def post(self, *, secret: str, body: bytes) -> str:
        """POST ``body`` signed with ``secret`` and return the HTTP status, or raise."""

        pinned = self._resolve_public_addresses()[0]
        connection = self._connection_for(pinned)
        try:
            connection.request(
                "POST",
                self._request_target,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Host": self._host_header,
                    SIGNATURE_HEADER: self._signature(secret, body),
                },
            )
            response = connection.getresponse()
            status = self._response_status(response)
        finally:
            connection.close()

        if status < 200 or status >= 300:
            raise WebhookDeliveryError(f"HTTP {status}", status=str(status))
        return str(status)

    @property
    def _hostname(self) -> str:
        """Return the validated delivery hostname."""

        if self._parsed.hostname is None:  # pragma: no cover - parse_http_url guarantees a host
            raise ValidationError("Webhook URL must include a host.")
        return self._parsed.hostname

    @property
    def _port(self) -> int:
        """Return the effective delivery port."""

        if self._parsed.port is not None:
            return self._parsed.port
        return http.client.HTTPS_PORT if self._parsed.scheme == "https" else http.client.HTTP_PORT

    @property
    def _request_target(self) -> str:
        """Return the origin-form request target."""

        return urlunsplit(("", "", self._parsed.path or "/", self._parsed.query, ""))

    @property
    def _host_header(self) -> str:
        """Return the Host header for the delivery URL."""

        hostname = self._hostname
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        if self._parsed.port is None or self._parsed.port == self._port:
            return hostname
        return f"{hostname}:{self._parsed.port}"

    def _resolve_public_addresses(self) -> tuple[_PinnedAddress, ...]:
        """Resolve the host once and return validated public resolver answers."""

        try:
            results = socket.getaddrinfo(self._hostname, self._port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValidationError("Webhook URL host could not be resolved.") from error

        addresses: list[_PinnedAddress] = []
        seen: set[tuple[int, str, int]] = set()
        for family, socktype, proto, _canonname, sockaddr in results:
            address = ipaddress.ip_address(sockaddr[0])
            if is_unsafe_address(address):
                raise ValidationError("Webhook URL host must resolve only to public IP addresses.")
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
            raise ValidationError("Webhook URL host could not be resolved.")
        return tuple(addresses)

    def _connection_for(self, pinned_address: _PinnedAddress) -> http.client.HTTPConnection:
        """Return a connection that dials ``pinned_address`` and preserves URL host semantics."""

        if self._parsed.scheme == "https":
            return _PinnedHTTPSConnection(
                self._hostname,
                port=self._port,
                timeout=HTTP_TIMEOUT_SECONDS,
                pinned_address=pinned_address,
                tls_hostname=self._hostname,
                context=ssl.create_default_context(),
            )
        return _PinnedHTTPConnection(
            self._hostname,
            port=self._port,
            timeout=HTTP_TIMEOUT_SECONDS,
            pinned_address=pinned_address,
        )

    @staticmethod
    def _signature(secret: str, body: bytes) -> str:
        """Return the Angee webhook signature header value for ``body``."""

        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    @staticmethod
    def _response_status(response: Any) -> int:
        """Return the integer HTTP status from a stdlib response or test double."""

        status = getattr(response, "status", None)
        if status is not None:
            return int(status)
        getcode = getattr(response, "getcode", None)
        if callable(getcode):
            return int(getcode())
        return 200
