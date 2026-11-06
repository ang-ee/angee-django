"""Native party, identity and readable-circle populations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self, TypedDict

from django.apps import apps
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.expressions import BaseExpression
from django.db.models.functions import Coalesce, NullIf

from angee.base.models.hierarchy import HierarchyQuerySet
from angee.base.models.identity import instances_from_public_ids
from angee.base.models.trash import ArchiveQuerySet
from angee.base.querysets import MergeableQuerySet
from angee.iam.permissions import instance_actor
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.provenance.evidence import EvidenceReference
from angee.provenance.statements import (
    SourceKind,
    StatementQuerySet,
    StatementSubjectQuerySet,
)

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations

    from angee.parties.mixins import PartyStatementMixin
    from angee.parties.models import Circle, Handle, Party

_DISPLAY_OWN_AVATAR = "_display_own_avatar"


_DISPLAY_AVATAR_HANDLES = "_display_avatar_handles"


class CircleQuerySet[CircleT: Circle](HierarchyQuerySet[CircleT], AngeeHistoryQuerySet[CircleT]):
    """Circle read scopes: the hierarchy subtree vocabulary over the Angee base."""

    def memberships(self, *, confirmed_only: bool = False) -> Any:
        """Readable memberships in these readable circles, optionally confirmed only."""

        member_model = apps.get_model("parties", "CircleMember")
        members = member_model.objects.db_manager(self.db).all()
        if actor := self.actor():
            members = members.with_actor(actor)
        members = members.scoped_for_aggregate()
        members = members.filter(circle_id__in=Subquery(self.scoped_for_aggregate().order_by().values("pk")))
        if confirmed_only:
            members = members.filter(is_confirmed=True, is_dismissed=False)
        return members

    def containing_parties(self, parties: Any, *, confirmed_only: bool = False) -> Self:
        """Readable circles containing these parties through readable memberships."""

        members = self.memberships(confirmed_only=confirmed_only).filter(party__in=parties)
        return self.filter(pk__in=Subquery(members.order_by().values("circle_id")))

    def with_member_counts(self) -> Self:
        """Annotate each circle with its distinct-party count across its subtree."""

        circles = apps.get_model("parties", "Circle").objects.db_manager(self.db).all()
        people = apps.get_model("parties", "Person").objects.db_manager(self.db).all()
        if actor := self.actor():
            circles = circles.with_actor(actor)
            people = people.with_actor(actor)
        visible_person_ids = people.scoped_for_aggregate().canonical().values("pk")
        subtree = circles.filter(
            owner_id=OuterRef(OuterRef("owner_id")),
            path__startswith=OuterRef(OuterRef("path")),
        )
        subtree_count = (
            subtree.memberships()
            .filter(party_id__in=Subquery(visible_person_ids))
            .order_by()
            .values("circle__owner_id")
            .annotate(total=Count("party_id", distinct=True))
            .values("total")[:1]
        )
        return self.annotate(
            _member_count=Coalesce(
                Subquery(subtree_count, output_field=IntegerField()),
                Value(0),
            ),
        )


class _HandleSenderName(TypedDict):
    _sender_name: str


class HandleQuerySet[HandleT: Handle](MergeableQuerySet[HandleT], AngeeHistoryQuerySet[HandleT]):
    """Handle read scopes over the Angee base."""

    def confirmed(self, platform: str, value: str) -> Self:
        """Return matching handles with a confirmed, nonempty party link.

        Value comparison follows the platform's persisted normalization rule.
        Existing filters and actor scope are preserved.
        """

        return self.filter(
            platform=platform,
            normalized_value=self.model.normalize_value(platform, value),
            party_link_confirmed=True,
            party__isnull=False,
        )

    def with_sender_name(self) -> HandleQuerySet[WithAnnotations[HandleT, _HandleSenderName]]:
        """Select each readable handle's confirmed, readable party/envelope name.

        Explicit actor scopes keep elevated parents from exposing directory
        identities. Empty strings follow the same fallback as absent values.
        """

        actor = instance_actor(self)
        handles = self.with_actor(actor).scoped() if actor is not None else self.none()
        return handles.annotate(_sender_name=handles.sender_name_expression())

    def sender_name_expression(self, prefix: str = "", *, party_read_implied: bool = False) -> Coalesce:
        """Project the name rule onto an already-readable handle or handle join.

        Callers authorize the handle population. A confirmed party name keeps
        its own read gate unless the admitted parent also implies that display
        fact, as a message does for its sender. Prefix follows Django relation
        paths, including ``__``.
        """

        party_name: BaseExpression | F
        if party_read_implied:
            party_name = F(f"{prefix}party__display_name")
        else:
            actor = instance_actor(self)
            party_model = apps.get_model("parties", "Party")
            party_name = party_model.objects.filter(pk=OuterRef(f"{prefix}party_id")).readable_scalar_subquery(
                "display_name",
                actor=actor,
            )
        return Coalesce(
            NullIf(
                Case(
                    When(**{f"{prefix}party_link_confirmed": True}, then=party_name),
                    default=Value(None),
                    output_field=TextField(),
                ),
                Value(""),
            ),
            NullIf(f"{prefix}display_name", Value("")),
            f"{prefix}value",
            Value(""),
            output_field=TextField(),
        )

    def resolved_confidence_expression(self) -> Any:
        rows = (
            apps.get_model("parties", "Party")
            .objects.handle_statements()
            .for_reader(instance_actor(self))
            .applied()
            .filter(subject_id=OuterRef("party_id"), handle_id=OuterRef("pk"))
        )
        return Subquery(rows.values("confidence")[:1])

    def excluding_identity_of(self, user_id: Any) -> Self:
        """Drop the user's own handles: controlled by them or confirmed on their Person."""

        if user_id is None:
            return self
        handles = self.exclude(controller_id=user_id)
        identity = apps.get_model("parties", "Party").objects.identity_for_user_id(user_id)
        if identity is not None:
            handles = handles.exclude(party=identity, party_link_confirmed=True)
        return handles


