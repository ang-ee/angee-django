"""Collection behavior for derived party edges and user cadences."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Self

from django.apps import apps
from django.db import models, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from angee.base.models import AngeeManager, AngeeQuerySet


@dataclass
class _PairRollup:
    """The in-memory aggregate for one canonical party pair."""

    a_to_b_count: int = 0
    b_to_a_count: int = 0
    message_ids: set[tuple[Any, Any, Any]] = field(default_factory=set)
    thread_ids: set[Any] = field(default_factory=set)
    platforms: set[str] = field(default_factory=set)
    first_at: Any | None = None
    last_at: Any | None = None

    def add(
        self,
        *,
        message_id: Any,
        thread_id: Any,
        source_party_id: Any,
        target_party_id: Any,
        platform: str,
        interaction_at: Any,
    ) -> None:
        """Add one directed event, ignoring overlap between derivation sources."""

        event = (message_id, source_party_id, target_party_id)
        if event in self.message_ids:
            return
        self.message_ids.add(event)
        if source_party_id < target_party_id:
            self.a_to_b_count += 1
        else:
            self.b_to_a_count += 1
        if thread_id is not None:
            self.thread_ids.add(thread_id)
        self.platforms.add(str(platform))
        if interaction_at is not None:
            self.first_at = interaction_at if self.first_at is None else min(self.first_at, interaction_at)
            self.last_at = interaction_at if self.last_at is None else max(self.last_at, interaction_at)


class TieQuerySet(AngeeQuerySet[Any]):
    """Party-pair scopes over the derived edge collection."""

    def around_party(self, party: Any) -> Self:
        """Return edges touching ``party``, retaining the queryset's REBAC scope."""

        return self.filter(models.Q(party_a=party) | models.Q(party_b=party))


