"""Managers that own the directory-sync write path for parties.

A directory backend parses a source into neutral ``ParsedContact`` rows; these
managers turn one into a ``Party`` (a ``Person``) and its ``Handle`` /
``PartyStatement`` / ``Address`` rows. A contact is keyed by its source UID within
its observed RecordLink identity, handles dedupe on
``(platform, value)``, and ``handle_count`` plus the resolved ``Handle.party`` are
maintained here in the same transaction — so every directory source shares one
write path (the map lives on the models, not in each backend) and a re-sync
converges instead of duplicating. The sync runs under ``system_context``, so
``created_by`` is set explicitly to the directory owner.
"""

from __future__ import annotations

import mimetypes
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from itertools import chain, combinations, groupby, islice
from typing import Any, NamedTuple, Self

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery, TextField, Value, When
from django.db.models.functions import Coalesce, NullIf
from django.utils.crypto import salted_hmac
from phonenumbers import (
    NumberParseException,
    PhoneNumberMatcher,
    is_possible_number,
    is_valid_number,
    parse,
)
from rebac import (
    PermissionDenied,
    actor_context,
    current_actor,
    system_context,
)
from rebac.actors import to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.identity import EvidenceReference, instances_from_public_ids, public_id_for
from angee.base.impl import resolve_hooks
from angee.base.mixins import ArchiveQuerySet, HierarchyQuerySet, SourceKind, TrashQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.querysets import StatementQuerySet
from angee.base.refs import generic_pointer_model
from angee.base.scoping import by_key, read_scoped_queryset, system_queryset
from angee.base.serialization import canonical_json
from angee.parties.backends import ParsedAddress, ParsedContact, ParsedPhoto
from angee.parties.domains import GENERIC_EMAIL_DOMAINS
from angee.storage.models import UploadState

_UNSCOPED = object()


class Signing(NamedTuple):
    """One sender's signature fragment: the evidence signature mining reads.

    A downstream addon contributes these through ``ANGEE_PARTIES_SIGNING_PROVIDERS``;
    parties reads no other addon's models.
    """

    fragment_hash: str
    text: str
    sender_id: Any
    party_id: Any
    """The sender's resolved party, or ``None``."""
    owner_id: Any
    """The audit owner of the mail the fragment came from."""


class HandleAssociationStatus(StrEnum):
    """Nondisclosing assessment of one claimed Handle for a candidate Party."""

    SAME_CONFIRMED = "same_confirmed"
    SAME_DISMISSED = "same_dismissed"
    CONFIRMED_OTHER = "confirmed_other"
    WEAK_SAME = "weak_same"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HandleAssociationAssessment:
    """Closed risk status plus association rows already readable by the actor."""

    status: HandleAssociationStatus
    readable_links: tuple[Any, ...]
    conflict_evidence_readable: bool


@dataclass(frozen=True, slots=True)
class IdentityBasis:
    """One Party identity read: the actor-visible facts and the complete basis.

    ``current`` is safe to show the reading actor. ``complete`` may contain rows
    that actor cannot read; it is never shown and only anchors expected-state
    checks through :attr:`facts_hash`.
    """

    party: Any
    current: dict[str, Any]
    complete: dict[str, Any]

    @property
    def facts_hash(self) -> str:
        """Keyed digest of the complete basis; it cannot confirm guesses of hidden rows."""

        return salted_hmac(
            "angee.parties.identity_basis",
            canonical_json(self.complete),
            algorithm="sha256",
        ).hexdigest()


class CircleQuerySet(HierarchyQuerySet, AngeeQuerySet):
    """Circle read scopes: the hierarchy subtree vocabulary over the Angee base."""

    def memberships(self, *, confirmed_only: bool = False) -> Any:
        """Readable memberships in these readable circles, optionally confirmed only."""

        member_model = apps.get_model("parties", "CircleMember")
        members = member_model.objects.all().with_actor(self.actor() or current_actor()).scoped_for_aggregate()
        members = members.filter(circle_id__in=Subquery(self.scoped_for_aggregate().order_by().values("pk")))
        if confirmed_only:
            members = members.filter(is_confirmed=True, is_dismissed=False)
        return members

    def with_member_counts(self) -> Self:
        """Annotate each circle with its distinct-party count across its subtree."""

        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        person_model = apps.get_model("parties", "Person")
        visible_person_ids = person_model.objects.all().scoped_for_aggregate().canonical().values("pk")
        visible_subtree_circle_ids = (
            circle_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                created_by_id=OuterRef(OuterRef("created_by_id")),
                path__startswith=OuterRef(OuterRef("path")),
            )
            .values("pk")
        )
        subtree_count = (
            circle_member_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                circle_id__in=Subquery(visible_subtree_circle_ids),
                party_id__in=Subquery(visible_person_ids),
            )
            .order_by()
            .values("circle__created_by_id")
            .annotate(total=Count("party_id", distinct=True))
            .values("total")[:1]
        )
        return self.annotate(
            _member_count=Coalesce(
                Subquery(subtree_count, output_field=IntegerField()),
                Value(0),
            ),
        )


class CircleManager(AngeeManager.from_queryset(CircleQuerySet)):  # type: ignore[misc]
    """Manager for circles — subtree scopes ride in through :class:`CircleQuerySet`."""


class HandleQuerySet(AngeeQuerySet):
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

    def with_sender_name(self) -> Self:
        """Select each readable handle's confirmed, readable party/envelope name.

        Explicit actor scopes keep elevated parents from exposing directory
        identities. Empty strings follow the same fallback as absent values.
        """

        actor = self.actor() or current_actor()
        handles = self.with_actor(actor).scoped() if actor is not None else self.none()
        return handles.annotate(_sender_name=handles.sender_name_expression())

    def sender_name_expression(self, prefix: str = "") -> Coalesce:
        """Project the name rule onto an already-readable handle or handle join.

        Callers authorize the handle population; the party fallback keeps its
        own read gate. Prefix follows Django relation paths, including ``__``.
        """

        actor = self.actor() or current_actor()
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
            .objects.handle_statements(
                actor=self.actor() or current_actor(),
            )
            .canonical()
            .filter(subject_id=OuterRef("party_id"), handle_id=OuterRef("pk"))
        )
        return Subquery(rows.values("confidence")[:1])

    def owned_by(self, user: Any) -> Self:
        """Return the handles this user controls — the ``owner`` column, no joins."""

        return self.filter(owner=user)

    def excluding_identity_of(self, user_id: Any) -> Self:
        """Drop the user's own handles: owned by them or confirmed on their Person."""

        if user_id is None:
            return self
        handles = self.exclude(owner_id=user_id)
        identity = apps.get_model("parties", "Party").objects.identity_for_user_id(user_id)
        if identity is not None:
            handles = handles.exclude(party=identity, party_link_confirmed=True)
        return handles


