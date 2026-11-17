"""The deployment's public browser origin: one rule for CSRF trust and request-less links."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


def public_origin(value: object, *, debug: bool) -> str | None:
    """Return ``value`` as a bare public origin, or ``None`` when it is not one.

    A public origin is ``https://host[:port]`` (``http`` only under ``DEBUG``)
    whose host Django's URL validator accepts, with a valid port and no
    wildcard, user info, path, query or fragment. A trailing slash is dropped.
    """

    origin = str(value or "").strip().rstrip("/")
    if not origin:
        return None
    parts = urlsplit(origin)
    try:
        parts.port  # noqa: B018 -- an invalid port raises here
        URLValidator(schemes=["http", "https"] if debug else ["https"])(origin)
    except ValueError, ValidationError:
        return None
    if "@" in parts.netloc or "*" in parts.netloc or parts.path or parts.query or parts.fragment:
        return None
    return origin
