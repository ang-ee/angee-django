"""Chatter messages and source conversations on the record timeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.identity import public_id_of
from angee.messaging.models import ThreadedModelMixin
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource

MESSAGE_MODEL = "messaging.Message"
THREAD_MODEL = "messaging.Thread"


class CommentSource:
    """The record's own chatter: comments, notes and emails on its thread."""

    kind = "comment"

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, ThreadedModelMixin)

    def _messages(self, record: models.Model) -> Any:
        return apps.get_model("messaging", "Message").objects.record_scope(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int,
    ) -> Iterable[TimelineEntry]:
        messages = self._messages(record)
        if before is not None:
            messages = messages.filter(**before.before("_order_at"))
        for message in messages.order_by("-_order_at", "-pk")[:limit]:
            sender = message.sender
            yield TimelineEntry(
                id=public_id_of(message), at=message._order_at, kind=self.kind,
                title=message.preview, actor=(sender.display_name or str(sender)) if sender is not None else "",
                detail=message.subtype.name if message.subtype_id else message.get_message_type_display(),
                target_model=MESSAGE_MODEL, target_id=public_id_of(message),
            )

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._messages(record).count())


class ConversationSource:
    """Conversations the record came from: the source-role thread attachments."""

    kind = "conversation"

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _attachments(self, record: models.Model) -> Any:
        return apps.get_model("messaging", "ThreadAttachment").objects.source_threads_for_record(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int,
    ) -> Iterable[TimelineEntry]:
        attachments = self._attachments(record)
        if before is not None:
            attachments = attachments.filter(**before.before("created_at"))
        for attachment in attachments.order_by("-created_at", "-pk").select_related("thread__title")[:limit]:
            thread = attachment.thread
            yield TimelineEntry(
                id=public_id_of(thread), at=attachment.created_at, kind=self.kind,
                title=thread.title.text if thread.title_id else (attachment.label or "Conversation"),
                detail=f"{thread.message_count} messages",
                target_model=THREAD_MODEL, target_id=public_id_of(thread),
            )

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._attachments(record).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (CommentSource(), ConversationSource())
