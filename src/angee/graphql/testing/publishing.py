"""Capture committed GraphQL change-feed delivery without a channel layer."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from django.db import models


@contextmanager
def capture_broadcasts(
    *, observer: Callable[[type[models.Model], dict[str, Any]], None] | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Collect wire payloads in delivery order, optionally observing each at commit.

    The block replaces transport only; normal projection, broadcast policy and
    transaction callbacks still run. Keep it open through the transaction's commit
    (or Django's ``captureOnCommitCallbacks(execute=True)`` in a ``TestCase``).
    Like ``unittest.mock.patch``, capture is process-wide and restores on exit.
    """

    payloads: list[dict[str, Any]] = []

    def capture(model: type[models.Model], payload: dict[str, Any]) -> None:
        payloads.append(payload)
        if observer is not None:
            observer(model, payload)

    with patch("angee.graphql.publishing._broadcast", side_effect=capture):
        yield payloads
