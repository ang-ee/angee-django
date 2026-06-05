"""Outbound-URL safety: HTTP(S) scheme allow-list + SSRF address validation.

The single owner for "is this URL safe to call outbound." Used by the integrate
webhook delivery layer (as a model field validator and a per-delivery check) and
the resources remote-file fetcher. Callers that open a connection should resolve
once and dial the validated address (IP-pinning) to close the resolve-then-connect
gap; this module owns the allow-list and the address judgement.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import cast
from urllib.parse import SplitResult, urlsplit

from django.core.exceptions import ValidationError

ALLOWED_HTTP_SCHEMES = frozenset({"http", "https"})
"""URL schemes accepted for outbound HTTP calls."""

METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
"""Well-known cloud metadata service addresses that must never receive callbacks."""

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_http_url(url: str) -> SplitResult:
    """Return a parsed HTTP(S) URL with a well-formed port and host, or raise ``ValidationError``.

    The scheme + host gate shared by the webhook delivery layer (which then pins
    the resolved address) and ``validate_public_url`` (which then checks every
    resolved address). Callers that need the public-IP check call the latter.
    """

    try:
        parsed = urlsplit(url)
        parsed.port  # noqa: B018 — property access validates the port is well-formed
    except ValueError as error:
        raise ValidationError("URL is invalid.") from error
    if parsed.scheme not in ALLOWED_HTTP_SCHEMES:
        raise ValidationError("URL must use http or https.")
    if not parsed.hostname:
        raise ValidationError("URL must include a host.")
    return parsed


def validate_public_url(value: object) -> None:
    """Raise ``ValidationError`` unless ``value`` is an HTTP(S) URL resolving only to public IPs."""

    parsed = parse_http_url(str(value))
    for address in resolved_addresses(cast(str, parsed.hostname), parsed.port):
        if is_unsafe_address(address):
            raise ValidationError("URL host must resolve only to public IP addresses.")


def resolved_addresses(hostname: str, port: int | None) -> tuple[_IpAddress, ...]:
    """Return every IP address currently resolved for ``hostname``."""

    try:
        return (ipaddress.ip_address(hostname),)
    except ValueError:
        pass

    try:
        results = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise ValidationError("URL host could not be resolved.") from error

    addresses = [ipaddress.ip_address(result[4][0]) for result in results]
    if not addresses:
        raise ValidationError("URL host could not be resolved.")
    return tuple(dict.fromkeys(addresses))


def is_unsafe_address(address: _IpAddress) -> bool:
    """Return whether ``address`` is forbidden for outbound calls."""

    return (
        address in METADATA_IPS
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    )
