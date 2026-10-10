"""Mail evidence messaging contributes to parties' handle suggestions.

Parties declares the ``ANGEE_PARTIES_SHARED_SENDERS`` and ``ANGEE_PARTIES_SIGNINGS``
hooks and reads no messaging model; messaging's autoconfig appends these.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from django.apps import apps

from angee.parties.managers import Signing


def shared_senders() -> Iterable[Any]:
    """Return the handles that speak for a list or a system: senders of only automated mail."""

    return apps.get_model("messaging", "Message").objects.automated_sender_ids()


def signings() -> Iterator[Signing]:
    """Yield each signature fragment with a sender who signed it."""

    return apps.get_model("messaging", "Part").objects.signings()