class PartyStatementQuerySet[StatementT: PartyStatementMixin](StatementQuerySet[StatementT]):
    """Party handle scopes; serving callers explicitly authorize both endpoints."""

    def handles(self) -> Self:
        return self.current().filter(aspect="handle")

    def members(self) -> Self:
        """One strongest current stance per subject/member, including weak claims."""
        representative = (
            self.model._base_manager.using(self.db)
            .current()
            .filter(aspect="handle")
            .filter(
                subject_id=OuterRef("subject_id"),
                handle_id=OuterRef("handle_id"),
                status=self.model.StatementStatus.ASSERTED,
            )
            .order_by("-applied", *self.model.trust_ordering)
            .values("pk")[:1]
        )
        return self.filter(pk=Subquery(representative))

    def to_review(self) -> Self:
        decision = (
            self.model._base_manager.using(self.db)
            .current()
            .filter(aspect="handle")
            .filter(
                subject_id=OuterRef("subject_id"),
                handle_id=OuterRef("handle_id"),
                status=self.model.StatementStatus.ASSERTED,
                rank=self.model.StatementRank.PREFERRED,
            )
        )
        return (
            self.handles()
            .members()
            .filter(
                status=self.model.StatementStatus.ASSERTED,
                rank=self.model.StatementRank.NORMAL,
                snak=self.model.StatementSnak.VALUE,
                confidence__lt=self.model.statement_subject_model.REVIEW_CONFIDENCE,
            )
            .exclude(Exists(decision))
        )

    def asserted(self) -> Self:
        """Publish eligible applied directory/person assertions or confirmations."""
        return (
            self.applied()
            .filter(aspect="handle")
            .filter(
                Q(rank=self.model.StatementRank.PREFERRED)
                | Q(source_kind__in=(SourceKind.DIRECTORY, SourceKind.PERSON)),
            )
        )


