"""Collection behavior for derived party edges and user cadences."""

from __future__ import annotations

import datetime
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


@dataclass(frozen=True)
class PartyGraph:
    """One bounded graph projection in deterministic discovery order."""

    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    truncated: bool


@dataclass(frozen=True)
class NexusOverviewRows:
    """Viewer-relative fading and due-today relationship intelligence."""

    fading_ties: tuple[Any, ...]
    fading_count: int
    due_cadences: tuple[Any, ...]
    due_count: int


class TieQuerySet(AngeeQuerySet[Any]):
    """Party-pair scopes over the derived edge collection."""

    def around_party(self, party: Any) -> Self:
        """Return edges touching ``party``, retaining the queryset's REBAC scope."""

        return self.filter(models.Q(party_a=party) | models.Q(party_b=party))

    def party_graph(
        self,
        *,
        root: Any | None = None,
        circle: Any | None = None,
        lenses: Iterable[str] | None = None,
        depth: int = 1,
        limit: int = 60,
    ) -> PartyGraph:
        """Return a bounded actor-visible party graph from one party or circle.

        The projection composes three declared lenses: Ego (derived Nexus ties
        plus typed Parties relationships), Circle (membership), and Identity
        (suggested handle links, including unresolved candidates).
        Every contributing queryset applies its own ambient REBAC scope before a
        row can become a node or edge. ``limit`` bounds all nodes, the proportional
        edge ceiling bounds dense neighbourhoods, and ``depth`` is capped so one
        authored read can never become an unbounded crawl. No sudo path exists.
        """

        if (root is None) == (circle is None):
            raise ValueError("party_graph requires exactly one root_id or circle_id")
        requested_lenses = {
            str(lens).strip().lower()
            for lens in (lenses or ("ego",))
        }
        unknown = requested_lenses - {"ego", "circle", "identity"}
        if unknown:
            raise ValueError(f"unknown party graph lenses: {', '.join(sorted(unknown))}")
        selected_lenses: set[str] = set()
        if "ego" in requested_lenses:
            selected_lenses.update(("ties", "relationships"))
        if "circle" in requested_lenses:
            selected_lenses.add("circles")
        if "identity" in requested_lenses:
            selected_lenses.add("identity")

        bounded_depth = max(0, min(int(depth), 3))
        node_limit = max(1, min(int(limit), 120))
        edge_limit = node_limit * 4
        party_model = apps.get_model("parties", "Party")
        relationship_model = apps.get_model("parties", "Relationship")
        relationship_kind_model = apps.get_model("parties", "RelationshipKind")
        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        handle_model = apps.get_model("parties", "Handle")
        party_handle_model = apps.get_model("parties", "PartyHandle")
        cadence_model = apps.get_model("nexus", "Cadence")
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        parties: dict[Any, Any] = {}
        seen_edge_ids: set[str] = set()
        seed_memberships: list[Any] = []
        truncated = False

        def public_id(row: Any) -> str:
            return str(row.sqid)

        def add_party(party: Any) -> bool:
            nonlocal truncated
            node_id = public_id(party)
            if node_id in nodes:
                parties[party.pk] = party
                return True
            if len(nodes) >= node_limit:
                truncated = True
                return False
            parties[party.pk] = party
            concrete_kind = party.concrete_kind
            nodes[node_id] = {
                "id": node_id,
                "kind": "party",
                "title": str(party.display_name),
                "detail": f"{party.handle_count} handles",
                "meta": {
                    "model": (
                        "parties.Organization"
                        if concrete_kind == "organization"
                        else "parties.Person"
                        if concrete_kind == "person"
                        else "parties.Party"
                    ),
                    "record_id": node_id,
                    "handle_count": int(party.handle_count),
                },
            }
            return True

        if root is not None:
            add_party(root)
            nodes[public_id(root)]["kind"] = "root"
            frontier = {root.pk}
        else:
            assert circle is not None
            circle_id = public_id(circle)
            nodes[circle_id] = {
                "id": circle_id,
                "kind": "circle",
                "title": str(circle.name),
                "detail": str(circle.description or ""),
                "meta": {
                    "model": "parties.Circle",
                    "record_id": circle_id,
                    "color": str(circle.color or ""),
                    "icon": str(circle.icon or ""),
                },
            }
            subtree = circle_model.objects.subtree_of(circle).apply_ambient_scope()
            seed_memberships = list(
                circle_member_model.objects.filter(circle__in=subtree)
                .apply_ambient_scope()
                .order_by("sqid")[: edge_limit + 1]
            )
            if len(seed_memberships) > edge_limit:
                seed_memberships = seed_memberships[:edge_limit]
                truncated = True
            seed_party_ids = {membership.party_id for membership in seed_memberships}
            for party in (
                party_model.objects.canonical()
                .filter(pk__in=seed_party_ids)
                .apply_ambient_scope()
                .order_by("display_name", "sqid")
            ):
                if not add_party(party):
                    break
            frontier = set(parties)

        scoped_ties = self.apply_ambient_scope()
        for _level in range(bounded_depth):
            if not frontier or len(edges) >= edge_limit:
                break
            remaining_edges = edge_limit - len(edges)
            tie_candidates: list[Any] = []
            relationship_candidates: list[Any] = []
            if "ties" in selected_lenses:
                tie_candidates = list(
                    scoped_ties.filter(
                        models.Q(party_a_id__in=frontier)
                        | models.Q(party_b_id__in=frontier)
                    )
                    .order_by("-gravity", "sqid")[: remaining_edges + 1]
                )
            if "relationships" in selected_lenses:
                relationship_candidates = list(
                    relationship_model.objects.filter(
                        models.Q(party_id__in=frontier)
                        | models.Q(other_party_id__in=frontier)
                    )
                    .apply_ambient_scope()
                    .order_by("sqid")[: remaining_edges + 1]
                )
            candidate_party_ids = list(
                dict.fromkeys(
                    [
                        party_id
                        for tie in tie_candidates
                        for party_id in (tie.party_a_id, tie.party_b_id)
                    ]
                    + [
                        party_id
                        for relationship in relationship_candidates
                        for party_id in (relationship.party_id, relationship.other_party_id)
                        if party_id is not None
                    ]
                )
            )
            visible_candidates = {
                party.pk: party
                for party in party_model.objects.canonical()
                .filter(pk__in=set(candidate_party_ids).difference(parties))
                .apply_ambient_scope()
            }
            next_frontier: set[Any] = set()
            for party_id in candidate_party_ids:
                party = visible_candidates.get(party_id)
                if party is None:
                    continue
                if not add_party(party):
                    break
                next_frontier.add(party.pk)

            for tie in tie_candidates:
                if len(edges) >= edge_limit:
                    truncated = True
                    break
                if tie.party_a_id not in parties or tie.party_b_id not in parties:
                    continue
                edge_id = public_id(tie)
                if edge_id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge_id)
                edges.append({
                    "id": edge_id,
                    "source": public_id(parties[tie.party_a_id]),
                    "target": public_id(parties[tie.party_b_id]),
                    "kind": "tie",
                    "label": round(float(tie.gravity), 1),
                    "meta": {
                        "model": "nexus.Tie",
                        "record_id": edge_id,
                        "gravity": float(tie.gravity),
                        "is_fading": bool(tie.is_fading),
                        "message_count": int(tie.message_count),
                        "thread_count": int(tie.thread_count),
                        "a_to_b_count": int(tie.a_to_b_count),
                        "b_to_a_count": int(tie.b_to_a_count),
                        "platforms": list(tie.platforms or []),
                        "last_interaction_at": (
                            tie.last_interaction_at.isoformat()
                            if tie.last_interaction_at is not None
                            else None
                        ),
                    },
                })

            kind_ids = {
                relationship.kind_id
                for relationship in relationship_candidates
                if relationship.kind_id is not None
            }
            relationship_kinds = {
                kind.pk: kind
                for kind in relationship_kind_model.objects.filter(pk__in=kind_ids)
                .apply_ambient_scope()
            }
            for relationship in relationship_candidates:
                if len(edges) >= edge_limit:
                    truncated = True
                    break
                if relationship.party_id not in parties:
                    continue
                source_id = public_id(parties[relationship.party_id])
                if relationship.other_party_id in parties:
                    target_id = public_id(parties[relationship.other_party_id])
                elif relationship.other_party_id is None and relationship.other_name:
                    target_id = f"relationship-target:{public_id(relationship)}"
                    if target_id not in nodes:
                        if len(nodes) >= node_limit:
                            truncated = True
                            continue
                        nodes[target_id] = {
                            "id": target_id,
                            "kind": "relationship_target",
                            "title": str(relationship.other_name),
                            "meta": {"model": "parties.Relationship"},
                        }
                else:
                    continue
                edge_id = public_id(relationship)
                if edge_id in seen_edge_ids:
                    continue
                kind = relationship_kinds.get(relationship.kind_id)
                seen_edge_ids.add(edge_id)
                edges.append({
                    "id": edge_id,
                    "source": source_id,
                    "target": target_id,
                    "kind": "relationship",
                    "label": str(relationship.title or (kind.name if kind else "Relationship")),
                    "meta": {
                        "model": "parties.Relationship",
                        "record_id": edge_id,
                        "kind": str(kind.slug if kind else ""),
                        "kind_name": str(kind.name if kind else ""),
                        "kind_inverse_name": str(kind.inverse_name if kind else ""),
                        "source": str(relationship.source),
                    },
                })
            if len(tie_candidates) > remaining_edges or len(relationship_candidates) > remaining_edges:
                truncated = True
            frontier = next_frontier

        party_ids = set(parties)
        cadences = cadence_model.objects.filter(party_id__in=party_ids).apply_ambient_scope()
        for cadence in cadences:
            party = parties.get(cadence.party_id)
            if party is None:
                continue
            nodes[public_id(party)]["meta"]["cadence"] = {
                "id": public_id(cadence),
                "cadence_days": int(cadence.cadence_days),
                "touch_due_at": (
                    cadence.touch_due_at.isoformat()
                    if cadence.touch_due_at is not None
                    else None
                ),
            }

        if "circles" in selected_lenses or circle is not None:
            memberships = seed_memberships or list(
                circle_member_model.objects.filter(party_id__in=party_ids)
                .apply_ambient_scope()
                .order_by("sqid")[: edge_limit + 1]
            )
            if len(memberships) > edge_limit:
                memberships = memberships[:edge_limit]
                truncated = True
            circle_ids = {membership.circle_id for membership in memberships}
            circles = {
                item.pk: item
                for item in circle_model.objects.filter(pk__in=circle_ids)
                .apply_ambient_scope()
                .order_by("position", "name", "sqid")
            }
            for item in circles.values():
                node_id = public_id(item)
                if node_id in nodes:
                    continue
                if len(nodes) >= node_limit:
                    truncated = True
                    break
                nodes[node_id] = {
                    "id": node_id,
                    "kind": "circle",
                    "title": str(item.name),
                    "detail": str(item.description or ""),
                    "meta": {
                        "model": "parties.Circle",
                        "record_id": node_id,
                        "color": str(item.color or ""),
                        "icon": str(item.icon or ""),
                    },
                }
            for membership in memberships:
                if len(edges) >= edge_limit:
                    truncated = True
                    break
                if membership.party_id not in parties or membership.circle_id not in circles:
                    continue
                source_id = public_id(circles[membership.circle_id])
                target_id = public_id(parties[membership.party_id])
                if source_id not in nodes or target_id not in nodes:
                    continue
                edge_id = public_id(membership)
                if edge_id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge_id)
                edges.append({
                    "id": edge_id,
                    "source": source_id,
                    "target": target_id,
                    "kind": "membership",
                    "label": "member",
                    "meta": {
                        "model": "parties.CircleMember",
                        "record_id": edge_id,
                        "confidence": float(membership.confidence),
                        "source": str(membership.source),
                    },
                })

        if "identity" in selected_lenses:
            party_handles = list(
                party_handle_model.objects.filter(
                    party_id__in=party_ids,
                    is_dismissed=False,
                )
                .apply_ambient_scope()
                .order_by("-is_confirmed", "-confidence", "sqid")[: edge_limit + 1]
            )
            if len(party_handles) > edge_limit:
                party_handles = party_handles[:edge_limit]
                truncated = True
            handle_ids = {link.handle_id for link in party_handles}
            handles = {
                handle.pk: handle
                for handle in handle_model.objects.filter(pk__in=handle_ids)
                .apply_ambient_scope()
                .order_by("platform", "normalized_value", "sqid")
            }
            for link in party_handles:
                handle = handles.get(link.handle_id)
                if link.party_id not in parties or handle is None:
                    continue
                node_id = public_id(handle)
                if node_id not in nodes:
                    if len(nodes) >= node_limit:
                        truncated = True
                        break
                    nodes[node_id] = {
                        "id": node_id,
                        "kind": "identity",
                        "title": str(handle.display_name or handle.value),
                        "code": str(handle.platform),
                        "detail": str(handle.value),
                        "meta": {
                            "model": "parties.Handle",
                            "record_id": node_id,
                            "platform": str(handle.platform),
                            "confirmed": bool(handle.party_link_confirmed),
                            "resolved": handle.party_id == link.party_id,
                        },
                    }
                edge_id = public_id(link)
                if len(edges) >= edge_limit:
                    truncated = True
                    break
                if edge_id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge_id)
                edges.append({
                    "id": edge_id,
                    "source": public_id(parties[link.party_id]),
                    "target": node_id,
                    "kind": "identity",
                    "meta": {
                        "model": "parties.PartyHandle",
                        "record_id": edge_id,
                        "handle_id": node_id,
                        "confidence": float(link.confidence),
                        "source": str(link.source),
                        "is_confirmed": bool(link.is_confirmed),
                        "is_resolved": handle.party_id == link.party_id,
                    },
                })

        return PartyGraph(
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            truncated=truncated,
        )

    def overview_for(
        self,
        viewer: Any,
        *,
        user_id: Any,
        peek_limit: int = 6,
        now: Any | None = None,
    ) -> NexusOverviewRows:
        """Return bounded overview facts relative to the viewer's own party."""

        bounded = max(1, min(int(peek_limit), 20))
        fading = (
            self.around_party(viewer)
            .filter(is_fading=True)
            .apply_ambient_scope()
            .order_by("-gravity", "sqid")
        )
        fading_count = int(fading.count())
        current = now or timezone.now()
        local_day = timezone.localdate(current)
        zone = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.datetime.combine(local_day, datetime.time.min), zone)
        end = start + datetime.timedelta(days=1)
        cadence_model = apps.get_model("nexus", "Cadence")
        due = (
            cadence_model.objects.filter(
                user_id=user_id,
                touch_due_at__gte=start,
                touch_due_at__lt=end,
            )
            .apply_ambient_scope()
            .order_by("touch_due_at", "sqid")
        )
        due_count = int(due.count())
        return NexusOverviewRows(
            fading_ties=tuple(fading[:bounded]),
            fading_count=fading_count,
            due_cadences=tuple(due[:bounded]),
            due_count=due_count,
        )


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
