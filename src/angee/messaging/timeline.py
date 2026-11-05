"""Chatter and source conversations select their own saved target identities."""

from __future__ import annotations

from collections.abc import Iterable
from copy import copy
from typing import Any

from django.apps import apps
from django.db import models
from rebac import actor_context

from angee.messaging.models import ThreadedModelMixin
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind


class CommentKind(TimelineKind):
    """Record chatter has the transcript's record policy, not an independent inbox policy."""

    id = "messaging.comment"
    sequence = 40
    at = models.F("_order_at")
    source_models = {
        "messaging.Message": ("thread", "is_trashed", "preview", "sent_at", "created_at", "created_by",
                              "subtype", "message_type"),
        "messaging.ThreadAttachment": ("thread", "role"),
        "messaging.Thread": ("merged_into",),
        "messaging.Reaction": ("message", "reaction", "handle"),
        "messaging.MessageStar": ("message", "user"),
        "messaging.ThreadFollower": (
            "thread", "attachment", "notification_policy", "subtype_keys", "last_read_message",
        ),
        "messaging.ThreadNotification": ("thread", "attachment", "message", "read_at", "notification_status"),
    }

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, ThreadedModelMixin)

    def source(self, record: Any, actor: Any) -> tuple[models.QuerySet, tuple[Any, Any] | None]:
        messages = apps.get_model("messaging", "Message").objects
        readers = type(record).objects.with_actor(actor).with_action(record.thread_read_access)
        if not readers.filter(pk=record.pk).exists():
            return messages.none().annotate(_order_at=messages.chronological_time()), self.fold(record)
        with actor_context(actor):
            return messages.record_scope(record).scoped().prefetch_related(None).select_related(
                "subtype", "created_by",
            ), self.fold(record)

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        rows, _ = self.source(record, actor)
        messages = apps.get_model("messaging", "Message").objects
        with actor_context(actor):
            return rows.annotate(_sender_name=messages.sender_name_expression())

    def targets(self, row: Any) -> Iterable[models.Model]:
        if row._meta.label == "messaging.ThreadAttachment" and row.role != "chatter":
            return ()
        return (row,)

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        return {row.pk: Presentation(
            row.preview, row.subtype.name if row.subtype_id else row.get_message_type_display(),
            row.sender_name() if row.sender_id else str(row.created_by) if row.created_by_id else "",
        ) for row in rows}

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        if row._meta.label in {"messaging.Reaction", "messaging.MessageStar"}:
            record = row.message.threaded_record_ref()
        elif row._meta.label in {"messaging.ThreadFollower", "messaging.ThreadNotification"}:
            record = row.thread.chatter_record_ref() if row.thread_id is not None else None
        elif row._meta.label == "messaging.Message":
            record = row.threaded_record_ref()
        elif row._meta.label == "messaging.ThreadAttachment":
            record = row.record_ref
        else:
            record = row.chatter_record_ref()
        if record is not None:
            yield apps.get_model(record.model_label), record.object_id


class ConversationKind(TimelineKind):
    """One source attachment per record, subject to the edge and conversation read gates."""

    id = "messaging.conversation"
    sequence = 70
    source_models = ("messaging.ThreadAttachment", "messaging.Thread")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        with actor_context(actor):
            return apps.get_model("messaging", "ThreadAttachment").objects.source_threads_for_record(
                copy(record).with_actor(actor),
            ).select_related("thread__title")

    def targets(self, row: Any) -> Iterable[models.Model]:
        if row._meta.label == "messaging.ThreadAttachment":
            return (row,)
        return (
            apps.get_model("messaging", "ThreadAttachment")._base_manager
            .db_manager(row._state.db, hints={"instance": row})
            .filter(thread=row, role="source")
        )

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        return {row.pk: Presentation(
            row.thread.title.text if row.thread.title_id else (row.label or "Conversation"),
            f"{row.thread.message_count} messages",
        ) for row in rows}

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        if row.role == "source" and (model := row.content_type.model_class()) is not None:
            yield model, row.object_id


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (CommentKind(), ConversationKind())