@dataclass(frozen=True, slots=True)
class SameAsPair:
    """One deterministic pair with its normalized identity and readable evidence rows."""

    left: Any
    right: Any
    normalized_value: str
    handles: tuple[Any, ...]


class PartyQuerySet[PartyT: Party](MergeableQuerySet[PartyT], StatementSubjectQuerySet[PartyT]):
    """Party read scopes: canonical (unmerged) rows and organisation membership."""

    def with_concrete_kind(self) -> Self:
        """Project the two declared child-row kinds with the party population."""

        return self.annotate(_concrete_kind=self.model.concrete_kind_expression())

    def with_display_avatars(self, actor: Any) -> Self:
        """Batch :meth:`PartyManager.display_avatar` for these admitted parties.

        For a read owner that hands parties to a page outside the GraphQL
        optimizer (a navigator row, a same_as pair), so no party reads its
        avatar on its own.
        """

        avatars = apps.get_model("storage", "File").objects.with_actor(actor).select_related(
            "mime_type"
        )
        return self.prefetch_related(*self.model._default_manager.display_avatar_prefetches(actor, avatars=avatars))

    def named(self, name: str) -> Self:
        """Return canonical candidates with an exact display or legal name.

        Comparison ignores case and collapses surrounding and internal whitespace
        on both sides. Several parties may match. Compositions with ArchiveQuerySet
        also exclude archived rows; existing filters and actor scope are preserved.
        """

        words = name.split()
        if not words:
            return self.none()
        pattern = r"^\s*" + r"\s+".join(re.escape(word) for word in words) + r"\s*$"
        candidates = self.canonical()
        if isinstance(candidates, ArchiveQuerySet):
            candidates = candidates.unarchived()
        organizations = apps.get_model("parties", "Organization").objects.filter(legal_name__iregex=pattern)
        actor = instance_actor(self)
        if actor is not None:
            organizations = organizations.with_actor(actor)
        legal_name_ids = organizations.scoped_for_aggregate().values("pk")
        return candidates.filter(
            Q(display_name__iregex=pattern) | Q(pk__in=Subquery(legal_name_ids)),
        )

    def with_circle_names(self) -> Self:
        """Prefetch actor-visible circle names for generic chip rendering.

        A scoped prefetch is portable across the supported database floor and,
        unlike a reverse-join aggregate, independently applies both the membership
        and circle row policies before projecting names.
        """

        circles = apps.get_model("parties", "Circle").objects.db_manager(self.db).all()
        if actor := self.actor():
            circles = circles.with_actor(actor)
        visible_memberships = circles.memberships().select_related("circle").order_by("circle__name", "sqid")
        return self.prefetch_related(
            Prefetch(
                "circle_members",
                queryset=visible_memberships,
                to_attr="_angee_visible_circle_members",
            )
        )

    def unassigned(self) -> Self:
        """Return canonical parties with no actor-visible circle membership."""

        circles = apps.get_model("parties", "Circle").objects.db_manager(self.db).all()
        if actor := self.actor():
            circles = circles.with_actor(actor)
        visible_membership = circles.memberships().filter(party_id=OuterRef("pk"))
        return (
            self.canonical()
            .annotate(
                _has_visible_circle=Exists(visible_membership),
            )
            .filter(_has_visible_circle=False)
        )

    def to_review(self) -> Self:
        """Return canonical parties with an undecided low-confidence handle statement."""

        visible_review_link = (
            apps.get_model("parties", "Party")
            .objects.handle_statements()
            .for_reader(instance_actor(self))
            .to_review()
            .filter(subject_id=OuterRef("pk"))
        )
        return (
            self.canonical()
            .annotate(
                _has_visible_review_link=Exists(visible_review_link),
            )
            .filter(_has_visible_review_link=True)
        )

    def in_circle(self, circle: Any, *, confirmed_only: bool = False) -> Self:
        """Return canonical parties in ``circle`` or any of its descendants."""

        circle_model = apps.get_model("parties", "Circle")
        circles = circle_model.objects.all()
        if actor := self.actor():
            circles = circles.with_actor(actor)
        visible_subtree_membership = (
            circles.subtree_of(circle).memberships(confirmed_only=confirmed_only).filter(party_id=OuterRef("pk"))
        )
        return (
            self.canonical()
            .annotate(
                _in_visible_circle_subtree=Exists(visible_subtree_membership),
            )
            .filter(_in_visible_circle_subtree=True)
        )

    def organization_memberships(self) -> Any:
        """Readable current organisation relationships anchored at these parties."""

        actor = instance_actor(self)
        organizations = apps.get_model("parties", "Organization").objects.all()
        relationships = apps.get_model("parties", "Relationship").objects.canonical()
        if actor is not None:
            organizations = organizations.with_actor(actor)
            relationships = relationships.with_actor(actor)
        organizations = organizations.scoped_for_aggregate()
        relationships = relationships.scoped_for_aggregate()
        return relationships.filter(
            party_id__in=Subquery(self.scoped_for_aggregate().order_by().values("pk")),
            other_party_id__in=Subquery(organizations.order_by().values("pk")),
            ended_at__isnull=True,
        )

    def person_id_for_user(self, user_id: Any) -> Any | None:
        """Return this queryset's Person id linked to ``user_id``, without creating it."""

        person_model = apps.get_model("parties", "Person")
        people = person_model._base_manager.using(self.db).filter(user_id=user_id).values("pk")
        return self.filter(pk__in=Subquery(people)).values_list("pk", flat=True).first()

    def account_for_party(self, party_id: Any) -> Any | None:
        """Return the user account for a selected Person party, when one exists."""

        if self.filter(pk=party_id).values_list("pk", flat=True).first() is None:
            return None
        person_model = apps.get_model("parties", "Person")
        person = person_model._base_manager.using(self.db).select_related("user").filter(pk=party_id).first()
        return person.user if person is not None and person.user_id is not None else None

    def same_as_pairs(self, *, limit: int = 50) -> list[SameAsPair]:
        """Read resolved value pairs through both endpoints and each evidence row."""

        if limit <= 0:
            return []
        actor, unscoped = self.effective_actor(strict=True)
        visible = self.canonical().scoped_for_aggregate().values("pk")
        statements = self.model.same_as.model.statements.applied()
        statements = statements.sudo(reason="parties.same_as_pairs") if unscoped else statements.with_actor(actor)
        endpoints = self.canonical()
        if not unscoped:
            endpoints = endpoints.with_display_avatars(actor)
        pairs = (
            self.model.same_as.model._base_manager.filter(
                left_id__in=Subquery(visible),
                right_id__in=Subquery(visible),
            )
            .untrashed()
            .filter(Exists(statements.filter(subject_id=OuterRef("pk"))))
            .prefetch_related(
                Prefetch("partysameaspairstatement_set", queryset=statements, to_attr="_resolved_statements"),
                *(Prefetch(side, queryset=endpoints) for side in ("left", "right")),
            )
            .order_by("left_id", "right_id")
        )[: max(0, min(int(limit), 101))]
        refs_by_pair = {
            pair: [
                ref
                for value in pair._resolved_statements[0].evidence
                if (ref := EvidenceReference.model_validate(value)).model.lower() == "parties.handle"
            ]
            for pair in pairs
        }
        handle_model = apps.get_model("parties", "Handle")
        readable = instances_from_public_ids(
            handle_model,
            (ref.id for refs in refs_by_pair.values() for ref in refs),
            queryset=(
                handle_model.objects.sudo(reason="parties.same_as_pairs")
                if unscoped
                else handle_model.objects.with_actor(actor)
            ),
        )
        result = []
        for pair, refs in refs_by_pair.items():
            handles = [readable[ref.id] for ref in refs if ref.id in readable]
            if len(handles) != len(refs):
                continue
            result.append(
                SameAsPair(pair.left, pair.right, handles[0].normalized_value if handles else "", tuple(handles))
            )
        return result
