"""Cross-channel messages on their concerned parties and confirmed circles."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.messaging.managers import MessageQuerySet
from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind

CORRESPONDENCE_KIND = "nexus.correspondence"


class CorrespondenceKind(TimelineKind):
    """One message pointer per concerned record, with messaging's read policy."""

    id = CORRESPONDENCE_KIND
    sequence = 60
    occurrence = "message"
    at = models.F("_order_at")
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

    def eligible(self, model: type[models.Model]) -> bool:
        return issubclass(model, (apps.get_model("parties", "Party"), apps.get_model("parties", "Circle")))

    def occurs(self, record: Any) -> bool:
        """Probe membership without the reader-dependent canonical-message collapse.

        A readable redirect remains visible when its survivor is unreadable, so
        this actor-free probe deliberately includes all untrashed inbox copies.
        """
        return apps.get_model("messaging", "Message")._base_manager.filter(
            self._record_filter(record), MessageQuerySet.inbox_q(), MessageQuerySet.untrashed_q(),
        ).exists()

    @staticmethod
    def _record_filter(record: Any) -> models.Q:
        """The readable message owns its envelope, including private membership edges."""
        party = apps.get_model("parties", "Party")
        circle = apps.get_model("parties", "Circle")
        parties = party._base_manager.all().canonical().filter(party.objects.untrashed_q())
        if isinstance(record, circle):
            subtree = circle._base_manager.filter(circle.objects.subtree_filter(record))
            members = apps.get_model("parties", "CircleMember")._base_manager.filter(
                circle.objects.memberships_filter(subtree, confirmed_only=True),
            )
            parties = parties.filter(pk__in=members.order_by().values("party_id"))
        else:
            parties = parties.filter(pk=record.pk)
        return MessageQuerySet.involving_parties_filter(
            parties,
            handles=apps.get_model("parties", "Handle")._base_manager.all(),
            participants=apps.get_model("messaging", "Participant")._base_manager.all(),
        )

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        rows = apps.get_model("messaging", "Message").objects.filter(self._record_filter(record))
        return rows.with_actor(actor).inbox().canonical().scoped().annotate(
            _order_at=rows.chronological_time(),
        ).select_related("sender", "created_by").with_title_text()

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        return {message.pk: Presentation(message.title(), " · ".join(filter(None, (
            message.platform, message.direction, message.preview,
        ))), message.authorship()[1]) for message in rows}

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        """Find concerned records with SQL membership, without hydrating a message fan-out."""
        message = apps.get_model("messaging", "Message")
        party = apps.get_model("parties", "Party")
        label = row._meta.concrete_model._meta.label
        if label == "messaging.Message":
            messages = message.objects.filter(pk=row.pk)
        elif label in ("messaging.Participant", "messaging.Part"):
            messages = message.objects.filter(pk=row.message_id)
        elif label == "parties.Handle":
            # A handle merge keeps messages on the retired handle; its survivor's group concerns them.
            handles = apps.get_model("parties", "Handle")._base_manager.redirect_groups([row.merged_into_id or row.pk])
            messages = message.objects.filter(models.Q(participants__handle__in=handles) | models.Q(sender__in=handles))
        elif label in ("messaging.Thread", "messaging.ThreadAttachment"):
            messages = message.objects.for_thread(row if label == "messaging.Thread" else row.thread)
        else:
            if label == "parties.Circle":
                parties = party.objects.in_circle(row, confirmed_only=True)
            elif label == "parties.CircleMember":
                parties = party.objects.filter(pk=row.party_id)
            else:
                parties = party.objects.filter(pk__in=(row.pk, row.canonical().pk))
            return self._records(parties)
        involved = messages.order_by().values("pk")
        return self._records(party.objects.canonical().filter(
            models.Q(handles__participations__message__in=involved)
            | models.Q(handles__merged_from__participations__message__in=involved),
        ).distinct())

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