class HandleManager(AngeeManager.from_queryset(HandleQuerySet)):  # type: ignore[misc]
    """Factory + upsert for handles (the contact-point write path)."""

    def for_user(self, user: Any) -> Any:
        """Return the account's self-claimed handle for internal attribution."""

        if user is None or user.pk is None:
            raise ValueError("Handle author is required.")
        email = (user.email or "").replace("\x00", "").strip()
        username = user.get_username().replace("\x00", "").strip()
        value = email or username or str(user.pk)
        display_name = user.get_full_name().replace("\x00", "").strip() or username or value
        return self.claim_own(
            user,
            platform=self.model.Platform.for_value(value),
            value=value,
            display_name=display_name,
            asserted_by=(
                user
                if user.source_kind == SourceKind.PERSON
                else apps.get_model("parties", "Party").objects.rule_source("source_sender")
            ),
            metadata={"user_id": str(user.pk)},
        )

    def renormalize_phone_values(self) -> int:
        """Repair stored phone comparison values after normalization rules change.

        The pass is idempotent and collision-safe because ``normalized_value`` is a
        comparison projection rather than a uniqueness key: equal E.164 results are
        deliberately retained on their distinct source handles for same_as review.
        """

        phone_platforms = (self.model.Platform.PHONE, self.model.Platform.WHATSAPP)
        changed = 0
        for handle in self.filter(platform__in=phone_platforms).only(
            "id",
            "platform",
            "value",
            "normalized_value",
            "updated_at",
        ):
            normalized = self.model.normalize_value(handle.platform, handle.value)
            if handle.normalized_value == normalized:
                continue
            handle.normalized_value = normalized
            handle.save(update_fields=["normalized_value", "updated_at"])
            changed += 1
        return changed

    def upsert(
        self,
        *,
        platform: str,
        value: str,
        created_by_id: Any = None,
        **fields: Any,
    ) -> Any:
        """Get-or-create a handle on the identity it actually has, refreshing display fields.

        A source-stable ``external_id`` (in ``fields``, when the source has one)
        is the stronger identity — the model's conditional unique key — so the
        write serializes on whichever identity is present: ``get_or_create`` on
        ``(platform, external_id)`` when given, else ``(platform, value)``. That
        means an address whose human-readable ``value`` drifts (a chat account
        behind a changed number) refreshes the existing row instead of forking a
        duplicate or crashing a concurrent insert on the external-id constraint.
        When that refreshed ``value`` would instead collide with a different row
        that already owns ``(platform, value)``, the write converges on that owner
        rather than rewriting this row — the same rule the create path applies to
        the mirror collision, so a ``@lid`` and its phone JID settle on one handle.
        The value-keyed path never rewrites ``external_id`` (it is not the key it
        matched on).

        ``created_by_id`` stamps the audit owner. Control ownership is deliberately
        excluded from the generic refresh loop; :meth:`claim_own` is its only write
        path, so a routine upsert cannot silently transfer an account between users.
        ``normalized_value`` tracks ``value`` on every hit. Display fields refresh
        on every hit; blank values never clobber. Source display text is truncated
        to its field limit; identifiers are validated without changing their meaning.
        """

        if "owner" in fields or "owner_id" in fields:
            raise TypeError("Handle control ownership must be written through claim_own().")
        if "normalized_value" in fields:
            raise TypeError("Handle.normalized_value is maintained by Handle.save().")
        normalized_value = self.model.normalize_value(platform, value)
        external_id = str(fields.get("external_id") or "")
        for name, identifier in {
            "platform": platform,
            "value": value,
            "normalized_value": normalized_value,
            "external_id": external_id,
        }.items():
            try:
                self.model._meta.get_field(name).run_validators(identifier)
            except ValidationError as error:
                raise ValidationError({name: error}) from error
        for name in ("display_name", "label"):
            if fields.get(name):
                fields[name] = fields[name][: self.model._meta.get_field(name).max_length]
        if external_id:
            try:
                handle, created = self.get_or_create(
                    platform=platform,
                    external_id=external_id,
                    defaults={
                        "created_by_id": created_by_id,
                        "value": value,
                        "normalized_value": normalized_value,
                        **fields,
                    },
                )
            except IntegrityError:
                # The external-id create can still collide on ``(platform, value)``
                # when a second source identity already holds this contact point —
                # e.g. a WhatsApp contact reached both by phone JID and by a hidden
                # ``@lid`` that resolves to the same E.164. The value *is* the
                # contact point, so converge on the row that owns it instead of
                # forking or crashing; it keeps its own ``external_id`` (the other
                # source's idempotency key). ``get_or_create`` isolates its insert
                # in a savepoint, so the surrounding transaction stays usable.
                existing = self.filter(platform=platform, value=value).first()
                if existing is None:
                    raise
                self._refresh(
                    existing,
                    {name: val for name, val in fields.items() if name != "external_id"},
                )
                return existing
            if not created:
                # The external id resolved an existing row, but refreshing its
                # ``value`` can land on a contact point a *different* row already
                # owns — a WhatsApp ``@lid`` handle whose sender resolved to an
                # ``+E164`` that a phone-JID handle already holds. ``_refresh``
                # saves outside ``get_or_create``'s savepoint, so that collision
                # would raise ``uq_handle_platform_value`` straight into the
                # caller's ``atomic()`` and abort its whole message batch. A
                # pre-check (never a failed save inside the caller's transaction)
                # converges on the row that owns the value, exactly as the create
                # path does above; this row keeps its old value for the offline
                # backfill to merge.
                if value != handle.value:
                    owner_of_value = self.filter(platform=platform, value=value).exclude(pk=handle.pk).first()
                    if owner_of_value is not None:
                        self._refresh(
                            owner_of_value,
                            {name: val for name, val in fields.items() if name != "external_id"},
                        )
                        return owner_of_value
                self._refresh(handle, {"value": value, "normalized_value": normalized_value, **fields})
            return handle
        handle, created = self.get_or_create(
            platform=platform,
            value=value,
            defaults={
                "created_by_id": created_by_id,
                "normalized_value": normalized_value,
                **fields,
            },
        )
        if not created:
            # The value matched, not the external id — never rewrite it here.
            refresh = {name: val for name, val in fields.items() if name != "external_id"}
            self._refresh(handle, {"normalized_value": normalized_value, **refresh})
        return handle

    @staticmethod
    def _refresh(handle: Any, fields: dict[str, Any]) -> None:
        """Apply the non-blank ``fields`` that differ; one save, only when dirty.

        Adapter-owned metadata merges key-wise so refreshing one identifier does
        not erase another adapter's retained identifiers. Assertion provenance
        belongs to statements, independently of this transport metadata.
        """

        merged = fields.get("metadata")
        if isinstance(merged, dict):
            current = handle.metadata if isinstance(handle.metadata, dict) else {}
            fields = {**fields, "metadata": {**current, **merged}}
        dirty = [name for name, new in fields.items() if new and getattr(handle, name, None) != new]
        if dirty:
            for name in dirty:
                setattr(handle, name, fields[name])
            handle.save(update_fields=[*dirty, "updated_at"])

    def claim_own(
        self,
        user: Any,
        *,
        platform: str,
        value: str,
        asserted_by: Any = None,
        evidence: Iterable[Any] = (),
        display_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Record ``value`` as ``user``'s own account handle and normal identity claim.

        The one verb the connect flows (OIDC login, CardDAV/OAuth connect) call for
        the signed-in user's own address. It writes BOTH facts the model separates:
        the **control** fact (:attr:`Handle.owner` — this user sends/syncs as the
        address) and the **identity** fact (a normal-rank :class:`PartyStatement` to the
        user's own :class:`~angee.parties.models.Person`, so the address resolves to
        them). Directory *contacts'* handles get neither. Idempotent.

        A claim is contested when another user already controls the row. The
        existing control owner is never reassigned; instead the competing user's
        identity link is recorded unconfirmed at ``0.3`` confidence and returned
        for later review. The whole decision is atomic and row-locked.
        """

        evidence = tuple(evidence)
        person_model = apps.get_model("parties", "Person")
        link_owner = apps.get_model("parties", "Party").objects
        with system_context(reason="parties.handle.claim_own"), transaction.atomic():
            handle = self.upsert(
                platform=platform,
                value=value,
                created_by_id=user.pk,
                display_name=display_name,
                metadata=metadata or {},
            )
            handles, _existing_parties = link_owner.lock_identity_rows(
                party_ids=(),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            person = person_model.objects.for_user(user)
            handles, parties = link_owner.lock_identity_rows(
                party_ids=(person.pk,),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            person = parties[person.pk]
            if handle.owner_id is not None and handle.owner_id != user.pk:
                link_owner.assert_handle(
                    person,
                    handle,
                    confidence=0.3,
                    asserted_by=asserted_by or user,
                    evidence=evidence,
                )
                return handle
            if handle.owner_id is None:
                handle.owner = user
                handle.save(update_fields=["owner", "updated_at"])
            link_owner.assert_handle(
                person,
                handle,
                confidence=1.0,
                asserted_by=asserted_by or user,
                evidence=evidence,
            )
        return handle

    def resolve(self, handle: Any) -> None:
        """Materialize the applied, eligible cross-party winner and both counters."""
        handle_id = handle.pk if isinstance(handle, models.Model) else handle
        owner = apps.get_model("parties", "Party").objects
        with transaction.atomic(), system_context(reason="parties.handle.resolve"):
            handles, parties = owner.lock_identity_rows(party_ids=(), handle_ids=(handle_id,))
            handle = handles[handle_id]
            candidates = list(
                owner.handle_statements()
                .canonical()
                .filter(
                    handle=handle,
                    subject__is_trashed=False,
                )
                .select_related("subject")
            )
            winner = min(
                candidates,
                key=lambda row: (
                    *row.sort_key()[:2],
                    not row.subject.has_real_name,
                    *row.sort_key()[2:],
                ),
                default=None,
            )
            previous = handle.party_id
            resolved = winner.subject_id if winner else None
            confirmed = bool(winner and winner.is_confirmed)
            if handle.party_id != resolved or handle.party_link_confirmed != confirmed:
                handle.party_id, handle.party_link_confirmed = resolved, confirmed
                handle.save(update_fields=("party", "party_link_confirmed", "updated_at"))
            for pk in sorted({value for value in (previous, resolved) if value is not None}):
                self.recount(parties[pk])
            handle._party_links_resolved()

    def recount(self, party: Any) -> None:
        count = self.filter(party=party).count()
        if party.handle_count != count:
            party.handle_count = count
            party.save_without_historical_record(update_fields=("handle_count",))

    def resolve_stale_owners(self) -> int:
        owner = apps.get_model("parties", "Party").objects
        decisive = (
            owner.handle_statements().canonical().filter(handle_id=OuterRef("pk"), subject_id=OuterRef("party_id"))
        )
        stale = self.filter(party__isnull=False).exclude(Exists(decisive)).order_by("pk")
        repaired = 0
        for handle in stale.iterator():
            self.resolve(handle)
            repaired += 1
        return repaired


class PartyStatementQuerySet(StatementQuerySet):
    """Party handle scopes; serving callers explicitly authorize both endpoints."""

    def handles(self) -> Any:
        return self.current().filter(aspect="handle")

    def _unscoped(self) -> "PartyStatementQuerySet":
        """A fresh queryset of this class: shadow tables carry no read scope of their own."""
        return type(self)(model=self.model, using=self._db)

    def members(self) -> Any:
        """One strongest current stance per subject/member, including weak claims."""
        representative = (
            self._unscoped()
            .handles()
            .filter(
                subject_id=OuterRef("subject_id"),
                handle_id=OuterRef("handle_id"),
                status=self.model.StatementStatus.ASSERTED,
            )
            .order_by("-applied", *self.model.trust_ordering)
            .values("pk")[:1]
        )
        return self.filter(pk=Subquery(representative))

    def to_review(self) -> Any:
        decision = (
            self._unscoped()
            .handles()
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

    def asserted(self) -> Any:
        """Publish eligible applied directory/person assertions or confirmations."""
        return (
            self.canonical()
            .filter(aspect="handle")
            .filter(
                Q(rank=self.model.StatementRank.PREFERRED)
                | Q(source_kind__in=(SourceKind.DIRECTORY, SourceKind.PERSON)),
            )
        )

    def readable(self, actor: Any) -> Any:
        """Serving projection only; shadow tables have no independent read grants."""
        return (
            super()
            .readable(actor)
            .handles()
            .filter(
                handle_id__in=Subquery(read_scoped_queryset(apps.get_model("parties", "Handle"), actor).values("pk")),
            )
        )


@dataclass(frozen=True, slots=True)
class SameAsPair:
    """One deterministic pair with its normalized identity and readable evidence rows."""

    left: Any
    right: Any
    normalized_value: str
    handles: tuple[Any, ...]


class PartyQuerySet(TrashQuerySet, AngeeQuerySet):
    """Party read scopes: canonical (unmerged) rows and organisation membership."""

    def canonical(self) -> Self:
        """Return only the canonical (unmerged) parties.

        The merge owner flattens redirects, so a party
        is canonical exactly when it points nowhere — one indexed filter that stays
        correct even against a longer chain, since a terminal always points nowhere.
        """

        return self.untrashed().filter(merged_into__isnull=True)

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
        actor = self.actor() or current_actor()
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

        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        visible_circle_ids = circle_model.objects.all().scoped_for_aggregate().values("pk")
        visible_memberships = (
            circle_member_model.objects.all()
            .scoped_for_aggregate()
            .filter(circle_id__in=Subquery(visible_circle_ids))
            .select_related("circle")
            .order_by("circle__name", "sqid")
        )
        return self.prefetch_related(
            Prefetch(
                "circle_members",
                queryset=visible_memberships,
                to_attr="_angee_visible_circle_members",
            )
        )

    def unassigned(self) -> Self:
        """Return canonical parties with no actor-visible circle membership."""

        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        visible_circle_ids = circle_model.objects.all().scoped_for_aggregate().values("pk")
        visible_membership = (
            circle_member_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                party_id=OuterRef("pk"),
                circle_id__in=Subquery(visible_circle_ids),
            )
        )
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
            .objects.handle_statements(
                actor=self.actor() or current_actor(),
            )
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
        visible_subtree_membership = (
            circle_model.objects.all()
            .with_actor(self.actor() or current_actor())
            .subtree_of(circle)
            .memberships(confirmed_only=confirmed_only)
            .filter(party_id=OuterRef("pk"))
        )
        return (
            self.canonical()
            .annotate(
                _in_visible_circle_subtree=Exists(visible_subtree_membership),
            )
            .filter(_in_visible_circle_subtree=True)
        )

    def members_of(self, organization: Any) -> Self:
        """Return the parties whose relationships name ``organization`` as counterparty.

        The successor to the removed ``Organization.members`` reverse accessor: a
        member is any party anchoring a current (open-ended)
        :class:`Relationship` whose tracked counterparty is this organisation
        (employment and other org-typed edges).
        """

        return self.filter(
            pk__in=Subquery(self.organization_memberships().filter(other_party=organization).values("party_id"))
        )

    def organization_memberships(self) -> Any:
        """Readable current organisation relationships anchored at these parties."""

        actor = self.actor() or current_actor()
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

    def same_as_pairs(self, *, limit: int = 50) -> list[SameAsPair]:
        """Read resolved value pairs through both endpoints and each evidence row."""

        if limit <= 0:
            return []
        actor, unscoped = self.effective_actor(strict=True)
        visible = self.canonical().scoped_for_aggregate().values("pk")
        statements = self.model.same_as.model.statements.canonical()
        pairs = (
            self.model.same_as.model._base_manager.filter(
                left_id__in=Subquery(visible),
                right_id__in=Subquery(visible),
            )
            .untrashed()
            .filter(Exists(statements.filter(subject_id=OuterRef("pk"))))
            .select_related("left", "right")
            .prefetch_related(
                Prefetch("partysameaspairstatement_set", queryset=statements, to_attr="_resolved_statements")
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
            queryset=system_queryset(handle_model) if unscoped else read_scoped_queryset(handle_model, actor),
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


class PartyManager(AngeeManager.from_queryset(PartyQuerySet)):  # type: ignore[misc]
    """Factory for parties, including the idempotent directory-sync ingest.

    Also the effective manager of the MTI children (``Person`` / ``Organization``
    inherit the parent's concrete default manager), so the Person-per-user factory
    :meth:`for_user` lives here.
    """

    def handle_statements(self, *, actor: Any = _UNSCOPED) -> Any:
        rows = apps.get_model("parties", "Party").statements.handles()
        return rows if actor is _UNSCOPED else rows.readable(actor)

    @staticmethod
    def rule_source(key: str) -> Any:
        with system_context(reason="parties.rule"):
            return apps.get_model("provenance", "Rule").objects.for_key(f"parties.{key}")

    @transaction.atomic
    def assert_handle(
        self,
        party: Any,
        handle: Any,
        *,
        asserted_by: Any,
        confidence: float = 1.0,
        evidence: Iterable[Any] = (),
        link: Any = None,
    ) -> Any:
        """Assert normal handle facts from native supporting records, preserving person decisions."""
        source = to_subject_ref(asserted_by)
        subject = apps.get_model("parties", "Party")._base_manager.get(pk=party.pk)
        origins = list(evidence)
        refs = [
            {
                "model": generic_pointer_model(type(row))._meta.label,
                "id": public_id_for(generic_pointer_model(type(row)), row.pk),
            }
            for row in origins
        ]
        existing = subject.statements.current().filter(aspect="handle", handle=handle, asserted_by=str(source)).first()
        if existing is not None and existing.rank == existing.StatementRank.PREFERRED:
            subject.statements.asserting_source(asserted_by)
            subject.require_statement_write(source)
            return existing
        return subject.statements.set(
            aspect="handle",
            handle=handle,
            values={},
            asserted_by=asserted_by,
            confidence=confidence,
            evidence=refs,
            link=link,
        )

    def lock_identity_rows(
        self,
        *,
        party_ids: Iterable[int],
        handle_ids: Iterable[int],
    ) -> tuple[dict[int, Any], dict[int, Any]]:
        """Lock Handles, competing Parties and statements in bounded key batches."""
        with system_context(reason="parties.identity.lock"):
            handle_model = apps.get_model("parties", "Handle")
            party_model = apps.get_model("parties", "Party")
            handles: dict[int, Any] = {}
            for batch in by_key(handle_model.objects.using(self.db), sorted(set(handle_ids))):
                handles.update((row.pk, row) for row in batch.order_by("pk").lock_if_supported())
            ids = set(party_ids) | {row.party_id for row in handles.values() if row.party_id is not None}
            statements = self.handle_statements().using(self.db)
            for batch in by_key(handle_model._base_manager.using(self.db), sorted(handles)):
                ids.update(statements.filter(handle_id__in=batch.values("pk")).values_list("subject_id", flat=True))
            parties: dict[int, Any] = {}
            for batch in by_key(party_model.objects.using(self.db), sorted(ids)):
                parties.update((row.pk, row) for row in batch.order_by("pk").lock_if_supported())
            for batch in by_key(handle_model._base_manager.using(self.db), sorted(handles)):
                keys = list(statements.filter(handle_id__in=batch.values("pk")).values_list("pk", flat=True))
                for rows in by_key(statements, sorted(keys)):
                    list(rows.order_by("pk").lock_if_supported())
        return handles, parties

    def propose_manual_contact(
        self,
        party: Any,
        *,
        platform: str,
        value: str,
        label: str = "",
        actor: Any,
    ) -> Any:
        """Add one unconfirmed manual contact claim without asserting sender identity.

        Handles are globally deduplicated on their native ``(platform, value)``
        identity, while the Party association remains a reviewable ``PartyStatement``.
        Reusing a global Handle therefore requires read access and never refreshes
        its display fields. Repeating a dismissed Party association returns that
        durable anti-link unchanged; confirmation remains the explicit human action.
        """

        actor = to_subject_ref(party.require_access("write", actor))
        handle_model = apps.get_model("parties", "Handle")
        allowed_platforms = {
            str(handle_model.Platform.EMAIL),
            str(handle_model.Platform.PHONE),
        }
        normalized_platform = str(platform or "").strip().lower()
        normalized_label = " ".join(str(label or "").split()).strip()
        contact_value = str(value or "").strip()
        if normalized_platform not in allowed_platforms:
            raise ValidationError({"platform": "Choose email or phone."})
        if not contact_value:
            raise ValidationError({"value": "Enter an email address or phone number."})
        if normalized_platform == str(handle_model.Platform.EMAIL):
            try:
                validate_email(contact_value)
            except ValidationError as exc:
                raise ValidationError({"value": exc.messages}) from exc
        else:
            try:
                number = parse(contact_value, None)
            except NumberParseException as exc:
                raise ValidationError(
                    {"value": "Enter a valid international phone number including country code."}
                ) from exc
            if not is_possible_number(number) or not is_valid_number(number):
                raise ValidationError({"value": "Enter a valid international phone number including country code."})

        candidate = handle_model(
            platform=normalized_platform,
            value=contact_value,
            normalized_value=handle_model.normalize_value(normalized_platform, contact_value),
            label=normalized_label,
            created_by_id=actor_user_id(actor),
        )
        candidate.full_clean(validate_unique=False, validate_constraints=False)

        with transaction.atomic(), actor_context(actor):
            handle_owner = handle_model.objects
            handle = (
                handle_owner.sudo(reason="parties.statement.propose_manual_contact.handle")
                .lock_if_supported()
                .filter(platform=normalized_platform, value=contact_value)
                .first()
            )
            if handle is None:
                handle_owner.check_create()
                with system_context(reason="parties.statement.propose_manual_contact.handle"):
                    handle = handle_owner.upsert(
                        platform=normalized_platform,
                        value=contact_value,
                        created_by_id=actor_user_id(actor),
                    )
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            locked_party = parties[party.pk]
            locked_party.require_access("write", actor)
            if not handle.with_actor(actor).has_access("read"):
                raise PermissionDenied("Denied: cannot add this contact point.")
            if normalized_label and not handle.label and handle.has_access("write"):
                handle.label = normalized_label
                handle.save(update_fields=("label", "updated_at"))

            existing = self.handle_statements().filter(subject=locked_party, handle=handle).members().first()
            if existing is not None:
                return existing.with_actor(actor)
            return self.assert_handle(locked_party, handle, asserted_by=actor, confidence=0.4).with_actor(actor)

    def has_confirmed_association(self, handle: Any, *, actor: Any) -> bool:
        """Return whether this readable Handle has any confirmed owner, without disclosing it."""

        if actor is None:
            raise PermissionDenied("an actor is required to assess a party-handle association")
        handle.require_access("read", actor)
        with system_context(reason="parties.statement.has_confirmed_association"):
            return (
                self.handle_statements()
                .members()
                .filter(
                    handle_id=handle.pk,
                    rank=self.handle_statements().model.StatementRank.PREFERRED,
                    snak=self.handle_statements().model.StatementSnak.VALUE,
                    status=self.handle_statements().model.StatementStatus.ASSERTED,
                )
                .exists()
            )

    def assess_claimed_handle(self, party: Any, handle: Any, *, actor: Any) -> HandleAssociationAssessment:
        """Assess a claimed Handle without disclosing inaccessible Party associations."""

        if actor is None:
            raise PermissionDenied("an actor is required to assess a party-handle association")
        party.require_access("read", actor)
        handle.require_access("read", actor)
        visible = self.handle_statements().readable(actor).members()
        readable = tuple(visible.filter(handle_id=handle.pk).select_related("subject").order_by("pk"))
        return self._assess_claimed_handle_authorized(party=party, handle=handle, readable_links=readable)

    def _assess_claimed_handle_authorized(
        self,
        *,
        party: Any,
        handle: Any,
        readable_links: tuple[Any, ...],
    ) -> HandleAssociationAssessment:
        """Assess one exact Handle after its caller authorized Party and evidence reads."""

        if any(link.handle_id != handle.pk for link in readable_links):
            raise ValidationError({"handle": "Retained association evidence has the wrong Handle."})
        with system_context(reason="parties.statement.assess_claimed_handle"):
            authoritative = tuple(
                self.handle_statements()
                .filter(handle_id=handle.pk, status=self.handle_statements().model.StatementStatus.ASSERTED)
                .members()
                .order_by("pk")
            )
        same = tuple(link for link in authoritative if link.subject_id == party.pk)
        if any(link.is_dismissed for link in same):
            status = HandleAssociationStatus.SAME_DISMISSED
        elif any(link.is_confirmed and not link.is_dismissed and link.subject_id != party.pk for link in authoritative):
            status = HandleAssociationStatus.CONFIRMED_OTHER
        elif any(link.is_confirmed and not link.is_dismissed for link in same):
            status = HandleAssociationStatus.SAME_CONFIRMED
        elif any(not link.is_dismissed for link in same):
            status = HandleAssociationStatus.WEAK_SAME
        else:
            status = HandleAssociationStatus.UNKNOWN
        readable_ids = {link.pk for link in readable_links}
        conflict_ids = {
            link.pk
            for link in authoritative
            if (
                (link.subject_id == party.pk and link.is_dismissed)
                or (link.subject_id != party.pk and link.is_confirmed and not link.is_dismissed)
            )
        }
        return HandleAssociationAssessment(
            status=status,
            readable_links=readable_links,
            conflict_evidence_readable=conflict_ids.issubset(readable_ids),
        )

    def propose_claimed_handle(
        self,
        party: Any,
        handle: Any,
        *,
        evidence: Any,
        actor: Any,
        confidence: float = 0.4,
    ) -> Any:
        """Retain an unconfirmed address claim for human identity review.

        The evidence record may be a Message, document, or another readable native
        record. This owner deliberately records only that the source claimed the
        handle. Transport authentication remains unknown, independently of any
        later human confirmation of Party ownership.
        """

        party.require_access("write", actor)
        handle.require_access("read", actor)
        evidence.require_access("read", actor)
        return self._propose_claimed_handle_authorized(
            party, handle, evidence=evidence, actor=actor, confidence=confidence
        )

    def _propose_claimed_handle_authorized(
        self,
        party: Any,
        handle: Any,
        *,
        evidence: Any,
        actor: Any,
        confidence: float = 0.4,
    ) -> Any:
        """Retain a claim after the caller authorized exact Handle and evidence reads."""

        party.require_access("write", actor)
        if not 0 < confidence < self.model.REVIEW_CONFIDENCE:
            raise ValidationError(
                {"confidence": f"Claimed-handle proposals require confidence below {self.model.REVIEW_CONFIDENCE}."}
            )
        evidence_model = generic_pointer_model(type(evidence))
        evidence_ref = {
            "model": evidence_model._meta.label,
            "id": public_id_for(evidence_model, evidence.pk),
        }
        with system_context(reason="parties.statement.propose_claimed_handle"), transaction.atomic():
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            locked_handle = handles[handle.pk]
            locked_party = parties[party.pk]
            source = self.rule_source("source_sender")
            existing = (
                self.handle_statements()
                .filter(
                    subject=locked_party,
                    handle=locked_handle,
                    asserted_by=str(to_subject_ref(source)),
                )
                .first()
            )
            refs = list(existing.evidence) if existing is not None else []
            if evidence_ref not in refs:
                refs.append(evidence_ref)
            return locked_party.statements.set(
                aspect="handle",
                handle=locked_handle,
                values={},
                asserted_by=source,
                confidence=confidence,
                evidence=refs,
            )

    def suggest_for(self, handle: Any) -> Any:
        """Propose a party for a freshly-seen, unresolved ``handle`` (the EMAIL_MATCH producer).

        Three branches, none confirming its link and none duplicating an existing
        pair: indexed normalized twins contribute distinct candidate parties (the
        first at ``1.0``, which decides the owner, competing parties at ``0.3`` for review);
        otherwise an email whose non-generic domain matches a tracked
        :attr:`Organization.domain` contributes a rule suggestion at ``0.4``;
        otherwise no-op. Both branches stay inside ``handle.created_by``'s audit
        partition, and public mailbox-provider domains never imply organization
        membership. Returns the strongest created/existing link, or ``None``.
        """

        if handle.party_id is not None:
            return None
        if handle.created_by_id is None:
            return None
        handle_model = apps.get_model("parties", "Handle")
        twins = (
            handle_model.objects.filter(
                platform=handle.platform,
                normalized_value=handle.normalized_value,
                party__isnull=False,
                created_by_id=handle.created_by_id,
                party__created_by_id=handle.created_by_id,
            )
            .exclude(pk=handle.pk)
            .select_related("party")
            .order_by("sqid")
        )
        strongest = None
        seen_parties: set[Any] = set()
        for candidate in twins:
            if candidate.party_id in seen_parties:
                continue
            seen_parties.add(candidate.party_id)
            link = self.assert_handle(
                candidate.party,
                handle,
                confidence=1.0 if strongest is None else 0.3,
                asserted_by=self.rule_source("normalized_twin"),
                evidence=[candidate],
            )
            if strongest is None:
                strongest = link
        if strongest is not None:
            return strongest
        if handle.platform == handle_model.Platform.EMAIL and "@" in handle.normalized_value:
            domain = handle.normalized_value.rsplit("@", 1)[1]
            organization_model = apps.get_model("parties", "Organization")
            org = (
                organization_model.objects.filter(
                    created_by_id=handle.created_by_id,
                    domain__iexact=domain,
                ).first()
                if domain and domain not in GENERIC_EMAIL_DOMAINS
                else None
            )
            if org is not None:
                return self.assert_handle(
                    org,
                    handle,
                    confidence=0.4,
                    asserted_by=self.rule_source("email_domain"),
                    evidence=[handle],
                )
        return None

    def reconcile_same_as(self, *, limit: int = 1000) -> int:
        """Process at most ``limit`` unsupported or unasserted pairs per run.

        Current assertions and dismissals are skipped before the cap, so retained
        statements advance discovery without continuation state. Withdrawal
        checks support independently; unvisited pairs are never withdrawn.
        """

        limit = max(0, min(int(limit), 1000))
        if not limit:
            return 0
        source = self.rule_source("same_as")
        handles = (
            apps.get_model("parties", "Handle")
            ._base_manager.exclude(normalized_value="")
            .filter(
                party__merged_into__isnull=True,
                party__is_trashed=False,
                party__isnull=False,
            )
            .order_by("platform", "normalized_value", "party_id", "pk")
        )
        source_ref = to_subject_ref(source)
        statements = self.model.same_as.model.statements
        current = statements.current().filter(status=statements.model.StatementStatus.ASSERTED)
        shared = handles.filter(party_id=OuterRef("subject__left_id")).filter(
            Exists(
                handles.filter(
                    party_id=OuterRef(OuterRef("subject__right_id")),
                    platform=OuterRef("platform"),
                    normalized_value=OuterRef("normalized_value"),
                )
            )
        )
        unsupported = list(
            current.filter(asserted_by=str(source_ref))
            .exclude(Exists(shared))
            .order_by("subject__left_id", "subject__right_id")
            .values_list("subject_id", flat=True)[:limit]
        )
        changed = statements.withdraw(source, unsupported) if unsupported else 0

        def candidates() -> Iterable[Any]:
            for _, grouped in groupby(handles.iterator(), key=lambda row: (row.platform, row.normalized_value)):
                parties: defaultdict[Any, list[Any]] = defaultdict(list)
                for handle in grouped:
                    parties[handle.party_id].append(handle)
                for left_id, right_id in combinations(sorted(parties), 2):
                    if not current.filter(subject__left_id=left_id, subject__right_id=right_id).exists():
                        yield left_id, right_id, (*parties[left_id], *parties[right_id])

        for left_id, right_id, evidence in islice(candidates(), limit - changed):
            left, right = (self.model._base_manager.get(pk=pk) for pk in (left_id, right_id))
            pair = self.model.same_as.for_pair(left.with_actor(source_ref), right)
            pair.statements.set(
                aspect="same_as",
                values={},
                asserted_by=source,
                evidence=[{"model": row._meta.label, "id": row.public_id} for row in evidence],
            )
            changed += 1
        return changed

    def resolve_handle_statements(self) -> None:
        """Reapply retained statements, including rows folded before cutover."""
        party_model = apps.get_model("parties", "Party")
        ids = self.handle_statements().order_by("subject_id").values_list("subject_id", flat=True).distinct()
        for pk in ids.iterator():
            party_model._base_manager.get(pk=pk).statements.resolve()

    def reconcile_suggestions(self) -> int:
        """Reconcile the evidence-backed suggestions with every registered evidence provider.

        Display-name evidence is parties' own. Downstream addons contribute the rest
        as dotted callables, so parties reads no other addon's models:

        - ``ANGEE_PARTIES_SHARED_SENDER_PROVIDERS`` callables return handle ids whose
          display names are no evidence (list and notification senders).
        - ``ANGEE_PARTIES_SIGNING_PROVIDERS`` callables yield :class:`Signing` rows.
          Each yields all of its evidence, since a signature suggestion the signings
          no longer support is withdrawn. With no provider registered there is no
          signature evidence to judge, and the signature suggestions stay.

        A provider that raises does so before its pass writes anything. The verb
        runs as named system work: complete evidence spans every owner's mail,
        whatever the caller may read. Returns the suggestions created plus those
        withdrawn.
        """

        with system_context(reason="parties.reconcile_suggestions"):
            shared = frozenset(
                chain.from_iterable(provider() for provider in resolve_hooks("ANGEE_PARTIES_SHARED_SENDER_PROVIDERS"))
            )
            changed = self._reconcile_display_names(shared)
            if providers := resolve_hooks("ANGEE_PARTIES_SIGNING_PROVIDERS"):
                changed += self._reconcile_signatures(chain.from_iterable(provider() for provider in providers))
        return changed

    def _reconcile_signatures(self, signings: Iterable[Signing]) -> int:
        """Reconcile weak party-to-phone suggestions with every signature in the owners' mail.

        Each :class:`Signing` is neutral evidence that one sender signed with one
        fragment. This manager owns phone extraction, Handle creation, link
        provenance, owner partition and the durable-pair check. A number is a
        person's own only while that one person (or one unresolved address) signs
        with it in the owner's mail: forwards, company switchboards and conference
        bridges repeat a number under several people, and none of them owns it.
        Each such number of a resolved sender is one ``0.3`` suggestion recording
        its fragment; an undecided suggestion the evidence no longer supports is
        withdrawn, so ``signings`` must be complete. An existing pair, including a
        dismissed anti-link, is never changed.

        A signer is the resolved party, or the address while it is unresolved: one
        person writing from a resolved and an unresolved address counts twice until
        the second resolves, and people resolved onto one shared card count once.
        """

        handle_model = apps.get_model("parties", "Handle")
        party_model = apps.get_model("parties", "Party")
        numbers: dict[str, tuple[str, ...]] = {}
        signers: defaultdict[tuple[Any, str], set[Any]] = defaultdict(set)
        evidence: dict[tuple[Any, Any, str], Any] = {}
        for signing in signings:
            if signing.owner_id is None:
                continue
            if signing.fragment_hash not in numbers:
                numbers[signing.fragment_hash] = self._signature_phone_values(signing.text, handle_model=handle_model)
            signer = ("party", signing.party_id) if signing.party_id is not None else ("handle", signing.sender_id)
            for value in numbers[signing.fragment_hash]:
                signers[signing.owner_id, value].add(signer)
                if signing.party_id is not None:
                    evidence.setdefault((signing.owner_id, signing.party_id, value), signing.sender_id)
        # Support stays inside the owner partition: another owner's mail never
        # proposes, or keeps, a suggestion in this owner's review queue.
        supported = {key for key in evidence if len(signers[key[0], key[2]]) == 1}
        party_ids = {party_id for _owner_id, party_id, _value in supported}
        existing = set(
            self.handle_statements()
            .filter(
                status=self.handle_statements().model.StatementStatus.ASSERTED,
                handle__platform=handle_model.Platform.PHONE,
                subject_id__in=party_ids,
            )
            .values_list("subject_id", "handle__normalized_value")
        )
        parties = party_model.objects.in_bulk(party_ids)
        created = 0
        ordered = sorted(evidence.items(), key=lambda item: (item[0][2], str(item[0][1])))
        for key, sender_id in ordered:
            owner_id, party_id, value = key
            party = parties.get(party_id)
            if (
                key not in supported
                or (party_id, value) in existing
                or party is None
                or party.created_by_id != owner_id
            ):
                continue
            handle = handle_model.objects.upsert(
                platform=handle_model.Platform.PHONE,
                value=value,
                created_by_id=owner_id,
            )
            if handle.created_by_id != owner_id:
                # Handles are globally unique by source identity. Evidence owned by
                # one directory must never attach another owner's pre-existing row.
                continue
            created += self._suggest(
                party,
                handle,
                confidence=0.3,
                kind="signature_phone",
                evidence=[handle_model._base_manager.get(pk=sender_id)],
            )
        withdrawn = self._withdraw_unsupported(
            "signature_phone", supported, ("handle__created_by_id", "subject_id", "handle__normalized_value")
        )
        return created + withdrawn

    def _reconcile_display_names(self, shared: frozenset[Any]) -> int:
        """Reconcile weak identity links with the full display names pooled per audit owner.

        A resolved handle supplies evidence only to a handle on another platform,
        and only for a party that bears the same full name itself. A one-word name
        (a first name, a brand) identifies nobody, and a party named otherwise, such
        as a nameless card holding several people's numbers, is not that person.
        ``shared`` handles are addresses several people speak through: their display
        names name whoever spoke last and are no evidence. Each distinct candidate
        party of an unresolved handle receives one ``0.4`` rule link. A suggestion is
        withdrawn only when its names no longer support it, not when its handle
        later gains an owner; existing pairs, including dismissed links, remain
        untouched.
        """

        handle_model = apps.get_model("parties", "Handle")
        supported: set[tuple[Any, Any]] = set()
        created = 0
        owner_ids = (
            handle_model.objects.exclude(created_by_id=None)
            .exclude(display_name="")
            .values_list("created_by_id", flat=True)
            # Clear the model's default ordering: its columns silently join the
            # DISTINCT, yielding one "distinct owner" PER HANDLE — the pass then
            # repeats its full per-owner sweep tens of thousands of times.
            .order_by()
            .distinct()
        )
        for owner_id in owner_ids:
            handles = tuple(
                handle_model.objects.filter(created_by_id=owner_id)
                .exclude(display_name="")
                .select_related("party")
                .order_by("sqid")
            )
            # The durable-pair check reads once per owner, not once per pair:
            # steady state re-proposes tens of thousands of existing links, and a
            # get_or_create probe for each is the pass's dominant cost.
            existing_pairs = set(
                self.handle_statements()
                .filter(status=self.handle_statements().model.StatementStatus.ASSERTED, handle__created_by_id=owner_id)
                .values_list("subject_id", "handle_id")
            )
            pools: defaultdict[str, list[Any]] = defaultdict(list)
            for handle in handles:
                normalized_name = handle_model.normalize_display_name(handle.display_name)
                if " " in normalized_name and handle.pk not in shared:
                    pools[normalized_name].append(handle)

            for handle in handles:
                if handle.pk in shared:
                    continue
                normalized_name = handle_model.normalize_display_name(handle.display_name)
                seen_parties: set[Any] = set()
                for candidate in pools.get(normalized_name, ()):
                    if (
                        candidate.party_id is None
                        or candidate.party.created_by_id != owner_id
                        or handle_model.normalize_display_name(candidate.party.display_name) != normalized_name
                        or candidate.platform == handle.platform
                        or candidate.party_id in seen_parties
                    ):
                        continue
                    seen_parties.add(candidate.party_id)
                    supported.add((candidate.party_id, handle.pk))
                    if handle.party_id is not None or (candidate.party_id, handle.pk) in existing_pairs:
                        continue
                    existing_pairs.add((candidate.party_id, handle.pk))
                    created += self._suggest(
                        candidate.party,
                        handle,
                        confidence=0.4,
                        kind="display_name",
                        evidence=[candidate],
                    )
        return created + self._withdraw_unsupported("display_name", supported, ("subject_id", "handle_id"))

    def _withdraw_unsupported(self, kind: str, supported: set[tuple[Any, ...]], key: tuple[str, ...]) -> int:
        source = self.rule_source(kind)
        rows = self.handle_statements().filter(asserted_by=str(to_subject_ref(source)))
        keys = [
            {"subject": row["subject_id"], "aspect": "handle", "handle": row["handle_id"]}
            for row in rows.values("subject_id", "handle_id", *key)
            if tuple(row[name] for name in key) in supported
        ]
        return rows.model.objects.withdraw_unsupported(source, keys)

    def _suggest(self, party: Any, handle: Any, *, confidence: float, kind: str, evidence: list[Any]) -> int:
        source = self.rule_source(kind)
        existing = (
            self.handle_statements()
            .filter(subject=party, handle=handle, asserted_by=str(to_subject_ref(source)))
            .first()
        )
        self.assert_handle(party, handle, asserted_by=source, confidence=confidence, evidence=evidence)
        return int(existing is None or existing.status != existing.StatementStatus.ASSERTED)

    @staticmethod
    def _signature_phone_values(text: str, *, handle_model: Any) -> tuple[str, ...]:
        """Return deterministic canonical phone values mined from signature text.

        libphonenumber's matcher finds only valid numbers written with their country
        code. A digit run without one, such as a ticket or comment id, names no
        number and is never mined.
        """

        return tuple(
            sorted(
                {
                    handle_model.normalize_value(handle_model.Platform.PHONE, match.raw_string)
                    for match in PhoneNumberMatcher(text or "", None)
                }
            )
        )

    def identity_basis(self, party_id: str, *, actor: Any, lock: bool = False) -> IdentityBasis:
        """Read one Party's identity under ``actor``'s read authorization.

        ``current`` projects only the Address and PartyStatement rows ``actor`` can
        read (a link to an unreadable Handle stays hidden) and is what reviewers
        see. ``complete`` projects every row, read under a system context, so
        :attr:`IdentityBasis.facts_hash` is actor-independent: a reviewer who
        cannot read a private handle compares the same basis the workflow actor
        froze, while a concurrent change to that handle still invalidates the
        review. ``lock`` locks the complete row set in the shared Handle, Party,
        PartyStatement order, then the Party's addresses.
        """

        party = self.with_actor(actor).from_public_id(party_id)
        if party is None:
            raise ValidationError({"party_id": "Party was not found."})
        party.require_access("read", actor)
        reason = "parties.party.identity_basis"
        address_owner = apps.get_model("parties", "Address").objects
        link_owner = apps.get_model("parties", "Party").objects
        addresses = address_owner.system_context(reason=reason).canonical().filter(party=party)
        links = link_owner.handle_statements().filter(subject=party)
        if lock:
            _handles, locked_parties = link_owner.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=tuple(links.values_list("handle_id", flat=True)),
            )
            party = locked_parties[party.pk].with_actor(actor)
            addresses = addresses.lock_if_supported()
        address_rows = list(addresses.order_by("is_primary", "sqid"))
        link_rows = list(links.select_related("handle").order_by("pk"))
        readable_addresses = set(address_owner.with_actor(actor).filter(party=party).values_list("pk", flat=True))
        readable_handles = apps.get_model("parties", "Handle").objects.with_actor(actor).scoped().values("pk")
        readable_links = set(
            link_owner.handle_statements(actor=actor)
            .members()
            .filter(subject=party, handle_id__in=Subquery(readable_handles))
            .values_list("pk", flat=True)
        )
        return IdentityBasis(
            party=party,
            current=party.identity_values(
                [row for row in address_rows if row.pk in readable_addresses],
                [link for link in link_rows if link.pk in readable_links],
            ),
            complete=party.identity_values(address_rows, link_rows, include_evidence=True),
        )

    def identity_snapshot(self, party_id: str, *, actor: Any, lock: bool = False) -> tuple[Any, dict[str, Any]]:
        """Return the Party and its actor-visible identity from :meth:`identity_basis`."""

        basis = self.identity_basis(party_id, actor=actor, lock=lock)
        return basis.party, basis.current

    def apply_identity(
        self,
        *,
        party_id: str,
        expected_facts_hash: str,
        proposed: Mapping[str, Any],
        choices: Mapping[str, str],
        actor: Any,
    ) -> tuple[str, dict[str, str]]:
        """Apply plain reviewed values while the locked identity basis still matches.

        ``expected_facts_hash`` is :attr:`IdentityBasis.facts_hash` from review;
        it is compared with the complete basis locked here, and exact-replace
        expectations come from that same basis. Every write stays authorized as
        ``actor``. A replay after a successful change returns conflict because
        the retained basis hash no longer matches; unchanged choices are
        idempotent no-ops.
        """

        allowed = {
            "name_action": {"keep", "replace"},
            "address_action": {"keep", "add", "replace"},
            "handle_action": {"keep", "confirm", "dismiss"},
        }
        if set(choices) != set(allowed) or any(value not in allowed[name] for name, value in choices.items()):
            raise ValidationError({"choices": "Identity choices are invalid."})
        with transaction.atomic(), actor_context(actor):
            basis = self.identity_basis(party_id, actor=actor, lock=True)
            party, current = basis.party, basis.complete
            party.require_access("write")
            if basis.facts_hash != expected_facts_hash:
                return "conflict", {}
            results = {"name_result": "kept", "address_result": "kept", "handle_result": "kept"}
            if choices["name_action"] == "replace":
                results["name_result"] = self.replace_name_exact(
                    party=party,
                    expected=current["name"],
                    proposed=str(proposed["name"]),
                    actor=actor,
                )
            addresses = apps.get_model("parties", "Address").objects
            if choices["address_action"] == "add":
                results["address_result"], _ = addresses.attach_exact(
                    party=party,
                    values=proposed["address"],
                    actor=actor,
                    label=proposed["address"]["label"],
                    conflict="append",
                )
            elif choices["address_action"] == "replace":
                primary = next((row for row in current["addresses"] if row["is_primary"]), None)
                expected = (
                    addresses.system_context(reason="parties.party.apply_identity").from_public_id(primary["id"])
                    if primary
                    else None
                )
                results["address_result"], _ = addresses.replace_primary_exact(
                    party=party,
                    values=proposed["address"],
                    actor=actor,
                    expected_id=expected.pk if expected else None,
                    label=proposed["address"]["label"],
                )
            if choices["handle_action"] != "keep":
                link = self.handle_statements(actor=actor).from_public_id(proposed["handle"]["statement_id"])
                if link is None or link.subject_id != party.pk:
                    raise ValidationError({"handle_action": "The proposed statement changed during review."})
                getattr(link.with_actor(actor), choices["handle_action"])()
                results["handle_result"] = f"{choices['handle_action']}ed"
            outcome = "applied" if any(value not in {"kept", "matched"} for value in results.values()) else "unchanged"
            return outcome, results

    def apply_same_as_pair(self, *, left_id: str, right_id: str, survivor: str, action: str, actor: Any) -> str:
        """Apply one merge or durable dismissal using plain values and an explicit actor."""

        if survivor not in {"left", "right"} or action not in {"merge", "keep_separate"}:
            raise ValidationError({"pair": "Unsupported same_as pair operation."})
        with transaction.atomic(), actor_context(actor):
            left = self.with_actor(actor).from_public_id(left_id)
            right = self.with_actor(actor).from_public_id(right_id)
            if left is None or right is None:
                raise ValidationError({"pair": "Same_as pair references a missing party."})
            if action == "keep_separate":
                self.model.same_as.for_pair(left.with_actor(actor), right).with_actor(actor).dismiss(aspect="same_as")
                return "dismissed"
            locked = {
                row.pk: row.with_actor(actor)
                for row in self.lock_if_supported().filter(pk__in=[left.pk, right.pk]).order_by("pk")
            }
            into, source = (
                (locked[left.pk], locked[right.pk]) if survivor == "left" else (locked[right.pk], locked[left.pk])
            )
            already_merged = source.merged_into_id == into.pk
            if already_merged and into.merged_into_id is None:
                return "already_merged"
            into.merge(records=[source.public_id])
            return "already_merged" if already_merged else "merged"

    def replace_name_exact(self, *, party: Any, expected: str, proposed: str, actor: Any) -> str:
        """Apply a reviewed name only while the frozen Party name still matches."""

        normalized = " ".join(proposed.split()).strip()
        if not normalized:
            raise ValidationError({"name": "A replacement Party name must not be empty."})
        with transaction.atomic(), actor_context(actor):
            locked = self.with_actor(actor).locked_get(pk=party.pk)
            if locked.display_name != expected:
                raise ValidationError({"name": "The Party name changed during review."})
            if not locked.has_access("write"):
                raise PermissionDenied("write access to the Party is required")
            if locked.display_name == normalized:
                return "matched"
            locked.display_name = normalized
            locked.save(update_fields=["display_name", "updated_at"])
            return "replaced"

    def circle_names_for(self, party: Any) -> list[str]:
        """Return one party's actor-visible circle names through the fast projection.

        Resource reads install :meth:`PartyQuerySet.with_circle_names`; alternate
        authored paths fall back to the same independently scoped membership and
        circle collections instead of silently presenting an empty list.
        """

        prefetched = getattr(party, "_angee_visible_circle_members", None)
        if prefetched is not None:
            return list(dict.fromkeys(membership.circle.name for membership in prefetched))

        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        visible_circle_ids = circle_model.objects.all().scoped_for_aggregate().values("pk")
        return list(
            circle_member_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                party_id=party.pk,
                circle_id__in=Subquery(visible_circle_ids),
            )
            .order_by("circle__name")
            .values_list("circle__name", flat=True)
            .distinct()
        )

    def for_user(self, user: Any) -> Any:
        """Return the :class:`Person` linked to ``user``, get-or-created on the ``user`` O2O.

        The single owner of the one-Person-per-user invariant: every identity-link
        writer (OIDC first login, the connect flows, messaging reaction attribution)
        routes through here, so a user never grows two person rows. Targets the
        ``Person`` model explicitly, runs within the caller's ``system_context``, and
        stamps ``created_by``.
        """

        person_model = apps.get_model("parties", "Person")
        person, _created = person_model.objects.get_or_create(
            user=user,
            defaults={"display_name": _user_display_name(user), "created_by_id": user.pk},
        )
        return person

    def follow_user_name(self, user: Any) -> None:
        """Rename ``user``'s linked :class:`Person` after the account, as :meth:`for_user` names it.

        Runs within the caller's ``system_context``; a person already carrying
        the account's name is left untouched.
        """

        name = _user_display_name(user)
        person_model = apps.get_model("parties", "Person")
        for person in person_model.objects.filter(user=user).exclude(display_name=name):
            person.display_name = name
            person.save(update_fields=["display_name", "updated_at"])

    def search_display_name(self, query: str, *, limit: int = 20) -> list[Any]:
        """Return a bounded actor-visible people list filtered by display name."""

        bounded = max(1, min(int(limit), 100))
        return list(
            self.canonical()
            .with_circle_names()
            .filter(display_name__icontains=query.strip())
            .order_by("display_name", "sqid")[:bounded]
        )

    def identity_for_user_id(self, user_id: Any) -> Any | None:
        """Return the existing Person party linked to ``user_id`` without creating one."""

        person_model = apps.get_model("parties", "Person")
        return person_model._base_manager.filter(user_id=user_id).first()

    def user_for(self, party: Any) -> Any | None:
        """Return the platform user linked to ``party`` when it is a Person.

        This manager owns the Party-to-Person MTI lookup so consumers never
        inspect the concrete child table or its base manager themselves. An
        organization or external Person without a user resolves to ``None``.
        """

        person_model = apps.get_model("parties", "Person")
        person = person_model._base_manager.select_related("user").filter(pk=party.pk).first()
        if person is None or person.user_id is None:
            return None
        return person.user

    def lock_contact(
        self,
        person: Any | None,
        *,
        parsed: ParsedContact | None = None,
    ) -> None:
        """Lock the mapped contact aggregate before a caller rechecks its base.

        Follow the shared Handle, Party, PartyStatement order before locking the
        Person child and its mapped collections. The parent row lock also fences
        inserts through its foreign keys while the page transaction is open.
        """

        associations = apps.get_model("parties", "Party").objects
        handles = apps.get_model("parties", "Handle").objects
        handle_ids = (
            set(associations.handle_statements().filter(subject_id=person.pk).values_list("handle_id", flat=True))
            if person is not None
            else set()
        )
        if parsed is not None:
            handle_ids.update(
                handles.filter(
                    Q(platform="email", value__in=[item[0] for item in parsed.emails])
                    | Q(platform="phone", value__in=[item[0] for item in parsed.phones])
                ).values_list("pk", flat=True)
            )
        associations.lock_identity_rows(
            party_ids=(person.pk,) if person is not None else (),
            handle_ids=handle_ids,
        )
        if person is None:
            return
        list(
            apps.get_model("parties", "Person")
            .objects.filter(
                pk=person.pk,
            )
            .order_by("pk")
            .lock_if_supported()
        )
        list(
            apps.get_model("parties", "Address")
            .objects.filter(
                party_id=person.pk,
            )
            .order_by("pk")
            .lock_if_supported()
        )
        list(
            apps.get_model("parties", "Relationship")
            .objects.filter(
                party_id=person.pk,
                kind__slug="employee",
                other_party__isnull=True,
            )
            .order_by("pk")
            .lock_if_supported()
        )

    def project_contact(self, person: Any) -> ParsedContact:
        """Read one contact through the same batched projection used by sync."""

        projected = self.project_contacts((person,))
        if person.pk not in projected:
            raise apps.get_model("parties", "Person").DoesNotExist
        return projected[person.pk]

    def project_contacts(self, people: Iterable[Any]) -> dict[Any, ParsedContact]:
        """Project contacts with bounded queries and no avatar storage reads.

        Explicitly bound value queries read related scalar facts in bulk. The
        shared contact projection owns canonical ordering and JSON encoding.
        Contact points are only the person's asserted links: a suggestion stays
        inside Angee until a human confirms it, so it is never written to a card.
        """

        people = tuple(apps.get_model("parties", "Person").objects.filter(pk__in=[row.pk for row in people]))
        ids = [person.pk for person in people]
        if not ids:
            return {}
        platforms = apps.get_model("parties", "Handle").Platform
        handles: dict[Any, dict[str, list[tuple[str, str, bool]]]] = defaultdict(lambda: defaultdict(list))
        for row in (
            self.handle_statements()
            .asserted()
            .filter(subject_id__in=ids, handle__platform__in=(platforms.EMAIL, platforms.PHONE))
            .values("subject_id", "handle__platform", "handle__value", "handle__label", "handle__is_preferred")
        ):
            handles[row["subject_id"]][row["handle__platform"]].append(
                (row["handle__value"], row["handle__label"], row["handle__is_preferred"])
            )
        addresses: dict[Any, list[ParsedAddress]] = defaultdict(list)
        for address in apps.get_model("parties", "Address").objects.canonical().filter(party_id__in=ids):
            addresses[address.party_id].append(
                ParsedAddress(
                    label=address.label,
                    po_box=address.po_box,
                    extended=address.extended,
                    street=address.street,
                    city=address.city,
                    region=address.region,
                    postal_code=address.postal_code,
                    country=str(address.country),
                )
            )
        employment = {
            edge.party_id: edge
            for edge in (
                apps.get_model("parties", "Relationship")
                .objects.canonical()
                .filter(party_id__in=ids, kind__slug="employee", other_party__isnull=True)
            )
        }
        photos = {
            row["pk"]: ParsedPhoto(content_hash=row["content_hash"], mime=row["mime_type__mime_type"] or "")
            for row in (
                apps.get_model("storage", "File")
                .objects.filter(pk__in=[person.avatar_id for person in people if person.avatar_id])
                .values("pk", "content_hash", "mime_type__mime_type")
            )
        }
        result = {}
        for person in people:
            edge = employment.get(person.pk)
            result[person.pk] = ParsedContact(
                # ingest_contact stores the placeholder for a nameless card; the card stays nameless.
                display_name="" if person.display_name == person.PLACEHOLDER_NAME else person.display_name,
                name_prefix=person.name_prefix,
                given_name=person.given_name,
                additional_name=person.additional_name,
                family_name=person.family_name,
                name_suffix=person.name_suffix,
                nickname=person.nickname,
                notes=person.notes,
                birthday=person.birthday,
                anniversary=person.anniversary,
                emails=tuple(handles[person.pk][platforms.EMAIL]),
                phones=tuple(handles[person.pk][platforms.PHONE]),
                addresses=tuple(addresses[person.pk]),
                organization=edge.other_name if edge is not None else "",
                title=edge.title if edge is not None else "",
                role=edge.notes if edge is not None else "",
                photo=photos.get(person.avatar_id),
            )
        return result

    def prepare_contact(self, parsed: ParsedContact, *, created_by_id: Any) -> ParsedContact:
        """Store fetched photo bytes before contact locks and name countries as stored.

        The photo keeps its content address. Addresses take their stored form
        (:meth:`AddressManager.as_stored`), so the projection both sync sides compare
        reads a card's "USA" as the stored "US".
        """

        addresses = apps.get_model("parties", "Address").objects
        parsed = replace(parsed, addresses=tuple(addresses.as_stored(address) for address in parsed.addresses))
        photo = parsed.photo
        if photo is None or photo.content_hash:
            return parsed
        if photo.uri:
            raise ValidationError("Contact photo URIs must be resolved before preparation.")
        if not photo.data:
            return replace(parsed, photo=None)
        extension = mimetypes.guess_extension(photo.mime) if photo.mime else ""
        avatar = apps.get_model("storage", "File").objects.ingest_bytes(
            photo.data,
            filename=f"avatar{extension or '.bin'}",
            owner_id=created_by_id,
        )
        mime: Any = avatar.mime_type
        return replace(
            parsed,
            photo=ParsedPhoto(content_hash=avatar.content_hash, mime=mime.mime_type if mime is not None else ""),
        )

    def resolve_contact_photo(self, photo: ParsedPhoto | None, *, lock: bool = False) -> Any:
        """Resolve a prepared avatar, optionally validating it under its row lock."""

        if photo is None:
            return None
        if not photo.content_hash or photo.data is not None or photo.uri:
            raise ValidationError("Contact photos must have a prepared content address.")
        rows = apps.get_model("storage", "File").objects.filter(
            content_hash=photo.content_hash,
            mime_type__mime_type=photo.mime,
            upload_state=UploadState.READY,
            is_trashed=False,
        )
        if lock:
            rows = rows.lock_if_supported()
        avatar = rows.order_by("pk").first()
        if avatar is None:
            raise ValidationError("The prepared contact photo is no longer available.")
        return avatar

    def ingest_contact(
        self,
        parsed: ParsedContact,
        *,
        folder: Any,
        created_by_id: Any,
        link: Any,
        target: Any = None,
    ) -> Any:
        """Assert one link's contact facts; preserve other sources and person preferences.

        The link owns stable source identity, transport version and lossless bytes.
        Addresses are matched by content, including repeated equal addresses; removed
        members withdraw only this source's statements instead of deleting rows.
        """
        if not parsed.uid:
            return None
        if folder.directory_id is None or link.stream.integration_id != folder.directory_id:
            raise ValidationError("Contact ingestion requires its directory's observed link.")
        if link.stream.partition != folder.source_href or link.external_key != parsed.uid:
            raise ValidationError("The contact does not match the observed link identity.")
        parsed = self.prepare_contact(parsed, created_by_id=created_by_id)
        people = apps.get_model("parties", "Person").objects
        handles = apps.get_model("parties", "Handle").objects
        addresses = apps.get_model("parties", "Address")
        relationships = apps.get_model("parties", "Relationship")
        source = folder.directory.integration_ptr
        with transaction.atomic():
            target = target or link.target
            if target is not None:
                self.lock_contact(target, parsed=parsed)
                target = people.lock_if_supported().get(pk=target.pk)
            person = target or people.create(
                display_name=(parsed.display_name or parsed.family_name or people.model.PLACEHOLDER_NAME),
                created_by_id=created_by_id,
            )
            parent = apps.get_model("parties", "Party")._base_manager.get(pk=person.pk)
            apps.get_model("integrate", "RecordLink").objects.bind_target(link, parent)
            for subject, aspect, values in (
                (
                    parent,
                    "identity",
                    {"display_name": (parsed.display_name or parsed.family_name or people.model.PLACEHOLDER_NAME)},
                ),
                (
                    parent,
                    "contact",
                    {"notes": parsed.notes, "avatar": self.resolve_contact_photo(parsed.photo, lock=True)},
                ),
                (
                    person,
                    "name",
                    {name: getattr(parsed, name) for name in person.statements.model.statement_aspects["name"].fields},
                ),
                (person, "dates", {"birthday": parsed.birthday, "anniversary": parsed.anniversary}),
            ):
                subject.statements.set(aspect=aspect, values=values, asserted_by=source, link=link)
            supported_handles = []
            for platform, points in (("email", parsed.emails), ("phone", parsed.phones)):
                for value, label, preferred in points:
                    handle = handles.upsert(
                        platform=platform,
                        value=value,
                        created_by_id=created_by_id,
                        display_name=parsed.display_name,
                        label=label,
                        is_preferred=preferred,
                    )
                    mapped = {"label": label, "is_preferred": preferred}
                    dirty = [name for name, value in mapped.items() if getattr(handle, name) != value]
                    if dirty:
                        for name in dirty:
                            setattr(handle, name, mapped[name])
                        handle.save(update_fields=(*dirty, "updated_at"))
                    self.assert_handle(parent, handle, asserted_by=source, link=link, evidence=[parent])
                    supported_handles.append({"subject": parent, "aspect": "handle", "handle": handle})
            parent.statements.withdraw_unsupported(source, supported_handles, aspect="handle", link=link)
            supported_addresses: list[Any] = []
            for parsed_address in parsed.addresses:
                values = asdict(parsed_address)
                address = (
                    addresses._base_manager.filter(party_id=person.pk, **values)
                    .exclude(
                        pk__in=supported_addresses,
                    )
                    .order_by("pk")
                    .first()
                )
                if address is None:
                    address = addresses.objects.create(party_id=person.pk, created_by_id=created_by_id, **values)
                address.statements.set(aspect="address", values=values, asserted_by=source, link=link)
                supported_addresses.append(address.pk)
            removed = addresses.statements.filter(link=link).exclude(subject_id__in=supported_addresses)
            addresses.statements.withdraw(source, list(removed.values_list("subject_id", flat=True)), link=link)
            kinds = apps.get_model("parties", "RelationshipKind")
            try:
                employee = kinds.objects.get(slug="employee")
            except kinds.DoesNotExist as error:
                raise ValidationError(
                    "CardDAV employment sync requires the employee RelationshipKind master row."
                ) from error
            previous = relationships.statements.filter(link=link, subject__party_id=person.pk, subject__kind=employee)
            edge = relationships._base_manager.filter(pk__in=previous.values("subject_id")).order_by("pk").first()
            if parsed.organization:
                if edge is None:
                    edge = relationships.objects.create(
                        party_id=person.pk, kind=employee, other_name=parsed.organization, created_by_id=created_by_id
                    )
                edge.statements.set(
                    aspect="relationship",
                    asserted_by=source,
                    link=link,
                    values={
                        "other_name": parsed.organization,
                        "title": parsed.title,
                        "notes": parsed.role,
                        "started_at": None,
                        "ended_at": None,
                    },
                )
            elif edge is not None:
                edge.statements.withdraw(source, link=link)
            person.refresh_from_db()
            return person


def _user_display_name(user: Any) -> str:
    """Return a human display name for a user's Person, falling back to a stable id."""

    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    username = user.get_username() if hasattr(user, "get_username") else getattr(user, "username", "")
    for candidate in (full_name, username, getattr(user, "email", "")):
        text = (candidate or "").strip()
        if text:
            return text
    return str(user.pk)
