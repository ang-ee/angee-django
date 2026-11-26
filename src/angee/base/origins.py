"""The deployment's public browser origin: one rule for CSRF trust and request-less links."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import URLValidator

from angee.base.exceptions import DomainError


class PublicOriginUnavailable(DomainError, ImproperlyConfigured):
    """This deployment has no public origin to build a link with: ``ANGEE_PUBLIC_ORIGIN`` is unset or not one."""

    code = "PUBLIC_ORIGIN_UNAVAILABLE"


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


def configured_public_origin() -> str:
    """Return this deployment's public origin, or raise :class:`PublicOriginUnavailable`.

    The origin is ``settings.ANGEE_PUBLIC_ORIGIN`` as :func:`public_origin` gives
    it under ``settings.DEBUG``: bare, without a trailing slash. Code that builds a
    link without a request, or judges one, calls this rather than reading the
    setting itself. The refusal is a ``DomainError`` with a stable code and an
    ``ImproperlyConfigured``: a caller that offers the link to a person turns it
    into its own refusal, and one that only judges links treats it as no origin.
    """

    # A bare Django host that installs angee.base without its autoconfig declares no setting.
    origin = public_origin(getattr(settings, "ANGEE_PUBLIC_ORIGIN", ""), debug=settings.DEBUG)
    if origin is None:
        raise PublicOriginUnavailable
    return origin
