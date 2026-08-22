"""Pre-elevation checks for curated anonymous HTTP ingress.

Anonymous writes are deliberately outside the normal authenticated GraphQL
surface.  Every such endpoint must pass this utility before entering
``system_context``: it bounds the raw body, spends a per-IP rate-limit token,
parses a JSON object, rejects undeclared envelope fields, checks a honeypot, and
optionally delegates capability-token validation to the endpoint owner.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.core.cache import DEFAULT_CACHE_ALIAS, caches
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest

AnonymousIngressTokenHook = Callable[[HttpRequest, Mapping[str, Any]], bool | None]


class AnonymousIngressRejected(Exception):
    """A stable client-facing rejection raised before any actor elevation."""

    status_code = 400
    code = "invalid_request"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AnonymousIngressTooLarge(AnonymousIngressRejected):
    """The request body exceeds the endpoint's byte cap."""

    status_code = 413
    code = "body_too_large"


class AnonymousIngressUnsupportedMedia(AnonymousIngressRejected):
    """The endpoint accepts only JSON request bodies."""

    status_code = 415
    code = "unsupported_media_type"


class AnonymousIngressRateLimited(AnonymousIngressRejected):
    """The source IP exhausted this endpoint's fixed-window burst."""

    status_code = 429
    code = "rate_limited"


class AnonymousIngressTokenRejected(AnonymousIngressRejected):
    """The optional endpoint-owned capability-token hook denied the request."""

    status_code = 403
    code = "invalid_token"


class AnonymousIngressUnavailable(AnonymousIngressRejected):
    """A required guard dependency failed, so ingress fails closed."""

    status_code = 503
    code = "guard_unavailable"


@dataclass(frozen=True)
class AnonymousIngressRateLimit:
    """One fixed-window rate-limit bucket shared by an endpoint scope."""

    scope: str
    burst: int
    window_seconds: int
    cache_alias: str = DEFAULT_CACHE_ALIAS

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("Anonymous ingress rate-limit scope cannot be blank.")
        if self.burst < 1:
            raise ValueError("Anonymous ingress rate-limit burst must be positive.")
        if self.window_seconds < 1:
            raise ValueError("Anonymous ingress rate-limit window must be positive.")


@dataclass(frozen=True)
class AnonymousIngressPolicy:
    """The reusable checks one curated JSON endpoint opts into."""

    max_body_bytes: int
    allowed_fields: frozenset[str]
    rate_limit: AnonymousIngressRateLimit
    honeypot_field: str | None = None
    token_hook: AnonymousIngressTokenHook | None = None

    def __post_init__(self) -> None:
        if self.max_body_bytes < 1:
            raise ValueError("Anonymous ingress body cap must be positive.")
        if not self.allowed_fields:
            raise ValueError("Anonymous ingress must declare its envelope fields.")
        if self.honeypot_field and self.honeypot_field not in self.allowed_fields:
            raise ValueError("The anonymous ingress honeypot must be an allowed envelope field.")


@dataclass(frozen=True)
class AnonymousIngress:
    """A checked JSON envelope safe for an endpoint to interpret."""

    payload: Mapping[str, Any]
    body_size: int
    dropped: bool = False


def guard_anonymous_ingress(
    request: HttpRequest,
    policy: AnonymousIngressPolicy,
) -> AnonymousIngress:
    """Run every anonymous-ingress check and return the parsed JSON envelope.

    The caller must invoke this before any ``system_context``.  ``REMOTE_ADDR``
    is the source identity: forwarding headers are client-controlled unless a
    deployment's trusted proxy has already rewritten the WSGI/ASGI peer address.
    Cache failure rejects with 503 rather than silently disabling rate limiting.
    A filled honeypot is returned as ``dropped=True`` so the endpoint can emit
    its normal success-shaped response without performing a write.
    """

    if request.content_type != "application/json":
        raise AnonymousIngressUnsupportedMedia("Content-Type must be application/json.")
    body = _bounded_body(request, policy.max_body_bytes)
    _spend_rate_limit(request, policy.rate_limit)
    payload = _json_object(body)
    extras = sorted(set(payload).difference(policy.allowed_fields))
    if extras:
        rendered = ", ".join(extras)
        raise AnonymousIngressRejected(f"Unknown request field(s): {rendered}.")
    if policy.honeypot_field and payload.get(policy.honeypot_field) not in (None, ""):
        return AnonymousIngress(payload=payload, body_size=len(body), dropped=True)
    if policy.token_hook is not None:
        try:
            accepted = policy.token_hook(request, payload)
        except AnonymousIngressRejected:
            raise
        except Exception as error:
            raise AnonymousIngressTokenRejected("The ingress token is invalid or expired.") from error
        if accepted is False:
            raise AnonymousIngressTokenRejected("The ingress token is invalid or expired.")
    return AnonymousIngress(payload=payload, body_size=len(body))


def _bounded_body(request: HttpRequest, max_body_bytes: int) -> bytes:
    raw_length = request.META.get("CONTENT_LENGTH")
    if raw_length not in (None, ""):
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError) as error:
            raise AnonymousIngressRejected("Content-Length must be an integer.") from error
        if content_length < 0:
            raise AnonymousIngressRejected("Content-Length cannot be negative.")
        if content_length > max_body_bytes:
            raise AnonymousIngressTooLarge(f"Request body exceeds {max_body_bytes} bytes.")
    try:
        body = request.body
    except RequestDataTooBig as error:
        raise AnonymousIngressTooLarge(f"Request body exceeds {max_body_bytes} bytes.") from error
    if len(body) > max_body_bytes:
        raise AnonymousIngressTooLarge(f"Request body exceeds {max_body_bytes} bytes.")
    return body


def _json_object(body: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda token: _raise_invalid_number(token),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AnonymousIngressRejected("Request body must be a valid UTF-8 JSON object.") from error
    if not isinstance(value, dict):
        raise AnonymousIngressRejected("Request body must be a JSON object.")
    return value


def _raise_invalid_number(token: str) -> None:
    raise ValueError(f"JSON number {token!r} is not finite.")


def _spend_rate_limit(request: HttpRequest, limit: AnonymousIngressRateLimit) -> None:
    remote_addr = str(request.META.get("REMOTE_ADDR") or "unknown")
    digest = hashlib.sha256(f"{limit.scope}\0{remote_addr}".encode()).hexdigest()
    key = f"angee:anonymous-ingress:{digest}"
    cache = caches[limit.cache_alias]
    try:
        if cache.add(key, 1, timeout=limit.window_seconds):
            count = 1
        else:
            try:
                count = int(cache.incr(key))
            except ValueError:
                # The key may expire between ``add`` and ``incr``. Start the new
                # fixed window without granting two tokens to the same request.
                if cache.add(key, 1, timeout=limit.window_seconds):
                    count = 1
                else:
                    count = int(cache.incr(key))
    except Exception as error:
        raise AnonymousIngressUnavailable("Anonymous ingress rate limiting is unavailable.") from error
    if count > limit.burst:
        raise AnonymousIngressRateLimited(
            "Anonymous ingress rate limit exceeded.",
            retry_after=limit.window_seconds,
        )
