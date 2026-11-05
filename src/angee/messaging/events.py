"""Public event seams emitted by messaging write owners."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from django.apps import apps
from django.dispatch import Signal

from angee.base.signals import mute_changes

if TYPE_CHECKING:
    from angee.messaging.models import ThreadedModelMixin

_historical_import: ContextVar[bool] = ContextVar("messaging_historical_import", default=False)


@contextmanager
def historical_import() -> Iterator[None]:
    """Land history without live message, GraphQL change, timeline, or workflow hooks."""
    token = _historical_import.set(True)
    try:
        with mute_changes():
            yield
    finally:
        _historical_import.reset(token)


def historical_import_active() -> bool:
    """Whether the current ingest belongs to an explicit historical import."""
    return _historical_import.get()

message_landed = Signal()
"""Sent for each changed envelope inside that message's landing transaction.

Transactional receivers use this event to keep their state with that envelope.
"""

message_ingested = Signal()
"""Sent for each changed live envelope inside its ingest batch transaction.

``instance`` is that row; the final send carries all changed rows in
``created_batch``. Earlier sends carry an empty tuple so batch consumers act
once. Provider replays whose row and thread are unchanged emit nothing. Authored
posts retain the one-row ``instance`` signature. The identity observation
callback remains batched after the envelopes land.
"""


def end_unreadable_follows(*, instance: ThreadedModelMixin, using: str, **_kwargs: object) -> None:
    """End record follows synchronously after the access owner revokes a grant."""

    apps.get_model("messaging", "ThreadFollower").objects.db_manager(using).end_unreadable_for_record(
        instance, role=instance.thread_attachment_role,
    )
