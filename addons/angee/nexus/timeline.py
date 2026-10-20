"""Cross-channel messages on their concerned parties and confirmed circles."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.timeline.kinds import EventData, RecordIdentity, TimelineKind, target_scope

CORRESPONDENCE_KIND = "nexus.correspondence"


class CorrespondenceKind(TimelineKind):
    """One message pointer per concerned record, with messaging's read policy."""

    id = CORRESPONDENCE_KIND
    sequence = 60
    source_models = {
        "messaging.Message": ("sender", "thread", "platform", "direction", "sent_at", "created_at",
                              "preview", "created_by", "is_trashed", "merged_into"),
        "messaging.Part": ("message", "role", "fragment"),
        "messaging.Participant": ("message", "handle"),
        "messaging.Thread": ("merged_into",),
        "messaging.ThreadAttachment": ("thread", "role"),
        "parties.Handle": ("party", "display_name"),
        "parties.Party": ("merged_into", "is_trashed"),
        "parties.Circle": ("parent", "path"),
        "parties.CircleMember": ("party", "circle", "is_confirmed", "is_dismissed"),
    }

    def readable(self, actor: Any) -> models.Q:
        return target_scope(apps.get_model("messaging", "Message").objects.with_actor(actor).inbox())

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, (apps.get_model("parties", "Party"), apps.get_model("parties", "Circle")))

    def rows(self) -> Iterable[models.Model]:
        return apps.get_model("messaging", "Message").objects.inbox().canonical().order_by("pk").iterator()

    def targets(self, row: Any) -> Iterable[models.Model]:
        """Resolve identity, envelope and confirmed-membership changes to messages."""

        message = apps.get_model("messaging", "Message")
        party = apps.get_model("parties", "Party")
        label = row._meta.concrete_model._meta.label
        if label == "messaging.Message":
            return (row,)
        if label in ("messaging.Participant", "messaging.Part"):
            return message.objects.filter(pk=row.message_id)
        if label == "parties.Handle":
            return message.objects.filter(models.Q(participants__handle=row) | models.Q(sender=row)).distinct()
        if label in ("messaging.Thread", "messaging.ThreadAttachment"):
            return message.objects.for_thread(row if label == "messaging.Thread" else row.thread)
        if label == "parties.Circle":
            parties = party.objects.in_circle(row, confirmed_only=True)
        elif label == "parties.CircleMember":
            parties = party.objects.filter(pk=row.party_id)
        else:
            # Merge moves handles before retiring the old party. Its redirect
            # identifies the survivor even when the handle moves used SQL UPDATE.
            parties = party.objects.filter(pk__in=(row.pk, row.canonical().pk))
        return message.objects.involving_parties(parties)

    def events(self, message: Any) -> Iterable[EventData]:
        if not type(message).objects.inbox().canonical().filter(pk=message.pk).exists():
            return []
        parties = apps.get_model("parties", "Party").objects.canonical().filter(
            handles__participations__message=message,
        ).distinct()
        actor, actor_label = message.authorship()
        title = message.title()
        detail = " · ".join(filter(None, (message.platform, message.direction, message.preview)))
        return [EventData(
            kind=self.id, target=message, record=record, occurrence="message", at=message.sent_at or message.created_at,
            title=title, detail=detail,
            actor=actor, actor_label=actor_label,
        ) for record in self._records(parties)]

    @staticmethod
    def _records(parties: Iterable[models.Model]) -> list[RecordIdentity]:
        parties = list(parties)
        circle_model = apps.get_model("parties", "Circle")
        circles = list(circle_model.objects.containing_parties(parties, confirmed_only=True))
        return list(dict.fromkeys((type(row), row.pk) for row in (
            *parties, *circles, *circle_model.objects.ancestors_of(circles),
        )))


def timeline_kinds() -> tuple[TimelineKind, ...]:
    """Nexus's contribution to the shared record chronology."""

    return (CorrespondenceKind(),)
