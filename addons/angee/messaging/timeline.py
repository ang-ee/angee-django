"""Chatter messages and source conversations on the record timeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.identity import public_id_of
from angee.messaging.models import ThreadedModelMixin
from angee.timeline.events import target_ref
from angee.timeline.kinds import EventData, TimelineKind, actor_label

COMMENT_KIND = "messaging.comment"
CONVERSATION_KIND = "messaging.conversation"
THREAD_MODEL = "messaging.Thread"


def _chatter_attachment(thread: Any) -> Any | None:
    """The chatter edge of a thread, through the merged canonical thread when there is one."""

    attachment_model = apps.get_model("messaging", "ThreadAttachment")
    canonical = thread.canonical() if thread.merged_into_id is not None else thread
    return attachment_model._base_manager.select_related("thread").filter(thread=canonical, role="chatter").first()


class CommentKind:
    """The record's own chatter: comments, notes and emails on its thread."""

    id = COMMENT_KIND
    sequence = 40
    models = ("messaging.Message",)

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, ThreadedModelMixin)

    def rows(self) -> Iterable[models.Model]:
        message_model = apps.get_model("messaging", "Message")
        return (
            message_model._base_manager.filter(thread__attachments__role="chatter", is_trashed=False)
            .distinct().order_by("pk").iterator()
        )

    def events(self, message: models.Model) -> Iterable[EventData]:
        if message.is_trashed or message.thread_id is None:
            return []
        attachment = _chatter_attachment(message.thread)
        record = attachment.target if attachment is not None else None
        if record is None:
            return []
        target_model, target_id = target_ref(message)
        sender = message.sender
        created_by = message.created_by
        return [EventData(
            key=f"{COMMENT_KIND}:{target_id}:1", kind=COMMENT_KIND,
            at=message.sent_at or message.created_at, record=record,
            target_model=target_model, target_id=target_id,
            title=message.preview,
            detail=message.subtype.name if message.subtype_id else message.get_message_type_display(),
            actor=str(to_subject_ref(created_by)) if created_by is not None else "",
            actor_label=(sender.display_name or str(sender)) if sender is not None else actor_label(created_by),
        )]


class ConversationKind:
    """Conversations the record came from: the source-role thread attachments."""

    id = CONVERSATION_KIND
    sequence = 70
    models = ("messaging.ThreadAttachment", "messaging.Thread")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def rows(self) -> Iterable[models.Model]:
        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        return attachment_model._base_manager.filter(role="source").select_related("thread").order_by("pk").iterator()

    def events(self, row: models.Model) -> Iterable[EventData]:
        attachment_model = apps.get_model("messaging", "ThreadAttachment")
        if isinstance(row, attachment_model):
            attachments = [row] if row.role == "source" else []
        else:
            edges = attachment_model._base_manager.filter(thread=row, role="source")
            attachments = list(edges.select_related("thread"))
        events: list[EventData] = []
        for attachment in attachments:
            record = attachment.target
            if record is None:
                continue
            thread = attachment.thread
            events.append(EventData(
                key=f"{CONVERSATION_KIND}:{public_id_of(attachment)}:1", kind=CONVERSATION_KIND,
                at=attachment.created_at, record=record,
                target_model=THREAD_MODEL, target_id=public_id_of(thread),
                title=thread.title.text if thread.title_id else (attachment.label or "Conversation"),
                detail=f"{thread.message_count} messages",
            ))
        return events


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (CommentKind(), ConversationKind())
