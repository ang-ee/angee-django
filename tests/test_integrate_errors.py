"""The shared integration failure classification and retry backoff."""

from __future__ import annotations

import errno
import socket
import ssl
from datetime import timedelta

import httpx2
import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError

from angee.integrate.errors import (
    IntegrationError,
    doubling_backoff,
    is_transient_failure,
    transient_retry_delay,
)
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.session_process import SessionProcessError


def _status_error(status: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://vendor.example/")
    return httpx2.HTTPStatusError("status", request=request, response=httpx2.Response(status, request=request))


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (IntegrationError("refused"), False),
        (IntegrationError("busy", transient=True), True),
        (IntegrationError("throttled", retry_after=timedelta(seconds=5)), True),
        (OAuthFlowError("provider_unavailable", 503), True),
        (OAuthFlowError("invalid_state", 400), False),
        (SessionProcessError(-11), True),
        (ConnectionError("Connection to Telegram failed 5 time(s)"), True),
        (TimeoutError(), True),
        (socket.gaierror(8, "nodename nor servname provided"), True),
        (OSError(12, "Cannot allocate memory"), True),
        (OperationalError("the connection is closed"), True),
        (httpx2.ConnectError("refused"), True),
        (_status_error(503), True),
        (_status_error(404), False),
        (ValueError("bad input"), False),
        (ValidationError("bad config"), False),
        (ssl.SSLCertVerificationError("certificate verify failed"), False),
        (PermissionError(13, "Permission denied"), False),
        (FileNotFoundError(2, "No such file or directory", "signal-cli"), False),
        (OSError("dlopen(libneonize.dylib): image not found"), False),
        (OSError(errno.ENOBUFS, "No buffer space available"), True),
    ],
)
def test_is_transient_failure(error: BaseException, transient: bool) -> None:
    """Declared transience wins; only failures a later attempt can outlive are retryable."""

    assert is_transient_failure(error) is transient


def test_doubling_backoff_doubles_from_one_minute_to_its_cap() -> None:
    """Attempt one waits a minute; the delay doubles and never exceeds the cap."""

    cap = timedelta(hours=1)
    assert [doubling_backoff(attempt, cap=cap) for attempt in (0, 1, 2, 3, 7, 50)] == [
        timedelta(minutes=1),
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=4),
        cap,
        cap,
    ]


def test_transient_retry_delay_is_lengthened_by_retry_after_only() -> None:
    """A provider horizon raises the floor; it never shortens the backoff."""

    cap = timedelta(hours=1)
    long_hint = IntegrationError("wait", retry_after=timedelta(hours=2))
    short_hint = IntegrationError("wait", retry_after=timedelta(seconds=5))
    assert transient_retry_delay(long_hint, 1, cap=cap) == timedelta(hours=2)
    assert transient_retry_delay(short_hint, 3, cap=cap) == timedelta(minutes=4)
    assert transient_retry_delay(ConnectionError(), 2, cap=cap) == timedelta(minutes=2)
