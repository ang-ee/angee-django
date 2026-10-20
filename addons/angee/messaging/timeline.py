"""Chatter and source conversations project their own saved target identities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac.managers import RebacQuerySet

from angee.messaging.models import ThreadedModelMixin
from angee.refs import generic_pointer_model
from angee.timeline.kinds import EventData, TimelineKind, target_scope


class CommentKind(TimelineKind):
    """Record chatter has the transcript's record policy, not an independent inbox policy."""

    id = "messaging.comment"
    sequence = 40
    source_models = {
        "messaging.Message": ("thread", "is_trashed", "preview", "sent_at", "created_at", "created_by",
                              "subtype", "message_type"),
        "messaging.ThreadAttachment": ("thread", "role"),
        "messaging.Thread": ("merged_into",),
    }

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, ThreadedModelMixin)

    def readable(self, actor: Any) -> models.Q:
        scope = models.Q(pk__in=())
        for model in apps.get_models():
            if not self.eligible(model) or generic_pointer_model(model) is not model:
                continue
            rows = model._default_manager.all()
            if isinstance(rows, RebacQuerySet):
                rows = rows.with_actor(actor).with_action(model.thread_read_access).scoped()
            scope |= models.Q(
                record_content_type__app_label=model._meta.app_label,
                record_content_type__model=model._meta.model_name,
            ) & models.Q(models.Exists(rows.filter(pk=models.OuterRef("record_object_id"))))
        return scope

    def rows(self) -> Iterable[models.Model]:
        return (
            apps.get_model("messaging", "Message")._base_manager
            .filter(thread__attachments__role="chatter").distinct().order_by("pk").iterator()
        )

    def targets(self, row: Any) -> Iterable[models.Model]:
        if row._meta.label == "messaging.Message":
            return (row,)
        if row._meta.label == "messaging.ThreadAttachment" and row.role != "chatter":
            return ()
        thread = row.thread if row._meta.label == "messaging.ThreadAttachment" else row
        return apps.get_model("messaging", "Message").objects.for_thread(thread)

    def events(self, message: Any) -> Iterable[EventData]:
        if message.is_trashed or message.thread_id is None:
            return []
        # Messaging owns both the canonical thread and its chatter record.
        record = message.threaded_record_ref()
        if record is None:
            return []
        actor, actor_label = message.authorship()
        return [EventData(
            kind=self.id, target=message, record=(apps.get_model(record.model_label), record.object_id),
            at=message.sent_at or message.created_at,
            title=message.preview,
            detail=message.subtype.name if message.subtype_id else message.get_message_type_display(),
            actor=actor, actor_label=actor_label,
        )]


class ConversationKind(TimelineKind):
    """One source attachment per record, subject to the edge and conversation read gates."""

    id = "messaging.conversation"
    sequence = 70
    source_models = ("messaging.ThreadAttachment", "messaging.Thread")

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def readable(self, actor: Any) -> models.Q:
        threads = apps.get_model("messaging", "Thread").objects.with_actor(actor).scoped().values("pk")
        attachments = apps.get_model("messaging", "ThreadAttachment").objects.with_actor(actor)
        return target_scope(attachments.filter(thread_id__in=threads))

    def rows(self) -> Iterable[models.Model]:
        model = apps.get_model("messaging", "ThreadAttachment")
        return model._base_manager.filter(role="source").order_by("pk").iterator()

    def targets(self, row: Any) -> Iterable[models.Model]:
        if row._meta.label == "messaging.ThreadAttachment":
            return (row,)
        return apps.get_model("messaging", "ThreadAttachment")._base_manager.filter(thread=row, role="source")

    def events(self, attachment: Any) -> Iterable[EventData]:
        if attachment.role != "source":
            return []
        model = attachment.content_type.model_class()
        if model is None:
            return []
        thread = attachment.thread
        return [EventData(
            kind=self.id, target=attachment, record=(model, attachment.object_id), at=attachment.created_at,
            title=thread.title.text if thread.title_id else (attachment.label or "Conversation"),
            detail=f"{thread.message_count} messages",
        )]


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (CommentKind(), ConversationKind())