class TieManager(AngeeManager.from_queryset(TieQuerySet)):  # type: ignore[misc]
    """Own the deliberate-interaction derivation and canonical pair lookups."""

    def for_parties(self, first: Any, second: Any) -> Any | None:
        """Return the canonically ordered edge between two parties, when present."""

        party_a_id, party_b_id = sorted((first.pk, second.pk))
        return self.filter(party_a_id=party_a_id, party_b_id=party_b_id).first()

    def recompute(self, party_ids: Iterable[Any] | None = None, *, now: Any | None = None) -> int:
        """Replace derived edges from addressed messages, replies, and mentions."""

        now = now or timezone.now()
        selected_ids = set(party_ids) if party_ids is not None else None
        rollups: dict[tuple[Any, Any], _PairRollup] = {}
        for rows in self._derivation_rows(selected_ids):
            for message_id, thread_id, source_id, target_id, platform, interaction_at in rows:
                pair = tuple(sorted((source_id, target_id)))
                rollups.setdefault(pair, _PairRollup()).add(
                    message_id=message_id,
                    thread_id=thread_id,
                    source_party_id=source_id,
                    target_party_id=target_id,
                    platform=platform,
                    interaction_at=interaction_at,
                )

        live_pairs = set(rollups)
        with transaction.atomic():
            for (party_a_id, party_b_id), rollup in rollups.items():
                platforms = sorted(rollup.platforms)
                message_count = len(rollup.message_ids)
                self.update_or_create(
                    party_a_id=party_a_id,
                    party_b_id=party_b_id,
                    defaults={
                        "a_to_b_count": rollup.a_to_b_count,
                        "b_to_a_count": rollup.b_to_a_count,
                        "message_count": message_count,
                        "thread_count": len(rollup.thread_ids),
                        "platforms": platforms,
                        "first_interaction_at": rollup.first_at,
                        "last_interaction_at": rollup.last_at,
                        "gravity": self.model.compute_gravity(
                            message_count=message_count,
                            a_to_b_count=rollup.a_to_b_count,
                            b_to_a_count=rollup.b_to_a_count,
                            last_at=rollup.last_at,
                            platform_count=len(platforms),
                            now=now,
                        ),
                        "is_fading": self.model.check_fading(
                            message_count=message_count,
                            first_at=rollup.first_at,
                            last_at=rollup.last_at,
                            now=now,
                        ),
                    },
                )

            candidates = self.all()
            if selected_ids is not None:
                candidates = candidates.filter(
                    models.Q(party_a_id__in=selected_ids) | models.Q(party_b_id__in=selected_ids)
                )
            stale_ids = [
                pk
                for pk, party_a_id, party_b_id in candidates.values_list("pk", "party_a_id", "party_b_id")
                if (party_a_id, party_b_id) not in live_pairs
            ]
            if stale_ids:
                self.filter(pk__in=stale_ids).delete()
            apps.get_model("nexus", "Cadence").objects.refresh_touch_due()
        return len(live_pairs)

    def _derivation_rows(self, party_ids: set[Any] | None) -> tuple[Any, Any, Any]:
        """Return one projected queryset for each deliberate-interaction source."""

        participant_model = apps.get_model("messaging", "Participant")
        message_model = apps.get_model("messaging", "Message")
        edge_model = apps.get_model("messaging", "MessageEdge")
        interaction_at = Coalesce("message__sent_at", "message__created_at")
        addressed = participant_model._base_manager.filter(
            message__isnull=False,
            role__in=(
                participant_model.ParticipantRole.TO,
                participant_model.ParticipantRole.CC,
            ),
            handle__party__isnull=False,
            message__sender__party__isnull=False,
            message__thread__attachments__isnull=True,
        ).exclude(
            message__thread__modality=apps.get_model("messaging", "Thread").Modality.PUBLIC_THREAD,
        ).exclude(
            handle__party_id=models.F("message__sender__party_id"),
        )
        if party_ids is not None:
            addressed = addressed.filter(
                models.Q(message__sender__party_id__in=party_ids) | models.Q(handle__party_id__in=party_ids)
            )
        addressed_rows = addressed.annotate(interaction_at=interaction_at).values_list(
            "message_id",
            "message__thread_id",
            "message__sender__party_id",
            "handle__party_id",
            "message__platform",
            "interaction_at",
        )

        reply_at = Coalesce("sent_at", "created_at")
        replies = message_model._base_manager.filter(
            sender__party__isnull=False,
            parent__sender__party__isnull=False,
            thread__attachments__isnull=True,
        ).exclude(
            thread__modality=apps.get_model("messaging", "Thread").Modality.PUBLIC_THREAD,
        ).exclude(
            sender__party_id=models.F("parent__sender__party_id"),
        )
        if party_ids is not None:
            replies = replies.filter(
                models.Q(sender__party_id__in=party_ids) | models.Q(parent__sender__party_id__in=party_ids)
            )
        reply_rows = replies.annotate(interaction_at=reply_at).values_list(
            "pk",
            "thread_id",
            "sender__party_id",
            "parent__sender__party_id",
            "platform",
            "interaction_at",
        )

        mention_at = Coalesce("src__sent_at", "src__created_at")
        mentions = edge_model._base_manager.filter(
            kind=edge_model.EdgeKind.MENTION,
            src__sender__party__isnull=False,
            dst__sender__party__isnull=False,
            src__thread__attachments__isnull=True,
        ).exclude(
            src__thread__modality=apps.get_model("messaging", "Thread").Modality.PUBLIC_THREAD,
        ).exclude(
            src__sender__party_id=models.F("dst__sender__party_id"),
        )
        if party_ids is not None:
            mentions = mentions.filter(
                models.Q(src__sender__party_id__in=party_ids) | models.Q(dst__sender__party_id__in=party_ids)
            )
        mention_rows = mentions.annotate(interaction_at=mention_at).values_list(
            "src_id",
            "src__thread_id",
            "src__sender__party_id",
            "dst__sender__party_id",
            "src__platform",
            "interaction_at",
        )
        return addressed_rows, reply_rows, mention_rows


class CadenceManager(AngeeManager):
    """Own derived due-date refreshes for the human cadence collection."""

    def refresh_touch_due(self) -> None:
        """Refresh every cadence after the derived edge collection changes."""

        cadences = list(self.all())
        for cadence in cadences:
            cadence.refresh_touch_due()
