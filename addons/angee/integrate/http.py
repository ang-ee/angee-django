"""Shared SSRF-pinned outbound HTTP for integration backends.

The single owner of "make one outbound HTTP request." The transport is httpx over
a custom httpcore network backend (:class:`_PinnedBackend`) that resolves the host
once and dials a validated IP — closing the resolve-then-connect (DNS-rebind) gap
— while httpcore's ``start_tls(server_hostname=…)`` keeps TLS verification on the
original hostname. The address judgement is owned by ``net.is_unsafe_address``;
this module only pins and dials.

One outbound-network policy lives here and everything composes it: integration
backends reach it as ``self.http`` (:class:`HttpClientMixin`), and the OAuth client
hands the same :class:`PinnedTransport` to Authlib. TLS trusts the system store
(``ssl.create_default_context``) per docs/backend/guidelines.md, not httpx's bundled
certifi default.

By default only public addresses are dialled. ``allow_private=True`` is the
operator-configured-connection policy — a self-hosted host on a private network:
it permits RFC-1918 / loopback so those connections work, but still rejects the
SSRF escapes that have no legitimate target either way — cloud metadata (the
well-known IPs, link-local ``169.254/16``, and the RFC 6598 shared range that
front metadata services), multicast, and unspecified. Redirects are not followed
unless ``follow_redirects=True``; each hop re-enters the pinned backend, so
following stays safe.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import httpcore
import httpx
from django.core.exceptions import ValidationError

from .net import canonical_address, is_unsafe_address, parse_http_url, resolved_addresses

HTTP_TIMEOUT_SECONDS = 10
"""Default timeout (seconds) for one outbound request."""

_DOWNLOAD_CHUNK_BYTES = 64 * 1024
"""Maximum chunk read while enforcing a capped streamed download."""

_SSL_CONTEXT = ssl.create_default_context()
"""One shared system-trust-store TLS context reused by every pinned transport, so the
CA bundle is parsed once rather than on every outbound request."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One outbound HTTP response: the status code, raw body bytes, and headers."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return whether the status is a 2xx success."""

        return 200 <= self.status < 300

    def json(self) -> Any:
        """Return the body parsed as JSON (``None`` for an empty body)."""

        return json.loads(self.body or b"null")

    def header(self, name: str) -> str:
        """Return one response header by case-insensitive name, or ``""``."""

        return self.headers.get(name.lower(), "")


class _PinnedBackend(httpcore.SyncBackend):
    """httpcore backend that resolves once, rejects SSRF-unsafe addresses, and dials
    a validated IP — so a DNS rebind between check and connect cannot move the
    request. ``net.is_unsafe_address`` owns the judgement; this only pins and dials.
    """

    def __init__(self, *, allow_private: bool) -> None:
        """Bind the address policy for every connection this backend dials."""

        super().__init__()
        self._allow_private = allow_private

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        """Resolve ``host``, reject unsafe addresses, and dial a validated IP.

        ``host`` is the origin hostname; httpcore later calls
        ``start_tls(server_hostname=host)``, so dialing a validated IP here leaves
        SNI and certificate verification on the real hostname.
        """

        last_error: OSError | None = None
        for address in self._validated_addresses(host, port):
            try:
                return super().connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_error = _as_os_error(exc)
        # Every validated address failed to connect: surface a transport ``OSError``
        # (distinct from the gate's ``ValidationError``) so callers — e.g. webhook
        # telemetry — record it as a transport failure, not an SSRF rejection.
        raise last_error if last_error is not None else ConnectionError(f"{host!r} could not be reached")

    def _validated_addresses(self, host: str, port: int) -> tuple[Any, ...]:
        """Return every resolved address for ``host``, or raise if any is unsafe."""

        addresses = tuple(canonical_address(address) for address in resolved_addresses(host, port))
        for address in addresses:
            if is_unsafe_address(address, allow_private=self._allow_private):
                raise ValidationError("URL host resolves to an address that is not allowed.")
        return addresses


class PinnedTransport(httpx.HTTPTransport):
    """An httpx transport whose connections are SSRF-pinned and whose TLS trusts the
    system store. The shared pinned-httpx primitive: :class:`HttpClient` issues
    requests over it, and the OAuth client hands it to Authlib's ``OAuth2Client``.
    """

    def __init__(self, *, allow_private: bool = False) -> None:
        """Build a pinned transport; ``allow_private`` permits self-hosted RFC-1918 hosts."""

        super().__init__(verify=_SSL_CONTEXT, retries=0)
        # httpx.HTTPTransport builds the connection pool but exposes no public seam to
        # inject a network backend, so swap httpcore's private ``_network_backend``
        # before any request. Fail loudly if a future httpx/httpcore renames it: a
        # silent fallback to the default backend would disable SSRF pinning (fail-open).
        pool = self._pool
        if not hasattr(pool, "_network_backend"):
            raise RuntimeError(
                "httpx/httpcore changed: ConnectionPool exposes no '_network_backend'; SSRF "
                "pinning would be disabled — re-verify PinnedTransport against the new version."
            )
        pool._network_backend = _PinnedBackend(allow_private=allow_private)


class HttpClient:
    """A reusable SSRF-pinned outbound HTTP client over httpx.

    Stateless to the caller — one instance per backend is fine. Each call gates the
    URL, pins via :class:`PinnedTransport`, and dials the validated IP; a DNS rebind
    between check and connect cannot redirect it. A caller-supplied ``Host`` header
    cannot displace the URL's real host. Redirects are followed only when
    ``follow_redirects=True`` (each hop re-validates).
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_private: bool = False,
        follow_redirects: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """GET ``url`` and return the response."""

        return self.request(
            "GET", url, headers=headers, allow_private=allow_private, follow_redirects=follow_redirects, timeout=timeout
        )

    def download_capped(
        self,
        url: str,
        *,
        cap: int,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        allow_private: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> bytes | None:
        """Stream a pinned GET without reading more than ``cap`` bytes."""

        if cap <= 0:
            return None
        parse_http_url(url)
        with httpx.Client(
            transport=PinnedTransport(allow_private=allow_private),
            timeout=timeout,
        ) as client:
            with client.stream(
                "GET",
                url,
                headers=_without_host(headers),
                follow_redirects=follow_redirects,
            ) as response:
                if not response.is_success:
                    return None
                try:
                    content_length = int(response.headers.get("content-length") or 0)
                except ValueError:
                    content_length = 0
                if content_length > cap:
                    return None
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes(
                    chunk_size=min(_DOWNLOAD_CHUNK_BYTES, cap + 1),
                ):
                    size += len(chunk)
                    if size > cap:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        allow_private: bool = False,
        follow_redirects: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """POST ``body`` to ``url`` and return the response."""

        return self.request(
            "POST",
            url,
            headers=headers,
            body=body,
            allow_private=allow_private,
            follow_redirects=follow_redirects,
            timeout=timeout,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        allow_private: bool = False,
        follow_redirects: bool = False,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ) -> HttpResponse:
        """Send one pinned request to ``url`` and return the response.

        Raises ``ValidationError`` when the URL or a resolved address is rejected by
        the SSRF gate, and ``OSError`` when every validated address is unreachable.
        """

        parse_http_url(url)  # scheme/host gate; the pinned backend judges the address
        with httpx.Client(transport=PinnedTransport(allow_private=allow_private), timeout=timeout) as client:
            response = client.request(
                method, url, headers=_without_host(headers), content=body, follow_redirects=follow_redirects
            )
        return HttpResponse(
            status=_response_status(response),
            body=response.content,
            headers={name.lower(): value for name, value in response.headers.items()},
        )


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


def _without_host(headers: dict[str, str] | None) -> dict[str, str]:
    """Return ``headers`` without any ``Host`` entry so it cannot displace the URL host."""

    return {name: value for name, value in (headers or {}).items() if name.lower() != "host"}


def _as_os_error(exc: Exception) -> OSError:
    """Return the underlying ``OSError`` for a connect failure (httpcore wraps it)."""

    if isinstance(exc, OSError):
        return exc
    cause = exc.__cause__
    if isinstance(cause, OSError):
        return cause
    return OSError(str(exc))


def _response_status(response: Any) -> int:
    """Return the integer HTTP status from a response.

    A response that carries no status is anomalous; raise rather than defaulting to
    200, which would mask a failure as success.
    """

    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    raise ValueError("HTTP response carries no status code.")
