"""Chatter messages and source conversations on the record timeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.identity import public_id_of
from angee.messaging.models import ThreadedModelMixin
from angee.timeline.sources import Cursor, TimelineEntry, TimelineSource

COMMENT_KIND = "messaging.comment"
CONVERSATION_KIND = "messaging.conversation"
MESSAGE_MODEL = "messaging.Message"
THREAD_MODEL = "messaging.Thread"


class CommentSource:
    """The record's own chatter: comments, notes and emails on its thread."""

    kind = COMMENT_KIND
    sequence = 40
    models = ("messaging.Message",)

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, ThreadedModelMixin)

    def _messages(self, record: models.Model) -> Any:
        return apps.get_model("messaging", "Message").objects.record_scope(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
        if needs_me:
            return []
        messages = self._messages(record)
        if before is not None:
            messages = messages.filter(before.predicate(self.kind, "_order_at"))
        rows: list[TimelineEntry] = []
        for message in messages.order_by("-_order_at", "-pk")[:limit]:
            sender = message.sender
            rows.append(TimelineEntry(
                key=f"{self.kind}:{public_id_of(message)}:1", at=message._order_at, kind=self.kind, pk=message.pk,
                title=message.preview, actor=(sender.display_name or str(sender)) if sender is not None else "",
                detail=message.subtype.name if message.subtype_id else message.get_message_type_display(),
                target_model=MESSAGE_MODEL, target_id=public_id_of(message),
            ))
        return rows

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._messages(record).count())


class ConversationSource:
    """Conversations the record came from: the source-role thread attachments."""

    kind = CONVERSATION_KIND
    sequence = 70
    models = ("messaging.ThreadAttachment", "messaging.Thread")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def _attachments(self, record: models.Model) -> Any:
        return apps.get_model("messaging", "ThreadAttachment").objects.source_threads_for_record(record)

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
        if needs_me:
            return []
        attachments = self._attachments(record)
        if before is not None:
            attachments = attachments.filter(before.predicate(self.kind, "created_at"))
        rows: list[TimelineEntry] = []
        for attachment in attachments.order_by("-created_at", "-pk").select_related("thread__title")[:limit]:
            thread = attachment.thread
            rows.append(TimelineEntry(
                key=f"{self.kind}:{public_id_of(attachment)}:1", at=attachment.created_at, kind=self.kind,
                pk=attachment.pk,
                title=thread.title.text if thread.title_id else (attachment.label or "Conversation"),
                detail=f"{thread.message_count} messages",
                target_model=THREAD_MODEL, target_id=public_id_of(thread),
            ))
        return rows

    def count(self, record: models.Model, actor: Any) -> int:
        return int(self._attachments(record).count())


def timeline_sources() -> tuple[TimelineSource, ...]:
    return (CommentSource(), ConversationSource())
