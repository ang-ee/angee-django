"""Native host signal receivers with explicitly scoped registration."""

from __future__ import annotations

import math
from functools import partial
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created

WEB_STATEMENT_TIMEOUT_UID = "angee.asgi.web_statement_timeout"


def web_statement_timeout_ms() -> int:
    """Read ``ANGEE_WEB_STATEMENT_TIMEOUT`` once, at boot, in milliseconds.

    The setting is seconds; 0 disables the bound. Anything that is not zero or a
    positive finite number fails at boot instead of on every connection.
    """

    raw = settings.ANGEE_WEB_STATEMENT_TIMEOUT
    try:
        seconds = float(raw)
    except TypeError, ValueError:
        raise ImproperlyConfigured(f"ANGEE_WEB_STATEMENT_TIMEOUT must be a number of seconds, not {raw!r}.") from None
    if seconds < 0 or not math.isfinite(seconds):
        raise ImproperlyConfigured(
            f"ANGEE_WEB_STATEMENT_TIMEOUT must be zero or a positive number of seconds, not {raw!r}."
        )
    return 0 if seconds == 0 else max(1, round(seconds * 1000))


def bound_web_statements(*, connection: Any, timeout_ms: int, **_signal: Any) -> None:
    """Bound each PostgreSQL statement a web-process connection runs.

    The bound caps every statement a request, subscription or mounted app
    issues; it applies per statement, not per request. Only
    :func:`angee.asgi._application` connects this receiver: Celery workers, migrations,
    imports and other management commands never import this entrypoint and keep
    the server's own timeout.
    """

    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('statement_timeout', %s, false)", [str(timeout_ms)])


def connect_web_statement_timeout() -> None:
    """Apply the request-process policy only when the ASGI host starts."""

    timeout_ms = web_statement_timeout_ms()
    if timeout_ms:
        connection_created.connect(
            partial(bound_web_statements, timeout_ms=timeout_ms),
            weak=False,
            dispatch_uid=WEB_STATEMENT_TIMEOUT_UID,
        )
