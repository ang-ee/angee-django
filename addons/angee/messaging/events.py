"""Public event seams emitted by messaging write owners."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps
from django.dispatch import Signal

if TYPE_CHECKING:
    from angee.messaging.models import ThreadedModelMixin

message_ingested = Signal()
"""Sent inside the landing transaction for each new message activity.

Receivers get ``sender`` (the concrete Message model) and ``instance``. A
provider replay whose row and thread are unchanged emits nothing; a newly
created internal/external message, or an existing provider message newly moved
onto a thread, emits once. Composition addons may react transactionally without
being named by messaging.
"""


def end_unreadable_follows(*, instance: ThreadedModelMixin, using: str, **_kwargs: object) -> None:
    """End record follows synchronously after the access owner revokes a grant."""

    apps.get_model("messaging", "ThreadFollower").objects.db_manager(using).end_unreadable_for_record(
        instance, role=instance.thread_attachment_role,
    )
