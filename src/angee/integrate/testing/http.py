"""Scripted outbound HTTP for tests of integrate's network callers."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch
from urllib import parse

import httpx2

from angee.integrate.http import HttpClient


@contextmanager
def mock_transport(handler: Callable[[httpx2.Request], httpx2.Response]) -> Iterator[list[httpx2.Request]]:
    """Answer every outbound request with ``handler``; yield the requests it received, in order.

    ``httpx2.MockTransport`` replaces the network at ``HttpClient.transport_factory``,
    the one transport the integration backends and the OAuth client build, so URL
    gating, headers, redirects, byte caps and error mapping still run. ``handler``
    raises an ``httpx2`` transport error to model an unreachable host.
    """

    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    def transport(*, allow_private: bool) -> httpx2.BaseTransport:
        del allow_private
        return httpx2.MockTransport(answer)

    with patch.object(HttpClient, "transport_factory", staticmethod(transport)):
        yield requests


def request_fields(request: httpx2.Request) -> dict[str, Any]:
    """Return the fields of one JSON or form-encoded request body, such as an OAuth token request."""

    if request.headers.get("content-type", "").startswith("application/json"):
        return dict(json.loads(request.content))
    return dict(parse.parse_qsl(request.content.decode()))
