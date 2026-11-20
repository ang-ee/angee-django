"""Managers that own the directory-sync write path for parties.

A directory backend parses a source into neutral ``ParsedContact`` and
``ParsedOrganization`` rows; these managers turn one into a ``Party`` (a
``Person`` or an ``Organization``) and its ``Handle`` / ``PartyStatement`` /
``Address`` / ``Relationship`` rows. A contact is keyed by the external key of
its observed RecordLink, handles dedupe on
``(platform, value)``, and ``handle_count`` plus the resolved ``Handle.party`` are
maintained here in the same transaction — so every directory source shares one
write path (the map lives on the models, not in each backend) and a re-sync
converges instead of duplicating. The sync runs under ``system_context``, so
``created_by`` is set explicitly to the directory owner; a new party's owner
follows it.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from functools import partial
from itertools import batched, chain, combinations
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Self, TypedDict, cast

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.prefetch import GenericPrefetch
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, models, transaction
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
    prefetch_related_objects,
)
from django.db.models.functions import Coalesce, NullIf
from django.db.models.lookups import Exact
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.encoding import punycode
from phonenumbers import PhoneNumberMatcher
from pydantic import ValidationError as PydanticValidationError
from rebac import (
    PermissionDenied,
    actor_context,
    check_new,
    current_actor,
    system_context,
)
from rebac.actors import is_sudo, to_subject_ref
from rebac.field_visibility import redacted_fields
from rebac.resources import model_resource_type

from angee.base.fields.identity import SqidField
from angee.base.hooks import resolve_hooks
from angee.base.identity import public_data_id_field
from angee.base.lineage import concrete_child
from angee.base.models.hierarchy import HierarchyQuerySet
from angee.base.models.identity import instances_from_public_ids, public_id_for
from angee.base.models.trash import ArchiveQuerySet
from angee.base.pagination import clamp_page_size
from angee.base.permissions import actor_user_id, reader_queryset
from angee.base.querysets import MergeableQuerySet, by_key, query_batch_size
from angee.base.refs import generic_pointer_model
from angee.base.serialization import canonical_json
from angee.integrate.errors import IntegrationError
from angee.integrate.models import IntegrationLifecycle, IntegrationManager
from angee.integrate.states import LinkStatus, StreamDirection
from angee.parties.backends import AvatarReport, ParsedAddress, ParsedContact, ParsedOrganization, ParsedPhoto
from angee.parties.domains import GENERIC_EMAIL_DOMAINS
from angee.provenance.context import changeset
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.provenance.evidence import EvidenceReference
from angee.provenance.statements import (
    SourceKind,
    StatementQuerySet,
    StatementSourceMixin,
    StatementSubjectManager,
    StatementSubjectQuerySet,
)
from angee.storage.exceptions import UploadTooLarge, UploadTypeRefused

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations

    from angee.integrate.models import RecordLink
    from angee.parties.connections import ParsedConnection
    from angee.parties.mixins import PartyStatementMixin
    from angee.parties.models import Circle, Directory, Folder, Handle, Organization, Party, Person, Relationship

logger = logging.getLogger(__name__)

_DISPLAY_OWN_AVATAR = "_display_own_avatar"
"""Where a page's prefetch parks each party's own avatar the actor can read."""
_DISPLAY_AVATAR_HANDLES = "_display_avatar_handles"
"""Where a page's prefetch parks each party's ranked handles with an avatar the actor can read."""
VISIBLE_CIRCLE_MEMBERS = "_angee_visible_circle_members"
"""Where a read parks each party's actor-visible circle memberships (:meth:`PartyQuerySet.circle_members_prefetch`)."""

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
    """The creator of the mail the fragment came from: the user whose parties it may suggest into."""


class DisplayAvatarPrefetches(NamedTuple):
    """The reads :meth:`PartyManager.display_avatar` batches for a page of parties."""

    own: Prefetch
    """Each party's own avatar, when the actor can read it."""
    handles: Prefetch
    """Each party's ranked handles with an avatar the actor can read, avatar loaded."""


IDENTITY_BASIS_DIGEST = "angee.parties.identity_basis"
"""The purpose of an identity review's digest (:attr:`IdentityBasis.facts_hash`); fixed: issued digests rely on it."""
DUPLICATE_SET_DIGEST = "angee.parties.duplicate_set"
"""The purpose of a batch merge set's digest (:attr:`DuplicateSetPreview.digest`)."""


def keyed_facts_digest(purpose: str, facts: Any) -> str:
    """A keyed digest of facts a reader was shown, to compare later under a lock.

    ``purpose`` is Django's ``salted_hmac`` key salt: each purpose derives its
    own key from the site secret, so a digest issued for one purpose never
    stands for another over the same facts, and a client holding a digest
    cannot confirm a guess of what it covers.
    """

    return salted_hmac(purpose, canonical_json(facts), algorithm="sha256").hexdigest()


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

        return keyed_facts_digest(IDENTITY_BASIS_DIGEST, self.complete)


class CircleQuerySet[CircleT: Circle](HierarchyQuerySet[CircleT], AngeeHistoryQuerySet[CircleT]):
    """Circle read scopes: the hierarchy subtree vocabulary over the Angee base."""

    def memberships(self, *, confirmed_only: bool = False) -> Any:
        """Readable memberships in these readable circles, optionally confirmed only."""

        member_model = apps.get_model("parties", "CircleMember")
        members = reader_queryset(member_model, self.actor())
        return members.scoped_for_aggregate().filter(self.memberships_filter(
            self.scoped_for_aggregate(), confirmed_only=confirmed_only,
        ))

    @staticmethod
    def memberships_filter(circles: models.QuerySet, *, confirmed_only: bool = False) -> Q:
        """Membership edges within the caller's readable or structural circles."""
        predicate = Q(circle_id__in=Subquery(circles.order_by().values("pk")))
        if confirmed_only:
            predicate &= Q(is_confirmed=True, is_dismissed=False)
        return predicate

    def containing_parties(self, parties: Any, *, confirmed_only: bool = False) -> Self:
        """Readable circles containing these parties through readable memberships."""

        members = self.memberships(confirmed_only=confirmed_only).filter(party__in=parties)
        return self.filter(pk__in=Subquery(members.order_by().values("circle_id")))

    def with_member_counts(self) -> Self:
        """Annotate each circle with its distinct readable-person count across its readable subtree."""

        circles = reader_queryset(apps.get_model("parties", "Circle").objects.all(), self.actor())
        people = reader_queryset(apps.get_model("parties", "Person").objects.all(), self.actor())
        visible_person_ids = people.scoped_for_aggregate().canonical().values("pk")
        subtree = circles.filter(owner_id=OuterRef(OuterRef("owner_id")), path__startswith=OuterRef(OuterRef("path")))
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


_CircleManagerBase = AngeeHistoryManager.from_queryset(CircleQuerySet)


class CircleManager[CircleT: Circle](_CircleManagerBase[CircleT]):
    """Keep a stable import identity for circle subtree managers."""


class _HandleSenderName(TypedDict):
    _sender_name: str


class SameAsReviewCounts(NamedTuple):
    """How many pairs a reader may review, and how many are certain (:meth:`PartyManager.same_as_review_counts`)."""

    pairs: int
    certain: int


class HolderRepair(NamedTuple):
    """What a holder repair did (:meth:`HandleManager.resolve_stale_owners`)."""

    stated: int
    """Holders written onto their row that the ``stored_holder`` rule now states."""
    confirmed: tuple[Any, ...]
    """Handles whose confirmed holder was left as it is, for a person to confirm."""
    contested: tuple[Any, ...]
    """Handles whose holder was left as it is because another party's applied statement stands on the row."""
    resolved: int = 0
    """Holders whose statements no longer decided them, re-resolved."""
    remaining: int = 0
    """Holders a capped run left for the next one, at most this many (``limit``)."""


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

        actor = self.actor() or current_actor()
        handles = self.with_actor(actor).scoped() if actor is not None else self.none()
        return handles.annotate(_sender_name=handles.sender_name_expression())

    def sender_name_expression(self, prefix: str = "") -> Coalesce:
        """Project the name rule onto an already-readable handle or handle join.

        Callers authorize the handle population; the party fallback keeps its
        own read gate. Prefix follows Django relation paths, including ``__``.
        A merged handle names its survivor's party, since messages and
        recipients stay on the handle they used; its envelope name is its own.
        """

        actor = self.actor() or current_actor()
        party_model = apps.get_model("parties", "Party")
        party_id = MergeableQuerySet.survivor_field(prefix, "party_id", ref=OuterRef)
        party_name = party_model.objects.filter(pk=party_id).readable_scalar_subquery("display_name", actor=actor)
        confirmed = MergeableQuerySet.survivor_field(prefix, "party_link_confirmed")
        return Coalesce(
            NullIf(
                Case(
                    When(Exact(confirmed, True), then=party_name),
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
            .for_reader(self.actor() or current_actor())
            .applied()
            .filter(subject_id=OuterRef("party_id"), handle_id=OuterRef("pk"))
        )
        return Subquery(rows.values("confidence")[:1])

    def stored_holders(self) -> Self:
        """Live handles whose holder no current statement names: written onto the row, never stated.

        ``party`` is the projection of the winning applied handle statement
        (:meth:`~angee.parties.models.Handle.resolve`). A holder a row carries with
        no statement behind it (a resource row, an older write) is no candidate,
        so the next resolution of the handle, such as a claim stated on it, clears
        it. A trashed holder is one still; a merged-away holder is named through
        its survivor. A holder some statement names, withdrawn or below review
        confidence, is not stored but stale: resolution owns it.
        """

        named = (
            apps.get_model("parties", "Party")
            .objects.handle_statements()
            .filter(handle_id=OuterRef("pk"), subject_id=MergeableQuerySet.survivor_id("party__", ref=OuterRef))
        )
        return self.filter(merged_into__isnull=True, party__isnull=False).exclude(Exists(named))

    def excluding_identity_of(self, user_id: Any) -> Self:
        """Drop the user's own handles: controlled by them or confirmed on their Person."""

        if user_id is None:
            return self
        handles = self.exclude(controller_id=user_id)
        identity = apps.get_model("parties", "Party").objects.identity_for_user_id(user_id)
        if identity is not None:
            handles = handles.exclude(party=identity, party_link_confirmed=True)
        return handles


_HandleManagerBase = AngeeHistoryManager.from_queryset(HandleQuerySet)


class HandleManager[HandleT: Handle](_HandleManagerBase[HandleT]):
    """Factory + upsert for handles (the contact-point write path)."""

    REPAIR_LIMIT: ClassVar[int] = 500
    """The most rows a handle repair writes by default (``resolve_stale_owners``, ``renormalize_handles``).

    The next run continues.
    """

    def for_user(self, user: Any) -> Any:
        """Return the account's self-claimed handle for internal attribution."""

        if user is None or user.pk is None:
            raise ValueError("Handle author is required.")
        email = (user.email or "").replace("\x00", "").strip()
        username = user.get_username().replace("\x00", "").strip()
        value = email or username or str(user.pk)
        display_name = user.get_full_name().replace("\x00", "").strip() or username or value
        return self.claim_control(
            user,
            platform=self.model.Platform.for_value(value),
            value=value,
            display_name=display_name,
            asserted_by=(
                user
                if user.source_kind == SourceKind.PERSON
                else apps.get_model("parties", "Party").objects.rule_source("source_sender")
            ),
        )

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
        is the stronger identity — the model's conditional unique key — so an
        address whose human-readable ``value`` drifts (a chat account behind a
        changed number) refreshes the existing row instead of forking a duplicate.
        :meth:`find_identity` owns which existing row the identity lands on,
        including the convergence of a ``@lid`` and its phone JID on the row that
        owns the value; only when it finds none does this create one, and a
        concurrent insert of the same identity lands where it did. A pre-check,
        never a failed save, keeps a value collision out of the caller's
        transaction. The value-keyed path never rewrites ``external_id`` (it is
        not the key it matched on).

        ``created_by_id`` stamps the creator, who holds the handle's access. Control
        is deliberately excluded from the generic refresh loop; :meth:`claim_control`
        writes it and handle folding carries a merged alias's controller onto a handle
        without one, so a routine upsert cannot silently move an account between users.
        The holder is refused too: it is the projection of the winning statement, so a
        source states the link (``Party.objects.assert_handle``) and resolution writes it.
        ``normalized_value`` tracks ``value`` on every hit. Display fields refresh
        on every hit; blank values never clobber. Source display text is truncated
        to its field limit; identifiers are validated without changing their meaning.
        """

        if "controller" in fields or "controller_id" in fields:
            raise TypeError("Handle control must be written through claim_control().")
        if {"party", "party_id", "party_link_confirmed"}.intersection(fields):
            raise TypeError("Handle.party is resolved from statements: state it with Party.objects.assert_handle().")
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
                field = self.model._meta.get_field(name)
                assert isinstance(field, models.Field)
                field.run_validators(identifier)
            except ValidationError as error:
                raise ValidationError({name: error}) from error
        for name in ("display_name", "label"):
            if fields.get(name):
                field = self.model._meta.get_field(name)
                assert isinstance(field, models.Field) and field.max_length is not None
                fields[name] = fields[name][: field.max_length]
        handle = self.find_identity(platform=platform, value=value, external_id=external_id)
        if handle is None:
            try:
                # A savepoint isolates the insert, so the caller's transaction stays usable.
                with transaction.atomic():
                    return self.create(
                        platform=platform,
                        value=value,
                        normalized_value=normalized_value,
                        created_by_id=created_by_id,
                        **fields,
                    )
            except IntegrityError:
                # A concurrent writer created this identity first: land where it did.
                handle = self.find_identity(platform=platform, value=value, external_id=external_id)
                if handle is None:
                    raise
        if external_id and handle.external_id == external_id:
            self._refresh(handle, {"value": value, "normalized_value": normalized_value, **fields})
        else:
            # The value matched, not the external id: never rewrite it here.
            refresh = {name: val for name, val in fields.items() if name != "external_id"}
            self._refresh(handle, {"normalized_value": normalized_value, **refresh})
        return handle

    def reidentify(
        self, handle: Any, *, expected_value: str, value: str, display_name: str = "", dry_run: bool = False,
    ) -> tuple[bool, bool] | None:
        """Rewrite a canonical handle or fold it into the existing value owner.

        This is admitted system work, like the native fold it composes.
        Return ``(folded, name_filled)``; a gone/redirected/changed source is a
        stale request and returns None. Source and target Handles are locked in
        canonical identity order before Parties and their statements. A value
        created during a rewrite is retried after releasing the failed attempt's
        locks, so the same order holds for its subsequent fold. ``dry_run``
        validates the same locked source and projected survivor without business
        or provenance mutations or merge signals. Native elevated-read audit
        records remain expected. Its result describes current facts, not a reservation.
        """

        if not is_sudo():
            raise PermissionDenied("Handle identity rewrites require an admitted system boundary.")
        while True:
            try:
                with transaction.atomic():
                    source = self.model.system_queryset().filter(pk=handle.pk).first()
                    if source is None or source.merged_into_id is not None or source.value != expected_value:
                        return None
                    target = self.find_identity(platform=source.platform, value=value)
                    ids = (source.pk, target.pk) if target is not None else (source.pk,)
                    handles, _parties = apps.get_model("parties", "Party").objects.lock_identity_rows(
                        party_ids=(), handle_ids=ids,
                    )
                    source = handles.get(source.pk)
                    if source is None or source.merged_into_id is not None or source.value != expected_value:
                        return None
                    if target is not None and target.pk != source.pk:
                        target = handles[target.pk]
                        if target.merged_into_id is not None:
                            continue
                        if dry_run:
                            target.validate_merge([source])
                            carried = target.merge_values([source])
                            return True, bool(display_name and not carried.get("display_name", target.display_name))
                        kept = target.fold([source])
                        folded = True
                    else:
                        if dry_run:
                            return False, bool(display_name and not source.display_name)
                        source.value = value
                        source.save(update_fields=("value", "updated_at"))
                        kept, folded = source, False
                    named = bool(display_name and not kept.display_name)
                    if named:
                        kept.display_name = display_name
                        kept.save(update_fields=("display_name", "updated_at"))
                    return folded, named
            except IntegrityError:
                target = self.find_identity(platform=handle.platform, value=value)
                if target is None or target.pk == handle.pk:
                    raise

    def find_identity(self, *, platform: str, value: str, external_id: str = "") -> Any | None:
        """Return the existing handle :meth:`upsert` lands an identity on, or ``None`` when it would create one.

        The ``external_id`` match wins, unless its ``value`` would move onto a
        contact point a different row already owns: a WhatsApp ``@lid`` handle
        whose sender resolved to an ``+E164`` a phone-JID handle holds. The value
        *is* the contact point, so that owner wins, as it does when no row has the
        external id; it keeps its own ``external_id`` (the other source's
        idempotency key), and the moved row keeps its old value for the offline
        backfill to merge. Without an external id, the row owning
        ``(platform, value)``. Read-only, so a verb that must never create a
        handle shares this rule.
        """

        if not external_id:
            return self.find_identities([(platform, value)]).get((platform, value))
        handles = self.filter(platform=platform)
        handle = handles.filter(external_id=external_id).first() if external_id else None
        if handle is None or handle.value != value:
            owner = handles.filter(value=value).exclude(pk=(handle.pk if handle is not None else None)).first()
            handle = owner or handle
        return handle.canonical() if handle is not None else None

    def find_identities(self, identities: Iterable[tuple[str, str]]) -> dict[tuple[str, str], Any]:
        """Return the handle each ``(platform, value)`` lands on, for the identities that have one.

        :meth:`find_identity`'s rule without an external id, for many identities in
        one read: the row owning ``(platform, value)``, resolved to its merge
        survivor (``canonical``, a read only for a merged row). Read-only, for a
        caller that resolves a whole batch (a sync page's attendees) at once.
        """

        wanted = set(identities)
        if not wanted:
            return {}
        rows = self.filter(value__in=sorted({value for _platform, value in wanted}))
        return {
            (row.platform, row.value): row.canonical() for row in rows if (row.platform, row.value) in wanted
        }

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

    def claim_control(
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
        """Record ``value`` as the account handle ``user`` controls and their normal identity claim.

        The one verb the connect flows (OIDC login, CardDAV/OAuth connect) call for
        the signed-in user's own address. It writes BOTH facts the model separates:
        the **control** fact (:attr:`Handle.controller` — this user sends/syncs as the
        address) and the **identity** fact (a normal-rank :class:`PartyStatement` to the
        user's own :class:`~angee.parties.models.Person`, so the address resolves to
        them). Directory *contacts'* handles get neither. Idempotent.

        A claim is contested when another user already controls the row. The
        existing controller is never replaced; instead the competing user's
        identity link is recorded unconfirmed at ``0.3`` confidence and returned
        for later review. The identity statement would move the address like a
        confirmation does, so where someone holds the address whom the
        statement's author may not take it from (``PartyManager.holding_blocker``:
        an account's own sign-in address is always its own), the identity link is
        stated as a claim for review at ``Party.CLAIM_CONFIDENCE``, as a contact
        states an address someone else holds, and control stays as it is. Neither
        contested path refuses or touches the handle's display fields: signing in
        and connecting never settle who holds an address. The whole decision is
        atomic and row-locked.
        """

        evidence = tuple(evidence)
        author = asserted_by or user
        person_model = apps.get_model("parties", "Person")
        link_owner = apps.get_model("parties", "Party").objects
        with (
            system_context(reason="parties.handle.claim_control"),
            transaction.atomic(),
        ):
            handle = self.upsert(platform=platform, value=value, created_by_id=user.pk)
            handles, _parties = link_owner.lock_identity_rows(
                party_ids=(),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            person = person_model.objects.for_user(user)
            controlled = handle.controller_id is not None and handle.controller_id != user.pk
            held = (
                not controlled
                and handle.party_id != person.pk
                and StatementSourceMixin.source_kind_for(to_subject_ref(author)) == SourceKind.PERSON
                and link_owner.holding_blocker(person, handle, author=author) is not None
            )
            if controlled or held:
                link_owner.assert_handle(
                    person,
                    handle,
                    confidence=0.3 if controlled else link_owner.model.CLAIM_CONFIDENCE,
                    asserted_by=author,
                    evidence=evidence,
                )
                return handle
            handle = self.upsert(
                platform=platform,
                value=value,
                created_by_id=user.pk,
                display_name=display_name,
                metadata=metadata or {},
            )
            if handle.controller_id is None:
                handle.controller = user
                handle.save(update_fields=["controller", "updated_at"])
            link_owner.assert_handle(
                person,
                handle,
                confidence=1.0,
                asserted_by=author,
                evidence=evidence,
            )
        return handle

    def state_stored_holders(self, handles: Iterable[Any] | None = None, *, limit: int | None = None) -> HolderRepair:
        """State each stored holder's link as the ``stored_holder`` rule, so resolution keeps it; return what it did.

        A holder no current statement names (:meth:`HandleQuerySet.stored_holders`)
        gains that rule's normal statement at full confidence, with the handle as its
        evidence, as named system work rechecked under the identity locks; a
        merged-away holder is stated for its survivor. Three holders are left as
        they are, so the repair moves no address and clears no confirmation. A
        trashed one: resolution ignores a trashed party, so stating its link would
        clear the row; the repair after a restore states it. A confirmed one,
        reported: only a person or a seed states the preferred link that keeps the
        flag, and the rule's normal statement would clear it. A contested one,
        reported, on a row where another live party's applied statement stands: the
        rule is the last kind the handle aspect ranks (below a person, a system of
        record, a directory and an import, above kinds it does not list), so that
        party would take the address. A run states at most ``limit`` holders and
        counts the ones it did not reach as ``remaining``.

        ``handles`` are resource rows a load just wrote, and the holder each names
        is the seed's word. What an earlier load stated for a holder a row no
        longer names is withdrawn first, else the old holder would take the address
        back, or stay linked beside the new one and be proposed as the same person;
        then each named holder no standing statement names is stated. Idempotent.
        """

        party_model = apps.get_model("parties", "Party")
        statements = party_model.objects.handle_statements()
        stated = 0
        remaining = 0
        confirmed: list[Any] = []
        contested: list[Any] = []
        with system_context(reason="parties.handle.state_stored_holders"):
            if handles is None:
                pending = list(self.stored_holders().order_by("pk").values_list("pk", "party_id"))
                naming = statements
            else:
                seeded = {row.pk: row.party_id for row in handles}
                self._withdraw_moved_holders(seeded)
                pending = sorted((pk, party_id) for pk, party_id in seeded.items() if party_id is not None)
                naming = statements.filter(status=statements.model.StatementStatus.ASSERTED)
            survivors = party_model.canonical_ids({party_id for _pk, party_id in pending})
            competing = statements.applied().filter(subject__is_trashed=False)
            source = None
            for index, (pk, party_id) in enumerate(pending):
                if limit is not None and stated >= limit:
                    remaining = len(pending) - index
                    break
                holder = survivors.get(party_id, party_id)
                with transaction.atomic():
                    locked, parties = party_model.objects.lock_identity_rows(party_ids=(holder,), handle_ids=(pk,))
                    row = locked.get(pk)
                    party = parties.get(holder)
                    if (
                        row is None
                        or party is None
                        or party.is_trashed
                        or (handles is None and row.party_id != party_id)
                        or naming.filter(handle_id=pk, subject_id=holder).exists()
                    ):
                        continue
                    if row.party_link_confirmed:
                        confirmed.append(pk)
                        continue
                    if competing.filter(handle_id=pk).exclude(subject_id=holder).exists():
                        contested.append(pk)
                        continue
                    source = source or party_model.objects.rule_source("stored_holder")
                    party_model.objects.assert_handle(party, row, asserted_by=source, evidence=[row])
                stated += 1
        return HolderRepair(stated, tuple(confirmed), tuple(contested), remaining=remaining)

    @staticmethod
    def _withdraw_moved_holders(holders: Mapping[Any, Any]) -> int:
        """Withdraw the ``stored_holder`` rule's statements on these handles for anyone but each one's holder."""

        party_model = apps.get_model("parties", "Party")
        source = apps.get_model("provenance", "Rule")._base_manager.filter(key="parties.stored_holder").first()
        if not holders or source is None:
            return 0
        survivors = party_model.canonical_ids({party_id for party_id in holders.values() if party_id is not None})
        stance = party_model.statements.model
        standing = (
            stance._base_manager.filter(
                aspect="handle", asserted_by=str(to_subject_ref(source)), superseded__isnull=True,
            )
            .exclude(status=stance.StatementStatus.WITHDRAWN)
            .values_list("subject_id", "handle_id")
        )
        moved = {
            (subject_id, handle_id)
            for subject_id, handle_id in standing.filter(handle_id__in=sorted(holders))
            if subject_id != survivors.get(holders[handle_id], holders[handle_id])
        }
        if not moved:
            return 0
        kept = [
            {"subject": subject_id, "aspect": "handle", "handle": handle_id}
            for subject_id, handle_id in standing
            if (subject_id, handle_id) not in moved
        ]
        return int(party_model.statements.withdraw_unsupported(source, kept, aspect="handle"))

    def resolve_stale_owners(self, *, limit: int | None = None) -> HolderRepair:
        """Make every holder the projection of its statements again; return what was stated, left and re-resolved.

        Stored holders are stated first (:meth:`state_stored_holders`); the ones it
        leaves (trashed, confirmed, contested) keep their row as it is. A holder
        whose statements no longer decide it (a withdrawn link, a suggestion below
        review confidence) re-resolves, unless it is trashed: resolution ignores a
        trashed party and would clear its address. A run writes at most ``limit``
        holders, stated and re-resolved together, and reports the rest as
        ``remaining`` for the next run, which continues where it stopped.
        Idempotent: a second complete run states and re-resolves nothing.
        """

        repair = self.state_stored_holders(limit=limit)
        budget = None if limit is None else limit - repair.stated
        statements = apps.get_model("parties", "Party").objects.handle_statements()
        decisive = statements.applied().filter(handle_id=OuterRef("pk"), subject_id=OuterRef("party_id"))
        stale = (
            self.filter(party__isnull=False)
            .exclude(party__is_trashed=True, party__merged_into__isnull=True)
            .exclude(Exists(decisive))
            .exclude(pk__in=self.stored_holders().values("pk"))
            .order_by("pk")
        )
        resolved = 0
        for handle in stale.iterator():
            if budget is not None and resolved >= budget:
                break
            handle.resolve()
            resolved += 1
        remaining = repair.remaining
        if budget is not None and resolved >= budget:
            remaining += stale.count()
        return repair._replace(resolved=resolved, remaining=remaining)

    def renormalize_handles(self, *, limit: int | None = None) -> tuple[int, int]:
        """Save again each comparison key the normalization rule no longer gives; return ``(rekeyed, remaining)``.

        ``normalized_value`` is what ``Handle.normalize_value`` gave when a row
        was last saved, so a change of the rule leaves stored keys behind and
        the lookups that read them miss those rows. This repair compares every
        row, of every platform, against the rule as it is now and saves the key
        of each that differs through the handle's own save, which derives it.
        That save names ``normalized_value`` alone: ``value`` is never written,
        ``updated_at`` is stamped as every save of an audited row stamps it, and
        the row's history records the change as any save does. Rows are read in
        key batches within the database's parameter budget, never the table at
        once; each stale row is read again under its lock before it is saved. A
        run saves at most ``limit`` keys and counts the ones it did not reach as
        ``remaining``, which the next run takes. Named system work. Idempotent: a
        second complete run saves nothing.
        """

        rows = self.model._base_manager.order_by("pk").values_list("pk", "platform", "value", "normalized_value")
        size = query_batch_size(rows)
        rekeyed = remaining = 0
        last: Any = None
        with system_context(reason="parties.handle.renormalize"):
            while batch := list((rows if last is None else rows.filter(pk__gt=last))[:size]):
                last = batch[-1][0]
                stale = [pk for pk, platform, value, key in batch if self.model.normalize_value(platform, value) != key]
                reach = len(stale) if limit is None else max(0, min(len(stale), limit - rekeyed))
                remaining += len(stale) - reach
                if not reach:
                    continue
                with transaction.atomic():
                    for handle in self.filter(pk__in=stale[:reach]).order_by("pk").lock_if_supported():
                        if handle.normalized_value != self.model.normalize_value(handle.platform, handle.value):
                            handle.save(update_fields=["normalized_value"])
                            rekeyed += 1
        return rekeyed, remaining

    def fill_avatar(self, report: AvatarReport) -> bool:
        """Set the avatar a source reports on its handle; return whether it changed.

        The handle is the one :meth:`upsert` lands the reported identity on
        (:meth:`find_identity`), so an avatar lands where the source's messages
        did. A report for a handle that does not exist changes nothing, so a report
        never creates a handle or rewrites its identity. ``avatar=None`` clears the
        handle's avatar; bytes storage refuses as an image (empty, not a raster
        image, too large) change nothing.
        """

        with system_context(reason="parties.handle.fill_avatar"):
            handle = self.find_identity(platform=report.platform, value=report.value, external_id=report.external_id)
        if handle is None:
            return False
        avatar = None
        if report.avatar is not None:
            try:
                avatar = apps.get_model("storage", "File").objects.ingest_image(report.avatar)
            except UploadTooLarge, UploadTypeRefused:
                logger.info("A reported %s handle avatar was not an accepted image.", report.platform)
                return False
        return handle.set_avatar(avatar)

class PartyStatementQuerySet[StatementT: PartyStatementMixin](StatementQuerySet[StatementT]):
    """Party handle scopes; serving callers explicitly authorize both endpoints."""

    def handles(self) -> Self:
        return self.current().filter(aspect="handle")

    def members(self) -> Self:
        """One strongest current stance per subject/member, including weak claims."""
        representative = (
            self.model._base_manager
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
            self.model._base_manager
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

    def standing(self) -> Self:
        """Each party's strongest current stance on a handle, where it asserts a value: the claims that still stand.

        One row per party and handle (:meth:`members`). A claim stands whatever
        its confidence: a stance its source withdrew or a merge superseded makes
        nothing stand, and a person's dismissal outranks every claim. A caller
        that asks only who still claims what reads no evidence;
        :meth:`standing_claims` adds the records behind each.
        """

        return self.members().filter(status=self.model.StatementStatus.ASSERTED, snak=self.model.StatementSnak.VALUE)

    def standing_claims(self) -> dict[tuple[Any, Any], frozenset[EvidenceReference]]:
        """Return the handle claims among these statements that still stand, each with the records behind it.

        Keyed by ``(party id, handle id)``, for the claims :meth:`standing`
        answers. The records behind a standing claim are the evidence of each
        current stance that asserts the handle for the party, whichever source
        states it, and of the stances a merge folded under one of them: two
        parties' claims by one source collapse into one when the parties merge,
        and the records that carried the address for either still stand behind
        it. Evidence is parsed here, reference by reference, for the caller to
        compare in Python (``EvidenceReference.named_record`` says which record
        a reference names): a party and a handle share a handful of stances, and
        asking a database whether a JSON list contains an entry is not portable.
        Narrow the statements first, by handle or by party.
        """

        stance = self.model
        claims: dict[tuple[Any, Any], set[EvidenceReference]] = {
            pair: set() for pair in self.standing().order_by().values_list("subject_id", "handle_id")
        }
        if not claims:
            return {}
        asserting = Q(status=stance.StatementStatus.ASSERTED, snak=stance.StatementSnak.VALUE)
        stated = self.handles().filter(asserting).order_by()
        folded = stance._base_manager.filter(
            asserting,
            Exists(
                stated.filter(
                    subject_id=OuterRef("subject_id"),
                    handle_id=OuterRef("handle_id"),
                    asserted_by=OuterRef("asserted_by"),
                )
            ),
            aspect="handle",
            superseded__isnull=False,
            handle_id__in=stated.values("handle_id"),
        )
        for stances in (stated, folded):
            for party_id, handle_id, evidence in stances.values_list("subject_id", "handle_id", "evidence"):
                if (party_id, handle_id) in claims:
                    claims[party_id, handle_id].update(EvidenceReference.model_validate(ref) for ref in evidence)
        return {pair: frozenset(records) for pair, records in claims.items()}

class EmailBasis(StrEnum):
    """How :meth:`PartyManager.people_for_emails` found a person for an email address."""

    ACCOUNT = "account"
    """The user account with that address is the person's: they are its own Person."""
    HELD = "held"
    """The person holds the address: it is their resolved email handle."""
    CLAIM = "claim"
    """The one person a current asserted contact claim proposes the address for; no live person holds it yet."""


class EmailHolder(NamedTuple):
    """One person an email address names, and the basis it names them by.

    ``handle`` is the email handle that ties the address to the person: the one
    they hold, or the one claimed for them; for an account, the one its person
    holds, else ``None``. Only a ``HELD`` holder holds the address; a caller
    states more about a person found otherwise.
    """

    email: str
    """The address as the caller gave it."""
    person: Person
    basis: EmailBasis
    handle: Handle | None


def _email_key(value: str) -> str:
    """Return the normalized email address ``value`` (``Handle.normalize_value``), the key every lookup compares."""

    handle_model = apps.get_model("parties", "Handle")
    return cast(str, handle_model.normalize_value(handle_model.Platform.EMAIL, value))


@dataclass
class EmailLookup:
    """Who email addresses name and which an account signs in with, read once for a page.

    :meth:`PartyManager.lookup_emails` fills it; every key is a normalized
    address. ``holders`` keeps :meth:`PartyManager.people_for_emails`' answers
    for each address read (an empty list when it names nobody), less the claim
    basis for an address a lapsed holder keeps; ``lapsed`` the keys of each
    address's lapsed holders (:meth:`PartyManager.lapsed_holders`); and
    ``sign_in`` the addresses a person account signs in with, in any spelling.
    A record that lands or withdraws an address marks it ``changed``, so the
    next lookup of it reads it again. ``verdicts`` keeps each adoption verdict
    (:meth:`PartyManager.adoption_blocker`) by person and link, so the target
    decision and the ingest that binds the person admit them once.
    """

    holders: dict[str, list[EmailHolder]] = field(default_factory=dict)
    lapsed: dict[str, set[Any]] = field(default_factory=dict)
    sign_in: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    verdicts: dict[tuple[Any, Any], str | None] = field(default_factory=dict)

    def named(self, email: str) -> list[EmailHolder]:
        """Return the people ``email`` names, on every basis, as read."""

        return self.holders.get(_email_key(email), [])

    def signs_in(self, emails: Iterable[str]) -> bool:
        """Whether any of ``emails`` is an address a person account signs in with."""

        return any(_email_key(email) in self.sign_in for email in emails)


@dataclass(frozen=True, slots=True)
class SameAsPair:
    """One deterministic pair with its normalized identity and readable evidence rows."""

    left: Any
    right: Any
    normalized_value: str
    handles: tuple[Any, ...]
    id: str = ""
    """The pair's public id: its place in the review list, where the next page continues (``after``)."""


@dataclass(frozen=True, slots=True)
class CertainSameAsPair:
    """A pair awaiting review that one confirm would merge now (:meth:`PartyManager.certain_same_as_pairs`)."""

    left: Any
    right: Any
    normalized_value: str
    survivor: Any
    """The older record, which the merge keeps."""
    id: str = ""
    """The pair's public id, where the next batch continues (``after``)."""


class DuplicateMergeOutcome(StrEnum):
    """What :meth:`PartyManager.merge_duplicates` did with one confirmed set of records."""

    MERGED = "merged"
    REFUSED = "refused"
    """A guard or the merge refused the set as confirmed; ``reason`` says why and nothing merged."""
    NOT_CERTAIN = "not_certain"
    """A set confirmed as a certain pair is no longer one; nothing merged."""
    STOPPED = "stopped"
    """The database stopped the merge for a concurrent write (a deadlock, a lock timeout); nothing merged."""
    FAILED = "failed"
    """Any other database failure (a statement timeout, a lost connection) rolled the set back; nothing merged."""


@dataclass(frozen=True, slots=True)
class MergeFieldValues:
    """A merge field whose values differ across a set's members, and what the merged record will show.

    A preview files it as a disagreement (two different stated values, a choice
    for the person) or as a fill (one value stated, the rest empty, which the
    merge gives the survivor), :class:`DuplicateSetPreview`.
    """

    field: str
    values: tuple[str | None, ...]
    """Each member's value as text, in the set's member order."""
    shown: str | None
    """The value the survivor shows after the merge: the provenance ranking's winner among every member's statement
    of the field, or, when no member states it, the survivor's value, else the first member's that has one."""


@dataclass(frozen=True, slots=True)
class DuplicateSetPreview:
    """One set of records a person says are one, as :meth:`PartyManager.merge_duplicates` would merge it now."""

    members: tuple[Any, ...]
    """The readable members, oldest first: the first survives and the others fold into it in key order."""
    outcome: DuplicateMergeOutcome | None
    """``None`` when the set merges as listed; else why it would not."""
    reason: str = ""
    disagreements: tuple[MergeFieldValues, ...] = ()
    """The merge fields the members state at least two different values for, only for a set that
    merges: what a person must look at. A field the actor's ``read__<field>`` gate hides on any
    member is left out, here and in :attr:`fills`."""
    fills: tuple[MergeFieldValues, ...] = ()
    """The merge fields exactly one value is stated for, the rest empty, only for a set that merges:
    nothing to choose, the merge gives the survivor that value (``Party.merge_values``)."""
    digest: str = ""
    """For a set that merges, a keyed digest of everything the dialog shows of it, the fills
    included, which the merge compares under its lock (:meth:`PartyManager.merge_duplicates`)."""

    @property
    def survivor(self) -> Any:
        return self.members[0] if self.members and self.outcome is None else None


@dataclass(frozen=True, slots=True)
class DuplicateSetOutcome:
    """What one confirmed set came to: merged into ``survivor``, or left, and why."""

    members: tuple[str, ...]
    """The member ids as the person confirmed them."""
    outcome: DuplicateMergeOutcome
    survivor: Any = None
    reason: str = ""


class SamePersonLink(NamedTuple):
    """One party's link to an address (:meth:`PartyManager.same_person_links`)."""

    party: Any
    handle: Any
    source: Any
    """The source the link is attributed to: its asserting principal.

    A holder written onto its row, and a link one of parties' ``LINKING_RULES``
    states on a record's behalf (any source's claim, a holder found on its row),
    name no single source: each such link is a source of its own.
    """


def _age(party: Any) -> tuple[Any, Any]:
    """A party's place in a merge: the oldest record survives, then key order."""

    return party.created_at, party.pk


_LOCK_CONFLICT_SQLSTATES = frozenset({"40P01", "55P03"})
"""PostgreSQL's deadlock_detected and lock_not_available: a concurrent write held a row this transaction needed."""

_LOCK_CONFLICT_SQLITE_CODES = frozenset({5, 6})
"""SQLite's SQLITE_BUSY and SQLITE_LOCKED primary result codes: its lock timeout (it has no deadlock)."""


def _lock_conflict(error: Exception) -> bool:
    """Whether the driver error behind a Django database error is a lock conflict worth retrying."""

    cause = error.__cause__
    if getattr(cause, "sqlstate", None) in _LOCK_CONFLICT_SQLSTATES:
        return True
    code = getattr(cause, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in _LOCK_CONFLICT_SQLITE_CODES


def _refusal(error: Exception) -> str:
    """The person-facing message of a refused merge."""

    messages = getattr(error, "messages", None)
    return "; ".join(str(message) for message in messages) if messages else str(error)


class PartyQuerySet[PartyT: Party](MergeableQuerySet[PartyT], StatementSubjectQuerySet[PartyT]):
    """Party read scopes: canonical (unmerged) rows and organisation membership."""

    def with_display_avatars(self, actor: Any) -> Self:
        """Batch :meth:`PartyManager.display_avatar` for these parties, through ``actor``'s readable files.

        For a read owner that hands parties to a page outside the GraphQL
        optimizer (a navigator row, a same_as pair), so no party reads its
        avatar on its own.
        """

        avatars = apps.get_model("storage", "File").objects.with_actor(actor)
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
        actor = self.actor() or current_actor()
        organizations = reader_queryset(apps.get_model("parties", "Organization"), actor).filter(
            legal_name__iregex=pattern,
        )
        legal_name_ids = organizations.scoped_for_aggregate().values("pk")
        return candidates.filter(
            Q(display_name__iregex=pattern) | Q(pk__in=Subquery(legal_name_ids)),
        )

    def _readable_circles(self) -> Any:
        """Circles read as this queryset's actor, else the ambient one."""

        return reader_queryset(apps.get_model("parties", "Circle"), self.actor())

    def circle_members_prefetch(self) -> Prefetch:
        """Hint the circle memberships this read's actor may see, by circle name.

        A scoped prefetch is portable across the supported database floor and,
        unlike a reverse-join aggregate, independently applies both the membership
        and circle row policies before projecting names.
        """

        visible_memberships = (
            self._readable_circles().memberships().select_related("circle").order_by("circle__name", "sqid")
        )
        return Prefetch("circle_members", queryset=visible_memberships, to_attr=VISIBLE_CIRCLE_MEMBERS)

    def with_circle_names(self) -> Self:
        """Prefetch actor-visible circle names for generic chip rendering."""

        return self.prefetch_related(self.circle_members_prefetch())

    def unassigned(self) -> Self:
        """Return canonical parties with no actor-visible circle membership."""

        visible_membership = self._readable_circles().memberships().filter(party_id=OuterRef("pk"))
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
            .for_reader(self.actor() or current_actor())
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

        visible_subtree_membership = (
            self._readable_circles().subtree_of(circle)
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

    def organization_memberships(self) -> Any:
        """Readable current organisation relationships anchored at these parties."""

        actor = self.actor() or current_actor()
        organizations = reader_queryset(apps.get_model("parties", "Organization"), actor)
        relationships = reader_queryset(apps.get_model("parties", "Relationship").objects.all(), actor).canonical()
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
        people = person_model._base_manager.filter(user_id=user_id).values("pk")
        return self.filter(pk__in=Subquery(people)).values_list("pk", flat=True).first()

    def account_for_party(self, party_id: Any) -> Any | None:
        """Return the user account for a selected Person party, when one exists."""

        if self.filter(pk=party_id).values_list("pk", flat=True).first() is None:
            return None
        person_model = apps.get_model("parties", "Person")
        person = person_model._base_manager.select_related("user").filter(pk=party_id).first()
        return person.user if person is not None and person.user_id is not None else None

    def same_as_pairs(
        self, *, limit: int | None = 50, parties: Collection[Any] | None = None, after: str | None = None,
    ) -> list[SameAsPair]:
        """The resolved value pairs the reader may review, in key order: at most ``limit`` after ``after``.

        A pair is the reader's to review when they read both endpoints and every
        handle its evidence cites. Which pairs those are is decided for every pair
        at once, in a fixed number of queries whatever their number, and only the
        page's pairs are then read in full. The order is the pair rows' own key, so
        ``after`` (a pair's ``id``, the last of the previous page) continues past it
        however many pairs left the list meanwhile, a merged or kept-apart pair
        included: no page skips or repeats a pair. A caller's page is at most 101;
        ``limit=None`` reads every pair, for the owners that judge them all.
        ``parties`` narrows the read to pairs whose both endpoints are among those keys.
        """

        if limit is not None:
            limit = max(0, min(int(limit), 101))
            if not limit:
                return []
        pair_model = self.model.same_as.model
        start = None
        if after is not None:
            start = cast(SqidField, pair_model._meta.get_field("sqid")).public_id_to_value(after)
            if start is None:
                raise ValidationError({"after": "No such pair."})
        reviewable = [entry for entry in self._reviewable_pairs(parties) if start is None or entry[0] > start]
        page = reviewable if limit is None else reviewable[:limit]
        if not page:
            return []
        actor, unscoped = self.effective_actor(strict=True)
        endpoints = self.canonical() if unscoped else self.canonical().with_display_avatars(actor)
        rows = pair_model._base_manager.prefetch_related(
            *(Prefetch(side, queryset=endpoints) for side in ("left", "right")),
        ).in_bulk([pk for pk, _cited in page])
        return [
            SameAsPair(
                rows[pk].left, rows[pk].right, cited[0].normalized_value if cited else "", cited,
                id=pair_model.public_id_from_pk(pk),
            )
            for pk, cited in page
            if pk in rows
        ]

    def _reviewable_pairs(self, parties: Collection[Any] | None) -> list[tuple[Any, tuple[Any, ...]]]:
        """Every pair the reader may review, in key order, with the handles its evidence cites (:meth:`same_as_pairs`).

        Three reads whatever the number of pairs: the first applied statement of each
        pair whose endpoints the reader reads, and every handle those statements
        cite, through the reader. A pair citing a handle the reader may not read is
        left out.
        """

        actor, unscoped = self.effective_actor(strict=True)
        pair_model = self.model.same_as.model
        visible = self.canonical().scoped_for_aggregate().values("pk")
        statements = pair_model.statements.applied()
        statements = statements.sudo(reason="parties.same_as_pairs") if unscoped else statements.with_actor(actor)
        pairs = (
            pair_model._base_manager.filter(left_id__in=Subquery(visible), right_id__in=Subquery(visible))
            .untrashed()
            .filter(Q() if parties is None else Q(left_id__in=list(parties), right_id__in=list(parties)))
        )
        cited: dict[Any, list[EvidenceReference]] = {}
        for subject_id, evidence in (
            statements.filter(subject_id__in=Subquery(pairs.values("pk")))
            .order_by("subject_id", "pk")
            .values_list("subject_id", "evidence")
        ):
            cited.setdefault(subject_id, [
                ref for value in evidence
                if (ref := EvidenceReference.model_validate(value)).model.lower() == "parties.handle"
            ])
        handle_model = apps.get_model("parties", "Handle")
        handles = (
            handle_model.objects.sudo(reason="parties.same_as_pairs")
            if unscoped
            else handle_model.objects.with_actor(actor)
        )
        readable = instances_from_public_ids(
            handle_model, {ref.id for refs in cited.values() for ref in refs}, queryset=handles,
        )
        return [
            (pk, tuple(readable[ref.id] for ref in refs))
            for pk, refs in sorted(cited.items(), key=lambda item: item[0])
            if all(ref.id in readable for ref in refs)
        ]


_PartyManagerBase = AngeeHistoryManager.from_queryset(PartyQuerySet)


class PartyManager[PartyT: Party](_PartyManagerBase[PartyT]):
    """Factory for parties, including the idempotent directory-sync ingest.

    Also the effective manager of the MTI children (``Person`` / ``Organization``
    inherit the parent's concrete default manager), so the Person-per-user factory
    :meth:`for_user` lives here.
    """

    def display_avatar(self, party: Any, *, actor: Any) -> Any | None:
        """Return the avatar ``party`` shows ``actor``: its own, else its strongest handle's.

        Its own avatar counts when ``actor`` can read it. Otherwise it is the avatar
        of a handle resolution gave to this party (``Handle.party``, so only the
        party that won a handle shows its avatar), ranked by what that resolution
        materialised on the handle: a human-confirmed winner
        (``party_link_confirmed``) first, then the oldest handle. Statement
        confidence does not rank, so among unconfirmed handles an older inferred one
        shows before a newer one a card asserts. Only the handles ``actor`` can read
        count, which is what a party shared with its handles grants, and only avatars
        it can read: storage's image drive, which every signed-in user reads, holds
        every avatar stored since that drive existed. Derived for display only: never
        stored, and never part of the directory projection, so no address book
        receives it. A page of parties batches the same reads with
        :meth:`display_avatar_prefetches`.
        """

        if hasattr(party, _DISPLAY_OWN_AVATAR):
            own, handles = getattr(party, _DISPLAY_OWN_AVATAR), getattr(party, _DISPLAY_AVATAR_HANDLES)
        else:
            avatars = apps.get_model("storage", "File").objects.with_actor(actor)
            own = avatars.filter(pk=party.avatar_id).first() if party.avatar_id is not None else None
            handles = self._display_avatar_handles(actor, avatars).filter(party_id=party.pk)[:1]
        if own is not None:
            return own
        handle = next(iter(handles), None)
        return handle.avatar if handle is not None else None

    def display_avatar_prefetches(self, actor: Any, *, avatars: Any) -> DisplayAvatarPrefetches:
        """Return the prefetches :meth:`display_avatar` reads for a page of parties.

        ``avatars`` is ``actor``'s readable File queryset every avatar loads through;
        a GraphQL page passes it optimized for its selection.
        """

        return DisplayAvatarPrefetches(
            own=Prefetch("avatar", queryset=avatars, to_attr=_DISPLAY_OWN_AVATAR),
            handles=Prefetch(
                "handles", queryset=self._display_avatar_handles(actor, avatars), to_attr=_DISPLAY_AVATAR_HANDLES
            ),
        )

    @staticmethod
    def _display_avatar_handles(actor: Any, avatars: Any) -> Any:
        """Handles ``actor`` reads that carry an avatar in ``avatars``, strongest first, avatar loaded."""

        return (
            apps.get_model("parties", "Handle").objects.with_actor(actor)
            .filter(avatar__in=avatars.values("pk"))
            .order_by("-party_link_confirmed", "pk")
            .prefetch_related(Prefetch("avatar", queryset=avatars))
        )

    def handle_statements(self) -> Any:
        """Return the unscoped handle-statement owner for internal composition."""

        return apps.get_model("parties", "Party").statements.handles()

    LINKING_SOURCE_KINDS: ClassVar[tuple[SourceKind, ...]] = (
        SourceKind.PERSON,
        SourceKind.SYSTEM_OF_RECORD,
        SourceKind.DIRECTORY,
        SourceKind.IMPORT,
    )
    """Kinds of source whose handle statement links a party to an address: each states its own record."""

    LINKING_RULES: ClassVar[tuple[str, ...]] = ("source_sender", "stored_holder")
    """Parties' rules, by :meth:`rule_source` key, whose handle statement links a party to an address.

    A source's claim (:meth:`propose_claimed_handle_as_system`) and a holder found
    on its row (:meth:`HandleManager.state_stored_holders`) state a record's own
    address. Every other rule or extraction, parties' suggesters included, infers
    a link from other evidence and links nobody until it is named here
    (:meth:`same_person_links`).
    """

    SHARED_ADDRESS_LIMIT: ClassVar[int] = 4
    """The most parties one address links; an address more are linked to is shared and pairs nobody."""

    @staticmethod
    def rule_source(key: str) -> Any:
        with system_context(reason="parties.rule"):
            return apps.get_model("provenance", "Rule").objects.get(key=f"parties.{key}")

    @staticmethod
    def shared_sender_ids() -> frozenset[Any]:
        """The handles ``ANGEE_PARTIES_SHARED_SENDER_PROVIDERS`` name: addresses several people speak through.

        Each provider is a dotted callable a downstream addon contributes, returning
        the handle ids of list and notification senders; parties reads no other
        addon's models. A provider that raises does so before its caller writes.
        Read as named system work: complete evidence spans every owner's mail.
        """

        with system_context(reason="parties.shared_sender_ids"):
            return frozenset(
                chain.from_iterable(provider() for provider in resolve_hooks("ANGEE_PARTIES_SHARED_SENDER_PROVIDERS"))
            )

    @classmethod
    def linking_rule_refs(cls) -> list[str]:
        """The asserting references of :attr:`LINKING_RULES`, in one read."""

        rules = apps.get_model("provenance", "Rule")._base_manager.filter(
            key__in=[f"parties.{key}" for key in cls.LINKING_RULES]
        )
        return [str(to_subject_ref(rule)) for rule in rules.order_by("pk")]

    def same_person_links(
        self,
        *,
        addresses: Collection[tuple[str, str]] | None = None,
        shared: Collection[Any] = (),
        every_address: bool = False,
    ) -> dict[tuple[str, str], list[SamePersonLink]]:
        """Each address two to :attr:`SHARED_ADDRESS_LIMIT` live parties are linked to, with its links.

        With ``every_address`` (and ``addresses``), every requested address any
        live party is linked to, whatever their number: who is linked to an
        address, for a source deciding whether it may only claim it
        (:meth:`_contested_addresses`); a shared one is still no pair.

        A link is a holding or a standing statement (asserted, current, a value, not
        dismissed by a person), whatever its confidence, by a source that states its
        own record: :attr:`LINKING_SOURCE_KINDS` or :attr:`LINKING_RULES`. A holding
        is the applied statement that decides a handle's holder, whatever its
        source, or a holder written onto its row with no statement behind it
        (:meth:`HandleQuerySet.stored_holders`); a holder whose statements no longer
        decide it is stale and links nothing. Only a handle that names one account
        links (:meth:`~angee.parties.models.Handle.identifies_account`), and a
        contested control claim (an account's person claiming an address another
        account controls, :meth:`HandleManager.claim_control`) does not. Live means
        neither trashed nor merged away.

        An address more parties are linked to is shared (a switchboard, a family
        line) and left out, as is one that a ``shared`` handle has (a sender the
        shared-sender providers name, :meth:`shared_sender_ids`). The candidate
        addresses are found in SQL first, grouped by platform and normalized value,
        and only their links are read. ``addresses`` narrows the read to those.
        Each link names the source it is attributed to (:class:`SamePersonLink`).
        """

        handle_model = apps.get_model("parties", "Handle")
        person_model = apps.get_model("parties", "Person")
        rule_refs = self.linking_rule_refs()
        statements = self.handle_statements()
        stance = statements.model
        dismissed = statements.filter(
            subject_id=OuterRef("subject_id"),
            handle_id=OuterRef("handle_id"),
            status=stance.StatementStatus.ASSERTED,
            rank=stance.StatementRank.PREFERRED,
            snak=stance.StatementSnak.NO_VALUE,
        )
        contested = Q(handle__controller__isnull=False) & Exists(
            person_model._base_manager.filter(pk=OuterRef("subject_id"), user__isnull=False).exclude(
                user_id=OuterRef("handle__controller_id")
            )
        )
        stated = Q(source_kind__in=self.LINKING_SOURCE_KINDS) | Q(asserted_by__in=rule_refs)
        links = (
            statements.filter(
                handle_model.identifies_account_filter("handle__"),
                status=stance.StatementStatus.ASSERTED,
                snak=stance.StatementSnak.VALUE,
                handle__merged_into__isnull=True,
                subject__is_trashed=False,
                subject__merged_into__isnull=True,
            )
            .exclude(handle__normalized_value="")
            .filter(Q(applied=True, subject_id=F("handle__party_id")) | (stated & ~contested & ~Exists(dismissed)))
            .order_by()
        )
        bare = (
            handle_model.objects.system_context(reason="parties.same_person_links")
            .stored_holders()
            .filter(handle_model.identifies_account_filter(), party__is_trashed=False, party__merged_into__isnull=True)
            .exclude(normalized_value="")
            .order_by()
        )
        if addresses is not None:
            wanted = set(addresses)
            values = sorted({value for _platform, value in wanted})
            links = links.filter(handle__normalized_value__in=values)
            bare = bare.filter(normalized_value__in=values)
        candidates = set(
            links.values("handle__platform", "handle__normalized_value")
            .annotate(parties=Count("subject_id", distinct=True))
            .filter(**({} if every_address else {"parties__gte": 2, "parties__lte": self.SHARED_ADDRESS_LIMIT}))
            .values_list("handle__platform", "handle__normalized_value")
        )
        held = list(bare.values_list("platform", "normalized_value", "party_id", "pk"))
        candidates.update((platform, value) for platform, value, _party, _handle in held)
        if addresses is not None:
            candidates &= wanted
        found: defaultdict[tuple[str, str], list[SamePersonLink]] = defaultdict(list)
        for platform, value, party_id, handle_id in held:
            if (platform, value) in candidates:
                found[platform, value].append(SamePersonLink(party_id, handle_id, ("holder", party_id, handle_id)))
        values = sorted({value for _platform, value in candidates})
        columns = ("handle__platform", "handle__normalized_value", "subject_id", "handle_id", "asserted_by")
        for batch in batched(values, query_batch_size(links)):
            for platform, value, party_id, handle_id, source in links.filter(
                handle__normalized_value__in=batch
            ).values_list(*columns):
                if (platform, value) in candidates:
                    key = (source, party_id, handle_id) if source in rule_refs else source
                    found[platform, value].append(SamePersonLink(party_id, handle_id, key))
        if shared:
            shared_ids = set(shared)
            rows = handle_model._base_manager.all()
            for batch in batched(values, query_batch_size(rows)):
                for pk, platform, value in rows.filter(normalized_value__in=batch).values_list(
                    "pk", "platform", "normalized_value"
                ):
                    if pk in shared_ids:
                        found.pop((platform, value), None)
        return {
            address: address_links
            for address, address_links in sorted(found.items())
            if every_address or 2 <= len({link.party for link in address_links}) <= self.SHARED_ADDRESS_LIMIT
        }

    @staticmethod
    def party_kinds(party_ids: Iterable[Any]) -> tuple[dict[Any, Any], frozenset[Any]]:
        """Read which parties are people, with each person's account, and which are organizations.

        Returns ``({person id: user id or None}, organization ids)``; a party in
        neither is of no concrete kind.
        """

        ids = sorted(set(party_ids))
        people: dict[Any, Any] = {}
        organizations: set[Any] = set()
        for batch in by_key(apps.get_model("parties", "Person")._base_manager.all(), ids):
            people.update(batch.values_list("pk", "user_id"))
        for rows in by_key(apps.get_model("parties", "Organization")._base_manager.all(), ids):
            organizations.update(rows.values_list("pk", flat=True))
        return people, frozenset(organizations)

    def same_person_pairs(
        self, links: Mapping[tuple[str, str], Sequence[SamePersonLink]]
    ) -> dict[tuple[Any, Any], tuple[Any, ...]]:
        """Every two parties these links (:meth:`same_person_links`) make a same-person pair, with their handle ids.

        Two parties linked to one address are a pair when they are of one kind (a
        person is never paired with an organization), are not both an account's own
        person, and are linked by two sources between them: two parties whose only
        links to the address one source states are that source's distinct records
        (two cards of one address book sharing a number). Pairs come ordered, each
        citing the handles that link the two at every address that pairs them.
        """

        people, organizations = self.party_kinds(link.party for address in links.values() for link in address)

        def kind(party_id: Any) -> str:
            return "person" if party_id in people else "organization" if party_id in organizations else ""

        pairs: dict[tuple[Any, Any], dict[Any, None]] = {}
        for address in links.values():
            handles: defaultdict[Any, dict[Any, None]] = defaultdict(dict)
            sources: defaultdict[Any, set[Any]] = defaultdict(set)
            for link in address:
                handles[link.party][link.handle] = None
                sources[link.party].add(link.source)
            for left_id, right_id in combinations(sorted(handles), 2):
                if (
                    kind(left_id) != kind(right_id)
                    or (people.get(left_id) and people.get(right_id))
                    or len(sources[left_id] | sources[right_id]) < 2
                ):
                    continue
                pairs.setdefault((left_id, right_id), {}).update(dict.fromkeys((*handles[left_id], *handles[right_id])))
        return {pair: tuple(handle_ids) for pair, handle_ids in sorted(pairs.items())}

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
        with transaction.atomic():
            source = to_subject_ref(asserted_by)
            subject = apps.get_model("parties", "Party")._base_manager.get(pk=party.pk)
            refs = [EvidenceReference.of(row) for row in evidence]
            existing = (
                subject.statements.current().filter(aspect="handle", handle=handle, asserted_by=str(source)).first()
            )
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
            for batch in by_key(handle_model.objects.all(), sorted(set(handle_ids))):
                handles.update((row.pk, row) for row in batch.order_by("pk").lock_if_supported())
            ids = set(party_ids) | {row.party_id for row in handles.values() if row.party_id is not None}
            statements = self.handle_statements()
            for statement_handles in by_key(handle_model._base_manager.all(), sorted(handles)):
                ids.update(
                    statements.filter(handle_id__in=statement_handles.values("pk")).values_list("subject_id", flat=True)
                )
            parties: dict[int, Any] = {}
            for party_batch in by_key(party_model.objects.all(), sorted(ids)):
                parties.update((row.pk, row) for row in party_batch.order_by("pk").lock_if_supported())
            for statement_handles in by_key(handle_model._base_manager.all(), sorted(handles)):
                keys = list(
                    statements.filter(handle_id__in=statement_handles.values("pk")).values_list("pk", flat=True)
                )
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
                .filter(platform=normalized_platform, value=contact_value)
                .first()
            )
            if handle is None:
                if not candidate.with_actor(actor).has_access("create"):
                    raise PermissionDenied("Denied: cannot create this contact point.")
                with system_context(reason="parties.statement.propose_manual_contact.handle"):
                    handle = handle_owner.upsert(
                        platform=normalized_platform,
                        value=contact_value,
                        created_by_id=actor_user_id(actor),
                    )
            handle = handle.canonical()
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            if handle.merged_into_id is not None:
                raise ValidationError("This contact point was merged concurrently; try again.")
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

    def propose_claimed_handle_as_system(
        self,
        party: Any,
        handle: Any,
        *,
        evidence: Any,
        reason: str,
        confidence: float = 0.4,
    ) -> Any:
        """Retain an unconfirmed address claim for human identity review, under the caller's named reason.

        The evidence record may be a Message, document, or another readable native
        record. This owner deliberately records only that the source claimed the
        handle. Transport authentication remains unknown, independently of any
        later human confirmation of Party ownership. The calling verb's own gate
        authorized the claim (an access decision admitting its requester), so no
        actor is checked, neither an explicit, pinned nor ambient one.
        """

        if not reason.strip():
            raise ValueError("A system claim proposal requires a named reason.")
        if not 0 < confidence < self.model.REVIEW_CONFIDENCE:
            raise ValidationError(
                {"confidence": f"Claimed-handle proposals require confidence below {self.model.REVIEW_CONFIDENCE}."}
            )
        evidence_ref = EvidenceReference.of(evidence).model_dump(mode="json")
        with system_context(reason=reason), transaction.atomic():
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

    def claimant_as_system(
        self,
        *,
        email: str = "",
        phone: str = "",
        display_name: str = "",
        given_name: str = "",
        family_name: str = "",
        evidence: Any,
        own: models.QuerySet[Any],
        owner_id: Any = None,
        reason: str,
    ) -> tuple[Any, Any | None, Any | None]:
        """Return the person a source makes of a contact its record carried, with their email and phone handles.

        Each source creates its own people. ``evidence`` is the caller's record
        that carried the contact. Which records are the caller's own is the
        caller's rule, and ``own`` carries it: a queryset of them, the record
        links of its integration. The email decides, and a contact without one
        is decided by its number. The person found again is one whose standing
        claim on a handle of that address has a row of ``own`` behind it
        (:meth:`PartyStatementQuerySet.standing_claims`), never one a column
        names: another spelling of the address is the same address, a trashed
        person is nobody's answer, a merged one is found as its survivor
        (``Party.canonical_ids``), and of several the earliest. Nobody else's
        person is taken, whoever holds or claims the address, an account's own
        person included; otherwise a new ``Person`` is made, named as given
        (``Party.PLACEHOLDER_NAME`` without a name) and created by ``owner_id``.
        A contact with neither finds nobody again. An email that cannot be an
        address at all (``Handle.is_email_address``) is no address, and a number
        the handle owner reads nothing in, or refuses, is no number: each is
        left out, so two contacts that gave the same non-address are never one
        person for it.

        Nothing is stated in full. Each returned handle the person does not hold
        is recorded as a claim for review with ``evidence`` behind it
        (:meth:`propose_claimed_handle_as_system`): it decides nothing and moves
        nothing away from whoever holds the address. A claim gains one evidence
        reference per record that carried the address, deduplicated, which is how
        the next record finds the person. A person found again has nothing new
        stated of them: they carry a handle only when they hold it or a row of
        ``own`` stands behind their claim on it, since a number given beside a
        known address may be someone else's. A new person's number is known or
        made.

        The rows of the address and of the number, the parties they name and
        their statements are locked in one :meth:`lock_identity_rows` call before
        any claim is read, so two callers that meet a new address decide one
        after the other and no claim read here changes before it is written. The
        whole verb is named system work under ``reason``, in one transaction: the
        caller's own gate admitted its record, so no actor is checked.

        It proposes no same-person pair, and no caller needs to: a claim links
        its person to the address (:attr:`LINKING_RULES`), so the scheduled
        pairing pass (:meth:`reconcile_same_as` over :meth:`same_person_links`)
        proposes the claimant and whoever else is linked to it for a person's
        review.

        Returns ``(person, email handle, phone handle)``; a handle is ``None``
        where the contact has none or the person may not carry it.
        """

        if not reason.strip():
            raise ValueError("A system claimant requires a named reason.")
        if not isinstance(evidence, own.model):
            raise TypeError("A claimant's evidence is a record of the caller's own kind.")
        handle_model = apps.get_model("parties", "Handle")
        person_model = apps.get_model("parties", "Person")
        platforms = handle_model.Platform
        name = " ".join(display_name.split()) or " ".join(f"{given_name} {family_name}".split())
        address = email.strip() if handle_model.is_email_address(email) else ""
        number = phone.strip() if handle_model.normalize_value(platforms.PHONE, phone) else ""
        with system_context(reason=reason), transaction.atomic():
            emails = self._address_handles(platforms.EMAIL, address)
            if address and not emails:
                emails = [
                    handle_model.objects.upsert(
                        platform=platforms.EMAIL, value=address, display_name=name, created_by_id=owner_id
                    )
                ]
            numbers = self._address_handles(platforms.PHONE, number)
            locked, _parties = self.lock_identity_rows(
                party_ids=(), handle_ids=[row.pk for row in (*emails, *numbers)]
            )
            emails, numbers = [locked[row.pk] for row in emails], [locked[row.pk] for row in numbers]
            stated = self.handle_statements().filter(handle__in=[*emails, *numbers]).standing_claims()
            survivors = apps.get_model("parties", "Party").canonical_ids({party_id for party_id, _handle in stated})
            claims: defaultdict[tuple[Any, Any], frozenset[EvidenceReference]] = defaultdict(frozenset)
            for (party_id, handle_id), records in stated.items():
                if party_id in survivors:
                    claims[survivors[party_id], handle_id] |= records

            # One read of ``own`` says which of these claims are the caller's, however many parties claim the rows.
            vouched = self._vouched(claims, own)

            def carried(person_id: Any, rows: Sequence[Any]) -> Any | None:
                held = next((row for row in rows if row.party_id == person_id), None)
                return held or next((row for row in rows if (person_id, row.pk) in vouched), None)

            deciding = {row.pk for row in (emails if address else numbers)}
            claimants = {party_id for party_id, handle_id in vouched if handle_id in deciding}
            person = person_model._base_manager.filter(pk__in=claimants, is_trashed=False).order_by("pk").first()
            if person is not None:
                email_handle, phone_handle = carried(person.pk, emails), carried(person.pk, numbers)
            else:
                person = person_model.objects.create(
                    display_name=name or person_model.PLACEHOLDER_NAME,
                    given_name=given_name.strip(),
                    family_name=family_name.strip(),
                    created_by_id=owner_id,
                )
                email_handle = emails[0] if emails else None
                phone_handle = numbers[0] if numbers else None
                if phone_handle is None and number:
                    try:
                        phone_handle = handle_model.objects.upsert(
                            platform=platforms.PHONE, value=number, created_by_id=owner_id
                        )
                    except ValidationError:
                        phone_handle = None
            for handle in (email_handle, phone_handle):
                if handle is not None and handle.party_id != person.pk:
                    # The record that carried the address stands behind the claim: the first one, and each after it.
                    self.propose_claimed_handle_as_system(person, handle, evidence=evidence, reason=reason)
            return person, email_handle, phone_handle

    @staticmethod
    def _address_handles(platform: str, value: str) -> list[Any]:
        """Return the handle rows of ``value`` on ``platform``, every spelling of it, oldest first.

        A row merged away is left out: its survivor is the address's row, and a
        claim stated on a redirect would link nobody.
        """

        handle_model = apps.get_model("parties", "Handle")
        if not value:
            return []
        return list(
            handle_model._base_manager.filter(
                platform=platform,
                normalized_value=handle_model.normalize_value(platform, value),
                merged_into__isnull=True,
            ).order_by("pk")
        )

    @staticmethod
    def _vouched(
        claims: Mapping[tuple[Any, Any], Collection[EvidenceReference]], own: models.QuerySet[Any]
    ) -> set[tuple[Any, Any]]:
        """Return the claims a row of ``own`` stands behind, from one read of ``own`` by key for all of them.

        A reference names a record by the model its generic pointers resolve to
        and its public id (``EvidenceReference.named_record``, the one comparison
        of which record a reference names), and the id decodes to the row's key
        through the model's public id field. Only the keys the references name
        are asked for, in batches within the database's parameter budget
        (``by_key``), and only keys are read: ``own`` is never read as a whole,
        and nothing is read when no reference is of its model.
        """

        model = generic_pointer_model(own.model)
        field = public_data_id_field(model)
        named: dict[tuple[Any, Any], set[Any]] = {}
        for claim, references in claims.items():
            for reference in references:
                label, public_id = reference.named_record
                if label != model._meta.label_lower:
                    continue
                key = field.public_id_to_value(public_id) if field else model._meta.pk.to_python(public_id)
                if key is not None:
                    named.setdefault(claim, set()).add(key)
        ours: set[Any] = set()
        for batch in by_key(own.order_by(), sorted(set().union(*named.values()))):
            ours.update(batch.values_list("pk", flat=True))
        return {claim for claim, keys in named.items() if keys & ours}

    def suggest_for(self, handle: Any) -> Any:
        """Propose a party for a freshly-seen, unresolved ``handle`` (the EMAIL_MATCH producer).

        Three branches, none confirming its link and none duplicating an existing
        pair: indexed normalized twins contribute distinct candidate parties (the
        first at ``1.0``, which decides the handle's party, competing parties at ``0.3`` for
        review); otherwise an email whose non-generic domain matches a tracked
        :attr:`Organization.domain` contributes a rule suggestion at ``0.4``,
        both domains read in the one spelling ``Handle.normalize_email_domain``
        gives, so a punycode and a Unicode spelling match;
        otherwise no-op. Both branches stay inside the partition of the handle's
        creator: handles that user created and parties that user owns. Public
        mailbox-provider domains never imply organization membership. Returns the
        strongest created/existing link, or ``None``.
        """

        if handle.party_id is not None:
            return None
        if handle.created_by_id is None:
            return None
        handle_model = apps.get_model("parties", "Handle")
        twins = (
            handle_model.objects.canonical().filter(
                platform=handle.platform,
                normalized_value=handle.normalized_value,
                party__isnull=False,
                created_by_id=handle.created_by_id,
                party__owner_id=handle.created_by_id,
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
            domain = handle_model.normalize_email_domain(handle.normalized_value.rsplit("@", 1)[1])
            organization_model = apps.get_model("parties", "Organization")
            org = None
            if domain and domain not in GENERIC_EMAIL_DOMAINS:
                # An organization's domain is stored as typed, in either spelling: read both, compare by the one rule.
                spelled = Q(domain__iexact=domain)
                if not domain.isascii():
                    try:
                        spelled |= Q(domain__iexact=punycode(domain))
                    except UnicodeError:
                        pass
                org = next(
                    (
                        row
                        for row in organization_model.objects.filter(spelled, owner_id=handle.created_by_id)
                        if handle_model.normalize_email_domain(row.domain) == domain
                    ),
                    None,
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

    def reconcile_same_as(self, *, limit: int = 1000, shared: Collection[Any] | None = None) -> int:
        """Propose every same-person pair as one person; withdraw what no link supports any more.

        Pairs are :meth:`same_person_pairs` over :meth:`same_person_links`, which
        leave out every address a ``shared`` handle has (by default the ones the
        shared-sender providers name, :meth:`shared_sender_ids`). Each pair is
        stated once by the ``parties.same_as`` rule, citing the handles that link
        it; a pair holding any current assertion or a person's keep-separate
        decision is skipped before the cap, so retained statements advance
        discovery without continuation state. The rule's statements no pair
        supports any more are withdrawn exactly, except one stated after this run
        began (an importer's proposal committed meanwhile), which the next run
        judges. A supported statement citing a handle that no longer links the two
        (deleted, merged away) is stated again with the handles that do, so the
        review list can show it. Each run withdraws, restates and proposes at most
        ``limit`` pairs between them, looks the rule up once, and holds only the
        candidate addresses' links and the rule's own statements in memory.
        """

        limit = max(0, min(int(limit), 1000))
        if not limit:
            return 0
        started = timezone.now()
        source = self.rule_source("same_as")
        supported = self.same_person_pairs(
            self.same_person_links(shared=self.shared_sender_ids() if shared is None else shared)
        )
        statements = self.model.same_as.model.statements
        current = statements.current().filter(status=statements.model.StatementStatus.ASSERTED)
        own = list(
            current.filter(asserted_by=str(to_subject_ref(source)))
            .order_by("subject__left_id", "subject__right_id")
            .values_list("subject_id", "subject__left_id", "subject__right_id", "updated_at", "evidence")
        )
        source_ref = str(to_subject_ref(source))
        decided = {(left_id, right_id) for _subject, left_id, right_id, _at, _cited in own}
        others: set[tuple[Any, Any]] = set()
        lefts = sorted({left_id for left_id, _right in supported})
        for batch in batched(lefts, query_batch_size(current)):
            for left_id, right_id, asserted_by in current.filter(subject__left_id__in=batch).values_list(
                "subject__left_id", "subject__right_id", "asserted_by"
            ):
                decided.add((left_id, right_id))
                if asserted_by != source_ref:
                    others.add((left_id, right_id))
        unsupported = [
            subject for subject, left_id, right_id, at, _cited in own if (left_id, right_id) not in supported
            and at < started
        ][:limit]
        changed = statements.withdraw(source, unsupported) if unsupported else 0
        handle_model = apps.get_model("parties", "Handle")
        # Only the rule's own undecided pair is restated: a person's decision (or another source's) stands.
        stale = [
            (left_id, right_id)
            for _subject, left_id, right_id, _at, cited in own
            if (left_id, right_id) in supported
            and (left_id, right_id) not in others
            and not {EvidenceReference.model_validate(ref).id for ref in cited}
            <= {public_id_for(handle_model, pk) for pk in supported[left_id, right_id]}
        ]
        work = [*((pair, True) for pair in stale), *((pair, False) for pair in supported if pair not in decided)]
        work = work[: limit - changed]
        handles = handle_model._base_manager.in_bulk(sorted({pk for pair, _restate in work for pk in supported[pair]}))
        for (left_id, right_id), restate in work:
            evidence = [handles[pk] for pk in supported[left_id, right_id]]
            if self._state_same_person(left_id, right_id, evidence, source=source, restate=restate) is not None:
                changed += 1
        return changed

    def propose_same_person_as_system(self, left: Any, right: Any, *, evidence: Iterable[Any], reason: str) -> None:
        """Propose, once the caller commits, that two parties are one person, citing the handles that link them.

        An importer lands its own party and claim, then calls this. The call checks
        its arguments, issues no query and registers the proposal with
        ``transaction.on_commit``: nothing runs inside the caller's transaction, so
        it takes no lock there and waits on no pair, and a caller that rolls back
        proposes nothing. Called outside a transaction, it runs at once. After the
        commit it runs in a short transaction and changeset of its own, as named
        system work under ``reason``: each cited handle is read as its merge
        survivor and must link one of the two (:meth:`same_person_links`), and the
        two must be a same-person pair at a cited address
        (:meth:`same_person_pairs`). Then it states, under the pair's locks, the
        ``parties.same_as`` rule's pair statement, the one the scheduled job
        (:meth:`reconcile_same_as`) states, keeps while the pair stands and
        withdraws when it does not. It never merges, and writes nothing when the
        two are one party, either is trashed or merged away, the pair holds a
        current assertion or a person's keep-separate decision, or the evidence
        does not hold. A failure after the commit is logged, never raised; the job
        heals any miss, and consults the shared-sender providers this call does not.
        It elevates: the caller's own gate admitted the record it proposes for.
        """

        if not reason.strip():
            raise ValueError("A system same-person proposal requires a named reason.")
        handle_model = apps.get_model("parties", "Handle")
        handles = list(evidence)
        if not handles or not all(isinstance(row, handle_model) and row.pk is not None for row in handles):
            raise ValidationError({"evidence": "A same-person proposal cites the handle rows that link the two."})
        if left.pk is None or right.pk is None:
            raise ValidationError({"pair": "A same-person proposal names two saved parties."})
        if left.pk != right.pk:
            cited = tuple(row.pk for row in handles)
            transaction.on_commit(partial(self._propose_cited, left.pk, right.pk, cited, reason), robust=True)

    def _propose_cited(self, left_id: Any, right_id: Any, handle_ids: Sequence[Any], reason: str) -> Any | None:
        """Run one committed proposal of :meth:`propose_same_person_as_system`; return its statement or ``None``."""

        handle_model = apps.get_model("parties", "Handle")
        with changeset(fresh=True, activity="system", change_reason=reason), system_context(reason=reason):
            survivors = handle_model.canonical_ids(handle_ids)
            cited = list(dict.fromkeys(survivors[pk] for pk in handle_ids if pk in survivors))
            rows = handle_model._base_manager.in_bulk(cited)
            links = self.same_person_links(addresses={(row.platform, row.normalized_value) for row in rows.values()})
            pair = (min(left_id, right_id), max(left_id, right_id))
            linking = {link.handle for address in links.values() for link in address if link.party in pair}
            if not cited or not linking.issuperset(cited) or pair not in self.same_person_pairs(links):
                logger.info("A same-person proposal under %s is not supported by its evidence.", reason)
                return None
            return self._state_same_person(*pair, [rows[pk] for pk in cited], source=self.rule_source("same_as"))

    def _state_same_person(
        self, left_id: Any, right_id: Any, handles: Sequence[Any], *, source: Any, restate: bool = False
    ) -> Any | None:
        """State ``source``'s same_as value statement on the live, undecided pair, citing ``handles``.

        The pair's endpoints lock in one pass first (``Party.lock_subjects``), then
        their state and the pair's decisions are read again under the lock; ``None``
        when either is trashed or merged away or the pair holds a current
        assertion, other than ``source``'s own when ``restate`` refreshes it. Runs
        inside the caller's system context, in its own transaction unless the
        caller holds one.
        """

        party_model = apps.get_model("parties", "Party")
        stances = party_model.same_as.model.statements
        decided = stances.current().filter(
            subject__left_id=min(left_id, right_id),
            subject__right_id=max(left_id, right_id),
            status=stances.model.StatementStatus.ASSERTED,
        )
        if restate:
            decided = decided.exclude(asserted_by=str(to_subject_ref(source)))
        # A decided pair is read before it is locked: it takes no lock and makes no pair row.
        if decided.exists():
            return None
        with transaction.atomic():
            parties = party_model._base_manager.filter(pk__in=(left_id, right_id))
            live = parties.filter(is_trashed=False, merged_into__isnull=True)
            if live.count() != 2:
                return None
            first, second = parties.order_by("pk")
            pair = party_model.same_as.for_pair(first.with_actor(to_subject_ref(source)), second)
            # The pair locked both parties: read their state and the pair's decisions again under the lock.
            if live.count() != 2 or decided.exists():
                return None
            return pair.statements.set(
                aspect="same_as",
                values={},
                asserted_by=source,
                evidence=[EvidenceReference.of(row) for row in handles],
            )

    def propose_directory_match(self, person: Any, other: Any, *, evidence: Sequence[Any]) -> Any | None:
        """Propose, as the ``directory_match`` rule, that a directory's ``person`` and ``other`` are one person.

        Only for what no address links, which the one same-person verb
        (:meth:`propose_same_person_as_system`) cannot cite and its job would
        withdraw as unsupported: the person of another record the source merged
        into the contact (``DirectoryBackend.apply_record``), and an account whose
        sign-in address the contact states while no handle of theirs carries it
        (:meth:`ingest_contact`). A pair two people's links to one address tie is
        the verb's. ``evidence`` names what ties them (the contact's link, then
        the merged record's link). The pair joins the same-as review
        (:meth:`PartyQuerySet.same_as_pairs`), where a person merges or dismisses
        it. A pair kept separate is not proposed again, and proposing an unchanged
        pair again changes nothing (``StatementManager.set``). The ``same_as``
        rule's reconciler withdraws only its own rule's pairs
        (:meth:`reconcile_same_as`), so it leaves these standing; the rule's
        earlier proposals of an address pair stay reviewable as they are. Returns
        the rule's statement, ``None`` when nothing is proposed.
        """

        party = apps.get_model("parties", "Party")
        if person.pk == other.pk or party.same_as.kept_separate(person, other):
            return None
        with system_context(reason="parties.directory_match"):
            rule = self.rule_source("directory_match")
            left, right = (party._base_manager.get(pk=row.pk) for row in (person, other))
            pair = party.same_as.for_pair(left.with_actor(to_subject_ref(rule)), right)
            return pair.statements.set(
                aspect="same_as",
                values={},
                asserted_by=rule,
                evidence=[EvidenceReference.of(row) for row in evidence],
            )

    def withdraw_directory_match(self, person: Any, other: Any) -> int:
        """Withdraw the ``directory_match`` rule's proposal that ``person`` and ``other`` are one person.

        Called when the proposal is moot: the two are merged (``Party.merge_fields``),
        or the contact whose link left ``person`` lands on ``other`` or proposes its
        new person as ``other`` (:meth:`ingest_contact`). Returns how many
        statements it withdrew.
        """

        party = apps.get_model("parties", "Party")
        left_id, right_id = sorted((person.pk, other.pk))
        pair = party.same_as.model._base_manager.filter(left_id=left_id, right_id=right_id).first()
        if pair is None:
            return 0
        with system_context(reason="parties.directory_match"):
            return party.same_as.model.statements.withdraw(self.rule_source("directory_match"), [pair.pk])

    def reconcile_suggestions(self, *, shared: Collection[Any] | None = None) -> int:
        """Reconcile the evidence-backed suggestions with every registered evidence provider.

        Display-name evidence is parties' own. Downstream addons contribute the rest
        as dotted callables, so parties reads no other addon's models:

        - ``shared`` handles, by default the ones ``ANGEE_PARTIES_SHARED_SENDER_PROVIDERS``
          name (:meth:`shared_sender_ids`), are addresses whose display names are no
          evidence (list and notification senders).
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
            changed = self._reconcile_display_names(
                self.shared_sender_ids() if shared is None else frozenset(shared)
            )
            if providers := resolve_hooks("ANGEE_PARTIES_SIGNING_PROVIDERS"):
                changed += self._reconcile_signatures(chain.from_iterable(provider() for provider in providers))
        return changed

    def _reconcile_signatures(self, signings: Iterable[Signing]) -> int:
        """Reconcile weak party-to-phone suggestions with every signature in the owners' mail.

        Each :class:`Signing` is neutral evidence that one sender signed with one
        fragment. This manager owns phone extraction, Handle creation, link
        provenance, owner partition (a suggestion links only a party the mail's
        creator owns to a handle that user created) and the durable-pair check. A
        number is a person's own only while that one person (or one unresolved
        address) signs with it in the owner's mail: forwards, company switchboards
        and conference bridges repeat a number under several people, and none of
        them owns it. Each such number of a resolved sender is one ``0.3``
        suggestion recording its fragment; an undecided suggestion the evidence no
        longer supports is withdrawn, so ``signings`` must be complete. An existing
        pair, including a dismissed anti-link, is never changed.

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
                or party.owner_id != owner_id
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
        """Reconcile weak identity links with the full display names pooled per handle creator.

        A resolved handle supplies evidence only to a handle on another platform,
        and only for a party that bears the same full name itself. A one-word name
        (a first name, a brand) identifies nobody, and a party named otherwise, such
        as a nameless card holding several people's numbers, is not that person.
        ``shared`` handles are addresses several people speak through: their display
        names name whoever spoke last and are no evidence. Each distinct candidate
        party the creator owns receives one ``0.4`` rule link per unresolved handle. A
        suggestion is withdrawn only when its names no longer support it, not when
        its handle later resolves to a party; existing pairs, including dismissed
        links, remain untouched.
        """

        handle_model = apps.get_model("parties", "Handle")
        supported: set[tuple[Any, Any]] = set()
        created = 0
        owner_ids = (
            handle_model.objects.canonical().exclude(created_by_id=None)
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
                handle_model.objects.canonical().filter(created_by_id=owner_id)
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
                        or candidate.party.owner_id != owner_id
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
        links = link_owner.handle_statements().system_context(reason=reason).filter(subject=party)
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
        readable_links = set(
            link_owner.handle_statements()
            .for_reader(actor)
            .members()
            .filter(subject=party)
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
                pair = self.model.same_as.for_pair(left.with_actor(actor), right).with_actor(actor)
                pair.dismiss_statement(aspect="same_as")
                return "dismissed"
            into, source = (
                (left, right) if survivor == "left" else (right, left)
            )
            already_merged = source.merged_into_id == into.pk
            if already_merged and into.merged_into_id is None:
                return "already_merged"
            into.merge(records=[source.public_id])
            return "already_merged" if already_merged else "merged"

    BATCH_SETS: ClassVar[int] = 50
    """The most sets of records one batch merge takes."""

    SET_MEMBERS: ClassVar[int] = 10
    """The most records one set of a batch merge holds."""

    CHANGED_REASON: ClassVar[str] = (
        "These records changed since you looked at them; nothing merged. Look at them again before merging."
    )
    """What a person reads of a set whose shown facts changed between the preview and the merge."""
    STOPPED_REASON: ClassVar[str] = (
        "The database stopped this merge for a concurrent change; nothing merged, try again."
    )
    """What a person reads of a set a lock conflict stopped."""
    FAILED_REASON: ClassVar[str] = (
        "The database could not complete this merge; nothing merged. Try again, or ask an operator to read the log."
    )
    """What a person reads of a set a database failure stopped; the error itself goes to the log."""
    BATCH_CONFIRMED_FIELDS: ClassVar[tuple[str, ...]] = ()
    """The merge fields a batch merge records as the person's choice: none.

    Each record's statements move to the survivor as their sources' own and the
    provenance ranking decides what shows; an empty survivor field takes a merged
    record's value as a plain value no one states. ``("display_name",)`` would pin
    the survivor's name, which the dialog shows.
    """

    def certain_same_as_pairs(
        self, *, actor: Any, limit: int = 50, after: str | None = None,
    ) -> list[CertainSameAsPair]:
        """The certain pairs awaiting ``actor``'s review that ``actor`` may merge in a batch, at most ``limit``.

        The batch is the first certain pairs (:meth:`certain_pairs`) of ``actor``'s
        review list (:meth:`PartyQuerySet.same_as_pairs`) after ``after`` (the last
        pair of the previous batch), in its order, at most :attr:`BATCH_SETS`: the
        Review page's source for a batch merge, whose sets :meth:`merge_duplicates`
        re-checks. The next batch continues past the last one, so a pair a batch
        left unmerged is offered again only once the list starts over.
        """

        limit = max(0, min(int(limit), self.BATCH_SETS))
        if not limit:
            return []
        review = apps.get_model("parties", "Party")._default_manager.with_actor(actor).same_as_pairs(
            limit=None, after=after,
        )
        return self.certain_pairs(review, actor=actor)[:limit]

    def same_as_review_counts(self, *, actor: Any) -> SameAsReviewCounts:
        """How many pairs ``actor`` may review and how many of those are certain, in a fixed number of reads."""

        review = apps.get_model("parties", "Party")._default_manager.with_actor(actor).same_as_pairs(limit=None)
        return SameAsReviewCounts(len(review), len(self.certain_pairs(review, actor=actor)))

    def certain_pairs(self, review: Sequence[SameAsPair], *, actor: Any) -> list[CertainSameAsPair]:
        """The certain pairs among these review pairs, each with its survivor: the one definition of certain.

        A pair is certain when both are people and neither is an account's own
        person, ``actor`` may merge both in a batch (``batch_merge``: an active
        person account that writes them), both bear a real name that is none of the
        pair's cited addresses (``Party.names_an_address``) and equals the other's
        but for case, accents and hyphens (``Party.comparable_name``), and exactly
        these two parties are linked to an address the pair cites
        (:meth:`same_person_links`, the shared-sender providers consulted, read only
        when a pair passes the rest). The older record survives.
        """

        if not review:
            return []
        ids = {party.pk for pair in review for party in (pair.left, pair.right)}
        people, _organizations = self.party_kinds(ids)
        permitted = self.batch_mergeable(actor, ids)

        def mergeable(party: Any, handles: Sequence[Any]) -> bool:
            return (
                party.pk in permitted
                and party.pk in people
                and people[party.pk] is None
                and bool(party.has_real_name)
                and not party.names_an_address(handles)
            )

        candidates = [
            pair
            for pair in review
            if mergeable(pair.left, pair.handles)
            and mergeable(pair.right, pair.handles)
            and pair.left.comparable_name == pair.right.comparable_name
        ]
        if not candidates:
            return []
        with system_context(reason="parties.certain_pairs"):
            links = self.same_person_links(
                addresses={(row.platform, row.normalized_value) for pair in candidates for row in pair.handles},
                shared=self.shared_sender_ids(),
            )
        linked = {address: {link.party for link in address_links} for address, address_links in links.items()}
        return [
            CertainSameAsPair(
                pair.left, pair.right, pair.normalized_value, min((pair.left, pair.right), key=_age), id=pair.id,
            )
            for pair in candidates
            if any(
                linked.get((row.platform, row.normalized_value)) == {pair.left.pk, pair.right.pk}
                for row in pair.handles
            )
        ]

    def batch_mergeable(self, actor: Any, ids: Iterable[Any]) -> set[Any]:
        """The parties among ``ids`` that ``actor`` may merge in a batch: an active person who writes them.

        The party's ``batch_merge`` permission (``write & person->active_member``),
        so a service or agent account may merge none.
        """

        # Read on the party, the owner of the permission, whichever model's manager the caller used.
        parties = apps.get_model("parties", "Party")._default_manager
        rows = parties.with_actor(actor).with_action("batch_merge").filter(pk__in=sorted(set(ids)))
        return set(rows.values_list("pk", flat=True))

    def preview_duplicates(
        self, *, actor: Any, sets: Sequence[Sequence[str]], certain: bool = False,
    ) -> list[DuplicateSetPreview]:
        """Each set of records ``actor`` confirms are one, as :meth:`merge_duplicates` would merge it now.

        Read in a fixed number of queries whatever the number of sets and members.
        A set merges when it holds two to :attr:`SET_MEMBERS` records, every one
        readable, live (neither trashed nor merged away) and of one kind (all
        people or all organizations); none is an account's own person, which a
        person merges by hand on the compare page; ``actor`` may merge every one in
        a batch (:meth:`batch_mergeable`); and no two of them were kept apart by a
        person. With ``certain`` each set must also be a certain pair
        (:meth:`certain_pairs`). The oldest member survives and the others fold
        into it in key order. More than :attr:`BATCH_SETS` sets refuse the batch.
        """

        confirmed = self._confirmed_sets(sets)
        return self._judge_sets(actor, confirmed, certain=certain)

    def merge_duplicates(
        self,
        *,
        actor: Any,
        sets: Sequence[Sequence[str]],
        certain: bool = False,
        digests: Sequence[str] | None = None,
    ) -> list[DuplicateSetOutcome]:
        """Merge each confirmed set of records into its oldest member as ``actor``'s merge; return every outcome.

        A set merges exactly as confirmed. Its size, the actor's read of every
        member and the actor's batch permission (:meth:`batch_mergeable`) are
        checked first, for every set in one batch of reads (:meth:`_admission`), so
        a set the actor may not merge takes no lock and is not read further.
        Then, in its own transaction, its members, their same-person partners and
        every Handle any of them holds or names are locked in one pass
        (``Party.lock_merge_records``), the set is judged again under those locks
        (:meth:`preview_duplicates`), and a set whose shown facts changed since the
        preview (its ``digests`` entry, :attr:`DuplicateSetPreview.digest`, no
        longer matches) is refused with :attr:`CHANGED_REASON`, as is a member that changed
        since the person looked (merged elsewhere, trashed, kept apart from another)
        refuses the set rather than shrinking it. Then the others fold into the
        oldest in key order through ``Party.merge``, whose own permission and guards
        decide, under the person's confirmation of each pair. Values: each record's
        statements move as their sources' own and the provenance ranking decides
        what shows; an empty survivor field takes a merged record's value as a plain
        value the sources may still replace; no value is recorded as the person's
        choice (:attr:`BATCH_CONFIRMED_FIELDS`). Each set runs in its own
        transaction and stands alone: a refusal is reported with its reason, a merge
        a lock conflict stops (a deadlock, a lock that was not granted in time,
        :func:`_lock_conflict`) as ``stopped``, and any other database failure (a
        statement timeout, a lost connection) as ``failed`` with
        :attr:`FAILED_REASON`, its error logged for an operator; the next set still
        runs. More than :attr:`BATCH_SETS` sets refuse the batch.
        """

        party_model = apps.get_model("parties", "Party")
        confirmed = self._confirmed_sets(sets)
        if digests is not None and len(digests) != len(confirmed):
            raise ValidationError({"digests": "Send one digest for each set."})
        readable = self._readable_members(actor, confirmed)
        permitted = self.batch_mergeable(actor, sorted({row.pk for row in readable.values()}))
        outcomes = []
        for index, ids in enumerate(confirmed):
            if refusal := self._admission(ids, readable, permitted):
                outcomes.append(DuplicateSetOutcome(ids, DuplicateMergeOutcome.REFUSED, reason=refusal))
                continue
            try:
                with transaction.atomic():
                    rows = instances_from_public_ids(party_model, ids, queryset=party_model._base_manager.all())
                    if rows:
                        party_model.lock_merge_records(sorted(rows.values(), key=lambda row: row.pk))
                    (preview,) = self._judge_sets(actor, [ids], certain=certain)
                    if preview.outcome is not None:
                        outcomes.append(DuplicateSetOutcome(ids, preview.outcome, reason=preview.reason))
                        continue
                    if digests is not None and preview.digest != digests[index]:
                        outcomes.append(
                            DuplicateSetOutcome(ids, DuplicateMergeOutcome.REFUSED, reason=self.CHANGED_REASON)
                        )
                        continue
                    survivor, *merged = preview.members
                    kept = survivor.with_actor(actor).merge(
                        records=[row.public_id for row in merged],
                        fill_empty=True,
                        confirmed_fields=self.BATCH_CONFIRMED_FIELDS,
                    )
            except (PermissionDenied, ValidationError) as error:
                outcomes.append(DuplicateSetOutcome(ids, DuplicateMergeOutcome.REFUSED, reason=_refusal(error)))
            except DatabaseError as error:
                if _lock_conflict(error):
                    outcomes.append(DuplicateSetOutcome(ids, DuplicateMergeOutcome.STOPPED, reason=self.STOPPED_REASON))
                else:
                    logger.exception("parties.merge_duplicates: the database failed one set; nothing of it merged")
                    outcomes.append(DuplicateSetOutcome(ids, DuplicateMergeOutcome.FAILED, reason=self.FAILED_REASON))
            else:
                outcomes.append(DuplicateSetOutcome(ids, DuplicateMergeOutcome.MERGED, survivor=kept))
        return outcomes

    def _readable_members(
        self, actor: Any, confirmed: Sequence[tuple[str, ...]], *, statements: bool = False,
    ) -> Mapping[str, Any]:
        """The members of the sets within the size cap that ``actor`` reads, by the public id given, in one read.

        ``statements`` loads with them the handle statements they show ``actor``
        (:meth:`readable_statements_prefetch`), inside that same read.
        """

        party_model = apps.get_model("parties", "Party")
        named = {pk for ids in confirmed if len(ids) <= self.SET_MEMBERS for pk in ids}
        readable = party_model._default_manager.with_actor(actor)
        if statements:
            readable = readable.prefetch_related(self.readable_statements_prefetch(actor))
        return instances_from_public_ids(party_model, named, queryset=readable)

    def _admission(self, ids: Sequence[str], readable: Mapping[str, Any], permitted: Collection[Any]) -> str:
        """Why a set is refused before it is locked or read further, or ``""``.

        Its size, the actor's read of every member, then the actor's batch
        permission on every member (``permitted``, from :meth:`batch_mergeable`).
        """

        if not 2 <= len(ids) <= self.SET_MEMBERS:
            return f"Choose two to {self.SET_MEMBERS} records to merge as one."
        if any(pk not in readable for pk in ids):
            return "A record is gone or you may not read it."
        if any(readable[pk].pk not in permitted for pk in ids):
            return "Only an active person who may change every one of these records merges them in a batch."
        return ""

    def _confirmed_sets(self, sets: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
        confirmed = [tuple(dict.fromkeys(str(pk) for pk in ids)) for ids in sets]
        if len(confirmed) > self.BATCH_SETS:
            raise ValidationError({"sets": f"Merge at most {self.BATCH_SETS} sets of records in one batch."})
        return confirmed

    def _judge_sets(
        self, actor: Any, confirmed: Sequence[tuple[str, ...]], *, certain: bool,
    ) -> list[DuplicateSetPreview]:
        """Judge every confirmed set against the live records in one batch of reads (:meth:`preview_duplicates`)."""

        party_model = apps.get_model("parties", "Party")
        readable = party_model._default_manager.with_actor(actor)
        # With the statements the dialog lists, read once for every member and digested (the schema reuses them).
        found = self._readable_members(actor, confirmed, statements=True)
        pks = sorted({row.pk for row in found.values()})
        people, organizations = self.party_kinds(pks)
        permitted = self.batch_mergeable(actor, pks)
        stance = party_model.same_as.model.statements.model
        # The same keep-apart decision ``StatementPairManager.kept_separate`` reads, for every pair of the sets at once.
        kept_apart = set(
            stance._base_manager.filter(
                superseded__isnull=True,
                subject__left_id__in=pks,
                subject__right_id__in=pks,
                aspect=party_model.same_as.model.pair_name,
                applied=True,
                snak=stance.StatementSnak.NO_VALUE,
            ).values_list("subject__left_id", "subject__right_id")
        )
        review = readable.same_as_pairs(limit=101, parties=pks) if certain and pks else []
        sure = {(pair.left.pk, pair.right.pk) for pair in self.certain_pairs(review, actor=actor)}
        previews = []
        for ids in confirmed:
            members = sorted((found[pk] for pk in ids if pk in found), key=_age)
            keys = [row.pk for row in members]
            if admission := self._admission(ids, found, permitted):
                reason = admission
            elif any(row.is_trashed or row.merged_into_id is not None for row in members):
                reason = "A record is trashed or already merged into another."
            elif not (set(keys) <= people.keys() or set(keys) <= organizations):
                reason = "Only people, or only organizations, merge together."
            elif any(people.get(pk) is not None for pk in keys):
                reason = "An account's own person is merged by hand, on the compare page."
            elif any((left, right) in kept_apart for left, right in combinations(sorted(keys), 2)):
                reason = "A person kept two of these records apart."
            else:
                reason = ""
            if reason:
                previews.append(DuplicateSetPreview(tuple(members), DuplicateMergeOutcome.REFUSED, reason))
            elif certain and (len(keys) != 2 or (min(keys), max(keys)) not in sure):
                previews.append(
                    DuplicateSetPreview(tuple(members), DuplicateMergeOutcome.NOT_CERTAIN, "No longer a certain pair.")
                )
            else:
                previews.append(DuplicateSetPreview(tuple(members), None))
        # Only a set the actor may merge discloses its members' values.
        differing = self._differing_fields(
            actor, [preview.members if preview.outcome is None else () for preview in previews], people,
        )
        judged = [
            replace(preview, disagreements=differing[index][0], fills=differing[index][1])
            for index, preview in enumerate(previews)
        ]
        return [
            replace(preview, digest=self._shown_digest(preview)) if preview.outcome is None else preview
            for preview in judged
        ]

    def readable_statements_prefetch(self, actor: Any) -> Prefetch:
        """The handle statements a party shows ``actor``, each with its handle read through ``actor``."""

        handles = apps.get_model("parties", "Handle").objects.with_actor(actor)
        return Prefetch(
            "partystatement_set",
            queryset=self.handle_statements().for_reader(actor).prefetch_related(Prefetch("handle", queryset=handles)),
            to_attr="_readable_handle_statements",
        )

    @staticmethod
    def _shown_digest(preview: DuplicateSetPreview) -> str:
        """The keyed digest of what the dialog shows of a set (:attr:`DuplicateSetPreview.digest`).

        It covers what the reader was shown and nothing else: each member's id,
        name, creation time, first-met note (as the reader's gates leave it) and
        the readable handle statements with their source and handle, the
        survivor, and the disagreements and the fills with the values that will show.
        """

        def statement(row: Any) -> list[Any]:
            handle = row.handle
            return [
                public_id_for(type(row), row.pk), str(row.source_kind),
                *([handle.public_id, str(handle.platform), handle.value] if handle is not None else []),
            ]

        return keyed_facts_digest(DUPLICATE_SET_DIGEST, {
            "members": [
                {
                    "id": row.public_id,
                    "name": row.display_name,
                    "created": row.created_at.isoformat() if row.created_at else None,
                    "first_met": row.first_met_note,
                    "statements": sorted(statement(item) for item in row._readable_handle_statements),
                }
                for row in preview.members
            ],
            "survivor": preview.members[0].public_id if preview.members else None,
            "disagreements": [[row.field, list(row.values), row.shown] for row in preview.disagreements],
            "fills": [[row.field, list(row.values), row.shown] for row in preview.fills],
        })

    @staticmethod
    def _differing_fields(
        actor: Any, sets: Sequence[Sequence[Any]], people: Mapping[Any, Any],
    ) -> list[tuple[tuple[MergeFieldValues, ...], tuple[MergeFieldValues, ...]]]:
        """For each set, its disagreements and its fills (:class:`DuplicateSetPreview`), in fixed reads.

        A merge field whose values differ is a disagreement when its members state
        at least two different values, else a fill: one value stated, the rest
        empty. Each row carries every member's value and what the survivor will show.

        ``sets`` hold members read through ``actor`` and the person rows are read
        through it too, so each field's own ``read__<field>`` gate has hidden what
        the actor may not read, as on the record read, for the whole batch at
        once. A field hidden on any member of a set is not compared there: the
        actor sees neither its values nor whether they differ.
        """

        party_model = apps.get_model("parties", "Party")
        person_model = apps.get_model("parties", "Person")
        pks = sorted({row.pk for members in sets for row in members})
        if not pks:
            return [((), ()) for _members in sets]
        persons = person_model._default_manager.with_actor(actor).in_bulk([pk for pk in pks if pk in people])
        owners: dict[str, tuple[Any, str]] = {}
        stated: defaultdict[tuple[str, Any], list[Any]] = defaultdict(list)
        for statements in (party_model.statements, person_model.statements):
            aspects = {
                aspect: declaration
                for aspect, declaration in statements.model.statement_aspects.items()
                if declaration.fields and not declaration.members
            }
            for aspect, declaration in aspects.items():
                for name in declaration.fields:
                    owners[name] = (statements.model, aspect)
            rows = (
                statements.model._base_manager.filter(subject_id__in=pks, aspect__in=list(aspects))
                .filter(superseded__isnull=True, status=statements.model.StatementStatus.ASSERTED, applied=True)
                .filter(snak=statements.model.StatementSnak.VALUE)
                .select_related("subject", "link")
            )
            for row in rows:
                stated[row.aspect, row.subject_id].append(row)

        def text(value: Any) -> str | None:
            if value in (None, ""):
                return None
            return value.isoformat() if hasattr(value, "isoformat") else str(value)

        personal = set(party_model.merge_scalar_fields_of("person")) - set(party_model.merge_scalar_fields_of(None))

        def source_of(row: Any, name: str) -> Any:
            return persons.get(row.pk, row) if name in personal else row

        result: list[tuple[tuple[MergeFieldValues, ...], tuple[MergeFieldValues, ...]]] = []
        for members in sets:
            if len(members) < 2:
                result.append(((), ()))
                continue
            kind = "person" if all(row.pk in people for row in members) else None
            fields = party_model.merge_scalar_fields_of(kind)
            disagreements: list[MergeFieldValues] = []
            fills: list[MergeFieldValues] = []
            for name in fields:
                sources = [source_of(row, name) for row in members]
                if any(name in redacted_fields(source) for source in sources):
                    continue
                values = tuple(text(getattr(source, name, None)) for source in sources)
                if len(set(values)) < 2:
                    continue
                _model, aspect = owners.get(name, (None, ""))
                candidates = [statement for row in members for statement in stated.get((aspect, row.pk), ())]
                if candidates:
                    winner = min(candidates, key=lambda statement: statement.sort_key())
                    shown = text(winner.stated_value(name))
                else:
                    shown = next((value for value in values if value is not None), None)
                given = {value for value in values if value is not None}
                (disagreements if len(given) > 1 else fills).append(MergeFieldValues(name, values, shown))
            result.append((tuple(disagreements), tuple(fills)))
        return result

    def for_user(self, user: Any, *, adopt: Any = None) -> Any:
        """Return the :class:`Person` linked to ``user``, get-or-created on the ``user`` O2O.

        The single owner of the one-Person-per-user invariant: every identity-link
        writer (OIDC first login, the connect flows, messaging reaction attribution)
        routes through here, so a user never grows two person rows. Targets the
        ``Person`` model explicitly, runs within the caller's ``system_context``, and
        stamps ``created_by``, so the user also owns their Person.

        ``adopt`` is an existing Person a trusted ingress resolved as this user
        (an imported contact, carried by IAM's person event): it becomes the user's
        Person instead of a new row, keeping its identity and statements, and its
        ownership passes to the account, as a Person created here is owned by its
        user. A Person already linked to another user, or a user already linked to
        another Person, refuses rather than moving an identity.
        """

        person_model = apps.get_model("parties", "Person")
        if adopt is None:
            person, _created = person_model.objects.get_or_create(
                user=user,
                defaults={"display_name": _user_display_name(user), "created_by_id": user.pk},
            )
            return person
        with transaction.atomic():
            person = person_model._base_manager.filter(pk=adopt.pk).lock_if_supported().get()
            linked = person_model._base_manager.filter(user=user).exclude(pk=person.pk).exists()
            if linked or person.user_id not in (None, user.pk):
                raise ValidationError({"user": "This person and this account already have other identities."})
            if person.user_id is None:
                person.user, person.owner = user, user
                person.save(update_fields=["user", "owner", "updated_at"])
            return person

    def follow_user_name(self, user: Any) -> None:
        """Rename ``user``'s linked :class:`Person` after the account, as :meth:`for_user` names it.

        Runs within the caller's ``system_context``; a person already carrying
        the account's name is left untouched, and so is one whose name a source
        states (an adopted contact): the ``identity`` aspect's survivorship owns it.
        A person's own statement of their name is no source: it follows the account.
        """

        name = _user_display_name(user)
        person_model = apps.get_model("parties", "Person")
        party_model = apps.get_model("parties", "Party")
        stated = party_model.statements.model._base_manager.filter(
            aspect="identity", applied=True, superseded__isnull=True
        ).exclude(source_kind=SourceKind.PERSON)
        people = person_model.objects.filter(user=user).exclude(display_name=name)
        for person in people.exclude(pk__in=stated.values("subject_id")):
            person.display_name = name
            person.save(update_fields=["display_name", "updated_at"])

    def search_display_name(self, query: str, *, limit: int = 20) -> list[Any]:
        """Return a bounded actor-visible people list filtered by display name."""

        bounded = clamp_page_size(limit)
        return list(
            self.canonical()
            .with_circle_names()
            .filter(display_name__icontains=query.strip())
            .order_by("display_name", "sqid")[:bounded]
        )

    def identity_for_user_id(self, user_id: Any) -> Any | None:
        """Return the existing Person party linked to ``user_id`` without creating one."""

        person_model = apps.get_model("parties", "Person")
        party_id = self.sudo(reason="parties.party.identity_for_user").person_id_for_user(user_id)
        return person_model._base_manager.filter(pk=party_id).first() if party_id is not None else None

    def user_for(self, party: Any) -> Any | None:
        """Return the platform user linked to ``party`` when it is a Person.

        This manager owns the Party-to-Person MTI lookup so consumers never
        inspect the concrete child table or its base manager themselves. An
        organization or external Person without a user resolves to ``None``.
        """

        return self.sudo(reason="parties.party.user_for").account_for_party(party.pk)

    def ingest_connections(
        self, parsed_connections: Iterable[tuple[ParsedConnection, Any]], *, created_by_id: Any, asserted_by: Any
    ) -> list[Any]:
        """Idempotently land parsed social connections and return their Person rows.

        The batch entry point: the source user, its anchor Party, and each required
        ``RelationshipKind`` resolve once for the whole run — a friends export is
        thousands of records and must not repeat loop-invariant lookups.
        Each item pairs the parsed value with its observed RecordLink. Records land
        in separate transactions, so one bad record fails alone. Organization names
        in social exports are free text, not stable identities, so an employer
        lands as ``Relationship.other_name`` exactly like directory employment.
        """

        if StatementSourceMixin.source_kind_for(to_subject_ref(asserted_by)) != SourceKind.IMPORT:
            raise ValidationError("Connection exports require an identified IMPORT integration.")
        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        with system_context(reason="parties.connection.ingest"):
            user = user_model._base_manager.get(pk=created_by_id)
            anchor_party = self.for_user(user)
            kinds: dict[Any, Any] = {}
            return [
                self._ingest_connection(
                    parsed, link=link, anchor_party=anchor_party, kinds=kinds,
                    created_by_id=created_by_id, asserted_by=asserted_by,
                )
                for parsed, link in parsed_connections
            ]

    def _ingest_connection(
        self,
        parsed: ParsedConnection,
        *,
        link: Any,
        anchor_party: Any,
        kinds: dict[Any, Any],
        created_by_id: Any,
        asserted_by: Any,
    ) -> Any:
        """Land one parsed connection and return its Person.

        The source account's user is the relationship anchor. The imported person
        becomes its ``acquaintance`` from ``connected_at`` onward; normal handle
        assertions name the supplied IMPORT integration and are never preferred: an
        export is a source, not the person's decision. It decides the handle's party
        (confidence 1.0) only when the handle names an account by a real identifier
        (:meth:`Handle.identified_connection`); a name alone lands at 0.4, for review.
        Optional email and employment facts compose the same Handle, PartyStatement
        and Relationship owners.
        """

        handle_model = apps.get_model("parties", "Handle")
        relationships = apps.get_model("parties", "Relationship").objects
        if to_subject_ref(link.stream.integration) != to_subject_ref(asserted_by):
            raise ValidationError("The connection link must belong to its asserting integration.")
        with transaction.atomic():
            handle = handle_model.objects.upsert(
                platform=parsed.handle.platform,
                value=parsed.handle.value,
                external_id=parsed.handle.external_id,
                display_name=parsed.handle.display_name,
                metadata=parsed.handle.metadata,
                created_by_id=created_by_id,
            )
            person = self._connection_person(
                handle,
                display_name=parsed.handle.display_name or parsed.handle.value,
                asserted_by=asserted_by,
                created_by_id=created_by_id,
            )
            parent = apps.get_model("parties", "Party")._base_manager.get(pk=person.pk)
            apps.get_model("integrate", "RecordLink").objects.bind_target(link, parent)
            self.assert_handle(
                person,
                handle,
                confidence=1.0 if handle_model.identified_connection(parsed.handle) else 0.4,
                asserted_by=asserted_by,
                link=link,
                evidence=[handle],
            )
            if parsed.email:
                email = handle_model.objects.upsert(
                    platform=handle_model.Platform.EMAIL,
                    value=parsed.email,
                    display_name=person.display_name,
                    created_by_id=created_by_id,
                )
                self.assert_handle(
                    person, email, confidence=1.0, asserted_by=asserted_by, link=link, evidence=[handle],
                )
            relationships.land_acquaintance(
                anchor_party,
                person,
                connected_at=parsed.connected_at,
                kinds=kinds,
                created_by_id=created_by_id,
                asserted_by=asserted_by,
                link=link,
                evidence=handle,
            )
            employer = parsed.organization.strip()
            relationships.land_employment(
                person,
                [Employment(employer, parsed.title)] if employer else [],
                kinds=kinds,
                created_by_id=created_by_id,
                asserted_by=asserted_by,
                link=link,
                evidence=[handle],
            )
            parent.statements.set(
                aspect="identity",
                values={"display_name": parsed.handle.display_name or parsed.handle.value},
                asserted_by=asserted_by,
                link=link,
                evidence=[EvidenceReference.of(handle)],
            )
            person.refresh_from_db()
            return person

    def _connection_person(self, handle: Any, *, display_name: str, asserted_by: Any, created_by_id: Any) -> Any:
        """Return the handle's Person; identity statements own subsequent names.

        The person who holds the handle, by the holding rule :meth:`people_for_emails`
        answers with (:meth:`_holders`: through merge survivors, never an
        organization or a trashed person); otherwise the untrashed person an earlier
        run of the same import linked it to, since a claim awaiting review, or one
        the person dismissed, resolves nothing; a re-import therefore never mints a
        second person, and one whose earlier person is trashed mints a new one.
        An existing name is never overwritten: it may be user-curated, and export
        text must not clobber curation on every re-run.
        """

        person_model = apps.get_model("parties", "Person")
        held = self._holders([handle]).get(handle.pk)
        if held is not None and held[1] is not None:
            return held[1]
        party_id = (
            apps.get_model("parties", "PartyStatement")
            ._base_manager.filter(
                handle=handle,
                aspect="handle",
                asserted_by=str(to_subject_ref(asserted_by)),
                subject__is_trashed=False,
            )
            .order_by("pk")
            .values_list("subject_id", flat=True)
            .first()
        )
        if party_id is not None:
            person = person_model._base_manager.filter(pk=party_id).first()
            if person is not None:
                return person
        return person_model.objects.create(
            display_name=display_name or person_model.PLACEHOLDER_NAME,
            created_by_id=created_by_id,
        )

    def people_for_emails(self, emails: Iterable[str]) -> list[EmailHolder]:
        """Return the people the given email addresses name, each with the basis it names them by; decide nothing.

        ``emails`` come in the caller's preference order, and so does the answer: per
        address, at most one person on each basis, in this order (:class:`EmailBasis`):

        1. ``ACCOUNT``: the user account with that address is its user's existing
           Person (``UserManager.person_for_email``'s rule: an address two
           accounts share names nobody); an account without a Person names
           nobody, and a caller that needs one asks ``for_user`` itself;
        2. ``HELD``: the first email handle of the address that a live Person
           holds names them, through merge survivors;
        3. ``CLAIM``: whenever no live Person holds the address, the one live
           person a current asserted contact claim proposes a handle of it for;
           two claimants name nobody.

        A holder that is no live Person (an organization, a trashed person) does
        not count as holding the address: the next handle a live Person holds
        answers ``HELD``, and with none the claims answer, so a person minted
        earlier for an organization's address is found again by their claim.
        Handles compare by ``Handle.normalized_value``, so another spelling of the
        address is the same address whichever row its exact value made (the account
        compares by the account's own normalization). A trashed person, an
        organization and a merged-away row are nobody's answer. One person may come
        under several bases, and an address two people share (a family's) names
        them both: each caller applies its own policy, and only a ``HELD`` holder
        holds the address. Any batch reads in a bounded number of queries, and the
        lookup writes nothing. This is a system identity lookup: the caller gates
        what it reveals.
        """

        handle_model = apps.get_model("parties", "Handle")
        person_model = apps.get_model("parties", "Person")
        users = get_user_model().objects
        given = [email for email in dict.fromkeys(emails) if email.strip()]
        if not given:
            return []
        values = {email: handle_model.normalize_value(handle_model.Platform.EMAIL, email) for email in given}
        addresses = {email: users.normalize_email(email) for email in given}
        live = person_model._base_manager.filter(is_trashed=False, merged_into__isnull=True)
        account_people = self.account_people(given)
        with system_context(reason="parties.people_for_emails"):
            known = list(
                handle_model._base_manager.filter(
                    platform=handle_model.Platform.EMAIL, normalized_value__in=set(values.values())
                ).order_by("pk")
            )
            holders = self._holders(known)
            held: dict[str, tuple[Any, Any]] = {}
            for handle in known:
                if (holding := holders.get(handle.pk)) is not None and holding[1] is not None:
                    held.setdefault(handle.normalized_value, holding)
            statements = self.handle_statements()
            claims: defaultdict[str, dict[Any, Any]] = defaultdict(dict)
            for value, subject_id, handle_id in (
                statements.filter(handle__in=known, subject__in=live.values("pk"))
                .standing()
                .order_by("handle_id")
                .values_list("handle__normalized_value", "subject_id", "handle_id")
            ):
                claims[value].setdefault(subject_id, handle_id)
            claimed = {value: next(iter(found.items())) for value, found in claims.items() if len(found) == 1}
            claimants = live.in_bulk([subject_id for subject_id, _handle_id in claimed.values()])
        by_pk = {handle.pk: handle for handle in known}
        answer = []
        for email in given:
            holding = held.get(values[email], (None, None))
            if (person := account_people.get(addresses[email])) is not None:
                own_handle = holding[0] if holding[1] is not None and holding[1].pk == person.pk else None
                answer.append(EmailHolder(email, person, EmailBasis.ACCOUNT, own_handle))
            if holding[1] is not None:
                answer.append(EmailHolder(email, holding[1], EmailBasis.HELD, holding[0]))
            elif (claim := claimed.get(values[email])) is not None and claim[0] in claimants:
                answer.append(EmailHolder(email, claimants[claim[0]], EmailBasis.CLAIM, by_pk[claim[1]]))
        return answer

    def account_people(self, emails: Iterable[str]) -> dict[str, Person]:
        """Return the live Person of the one person account each address signs in with, by the account's address.

        The rule :meth:`people_for_emails` answers ``ACCOUNT`` by
        (``UserManager.person_for_email``'s: an address two accounts share names
        nobody, and an account without a Person names nobody). Keys are the
        accounts' own normalized addresses (``UserManager.normalize_email``). A
        system identity read in two queries; the caller gates what it reveals.
        """

        users = get_user_model().objects
        addresses = {users.normalize_email(email) for email in emails} - {""}
        if not addresses:
            return {}
        live = apps.get_model("parties", "Person")._base_manager.filter(is_trashed=False, merged_into__isnull=True)
        with system_context(reason="parties.account_people"):
            accounts: defaultdict[str, list[Any]] = defaultdict(list)
            for user in users.people().filter(email__in=addresses):
                accounts[user.email].append(user)
            owners = {found[0].pk: address for address, found in accounts.items() if len(found) == 1}
            return {owners[person.user_id]: person for person in live.filter(user_id__in=list(owners))}

    def lookup_emails(self, emails: Iterable[str], *, lookup: EmailLookup | None = None) -> EmailLookup:
        """Return ``lookup`` (new by default) knowing whom each of ``emails`` names and which an account signs in with.

        Only an address it has not read, or one a record of its page changed
        since (:attr:`EmailLookup.changed`), is read: its holders through
        :meth:`people_for_emails`, its lapsed holders (:meth:`lapsed_holders`),
        and whether a person account signs in with it in any spelling, the one
        given or one a handle holds, compared by iam's own key and by the
        handle's normalized one (``UserManager.sign_in_addresses``). An address
        a lapsed holder keeps is still theirs, so a claim names nobody by it:
        the claim basis is left out of its holders.
        Writes nothing; the caller reads it under the locks of the addresses it
        decides on.
        """

        lookup = EmailLookup() if lookup is None else lookup
        given = [email for email in dict.fromkeys(emails) if email.strip()]
        wanted = [email for email in given if (key := _email_key(email)) not in lookup.holders or key in lookup.changed]
        if not wanted:
            return lookup
        keys = {_email_key(email) for email in wanted}
        handle_model = apps.get_model("parties", "Handle")
        lapsed = self.lapsed_holders(handle_model.Platform.EMAIL, keys)
        lookup.holders.update((key, []) for key in keys)
        for key in keys:
            lookup.lapsed[key] = lapsed.get(key, set())
        for holder in self.people_for_emails(wanted):
            key = _email_key(holder.email)
            found = lookup.holders[key]
            if holder.basis == EmailBasis.CLAIM and lookup.lapsed[key]:
                continue
            if not any(row.basis == holder.basis and row.person.pk == holder.person.pk for row in found):
                found.append(holder)
        with system_context(reason="parties.lookup_emails"):
            spellings = {
                *wanted,
                *handle_model._base_manager.filter(
                    platform=handle_model.Platform.EMAIL, normalized_value__in=keys
                ).values_list("value", flat=True),
            }
        lookup.sign_in.difference_update(keys)
        signed_in = get_user_model().objects.sign_in_addresses(spellings, key=_email_key)
        lookup.sign_in.update(_email_key(email) for email in signed_in)
        lookup.changed.difference_update(keys)
        return lookup

    def lapsed_holders(self, platform: str, values: Collection[str]) -> dict[str, set[Any]]:
        """Return, by normalized value, the keys of the trashed people who still hold an address of ``platform``.

        Such a person (trashed, never merged away) has an applied, current
        statement of a handle of the address in some spelling: a restore makes
        them its holder again, so what a source may state about the address, and
        who may confirm it, treats them as its holder (:meth:`lookup_emails`).
        :meth:`people_for_emails` answers nobody for them and stays as it is.
        One read; ``values`` are normalized (``Handle.normalize_value``).
        """

        if not values:
            return {}
        lapsed: defaultdict[str, set[Any]] = defaultdict(set)
        with system_context(reason="parties.lapsed_holders"):
            rows = self.handle_statements().applied().filter(
                handle__platform=platform,
                handle__normalized_value__in=sorted(values),
                subject__is_trashed=True,
                subject__merged_into__isnull=True,
                subject__person__isnull=False,
            )
            for value, subject_id in rows.values_list("handle__normalized_value", "subject_id"):
                lapsed[value].add(subject_id)
        return dict(lapsed)

    def holding_blocker(self, party: Any, handle: Any, *, author: Any) -> str | None:
        """Return why ``author``'s stance may not make ``party`` the holder of ``handle``'s address, else ``None``.

        A person's stance that wins a handle (a confirmation, taking control, a
        restored confirmation) gives ``party`` the address and what is
        attributed to it. Its holders are read by the address, its platform and
        normalized value in every spelling, as the page lookup reads them: each
        live party a handle of it resolves to (through merge survivors, an
        organization included), each lapsed holder (:meth:`lapsed_holders`), and,
        for an email address, the person of the person account that signs in
        with it (:meth:`lookup_emails`). An account's sign-in address is its
        own: ``party`` may take it when it is that account's person, and nobody
        may while the account has no person. Otherwise ``author`` needs
        ``write`` on every holder but ``party``. The refusal is one message,
        whoever holds the address, so it reveals no holder ``author`` cannot read.
        """

        handle_model = apps.get_model("parties", "Handle")
        party_model = apps.get_model("parties", "Party")
        refusal = "This address cannot be given to this party."
        platform, value = handle.platform, handle.normalized_value
        with system_context(reason="parties.holding_blocker"):
            resolved = handle_model._base_manager.filter(platform=platform, normalized_value=value, party__isnull=False)
            survivors = party_model.canonical_ids(set(resolved.values_list("party_id", flat=True)))
            live = party_model._base_manager.filter(pk__in=set(survivors.values()), is_trashed=False)
            holders = set(live.values_list("pk", flat=True))
        holders |= self.lapsed_holders(platform, {value}).get(value, set())
        if platform == handle_model.Platform.EMAIL:
            lookup = self.lookup_emails([handle.value])
            accounts = {row.person.pk for row in lookup.named(handle.value) if row.basis == EmailBasis.ACCOUNT}
            if party.pk in accounts:
                return None
            if lookup.signs_in([handle.value]) and not accounts:
                return refusal
            holders |= accounts
        holders.discard(party.pk)
        rows = party_model._base_manager.filter(pk__in=holders) if holders else ()
        return refusal if any(not row.with_actor(author).has_access("write") for row in rows) else None

    def _holders(self, handles: Sequence[Any]) -> dict[Any, tuple[Any, Any | None]]:
        """Return each handle's merge survivor and the live Person holding it, by handle pk, in bounded reads.

        The survivor's party is followed to its own survivor; a party that is no
        Person, or a trashed one, holds nothing (``None``). A handle whose survivor
        has no party is absent.
        """

        handle_model = apps.get_model("parties", "Handle")
        person_model = apps.get_model("parties", "Person")
        loaded = {handle.pk: handle for handle in handles}
        redirects = handle_model.canonical_ids([handle.pk for handle in handles if handle.merged_into_id is not None])
        loaded.update(handle_model._base_manager.in_bulk(set(redirects.values()) - set(loaded)))
        survivors = {handle.pk: loaded[redirects.get(handle.pk, handle.pk)] for handle in handles}
        parties = apps.get_model("parties", "Party").canonical_ids(
            {survivor.party_id for survivor in survivors.values() if survivor.party_id is not None}
        )
        people = person_model._base_manager.filter(is_trashed=False).in_bulk(set(parties.values()))
        return {
            pk: (survivor, people.get(parties.get(survivor.party_id)))
            for pk, survivor in survivors.items()
            if survivor.party_id is not None
        }

    def contact_person(self, link: RecordLink) -> Person | None:
        """Resolve a contact through its native generic target and Person relation."""

        return self._linked_party(link, apps.get_model("parties", "Person"))

    def contact_people(self, links: Iterable[RecordLink]) -> dict[Any, Person | None]:
        """Return the live Person each link names, keyed by link pk and read once for the batch.

        A trashed person is nobody's target: it, like an unbound link, maps to
        ``None``. The one rule a directory's comparison, its absence sweep, its
        target resolution and the ingest's rebinding guard share.
        """

        return self._live_parties(links, apps.get_model("parties", "Person"))

    def _live_parties(self, links: Iterable[RecordLink], model: Any) -> dict[Any, Any | None]:
        """Return each link's untrashed ``model`` child (Person or Organization) by link pk; ``None`` while unbound."""

        links = list(links)
        prefetch_related_objects(
            links, GenericPrefetch("target", [self.model._base_manager.select_related(model._meta.model_name)])
        )
        linked = {link.pk: self._linked_party(link, model) for link in links}
        ids = [row.pk for row in linked.values() if row is not None]
        live = {row.pk: row for row in model.objects.untrashed().filter(pk__in=ids)} if ids else {}
        return {pk: live.get(row.pk) if row is not None else None for pk, row in linked.items()}

    @staticmethod
    def _linked_party(link: RecordLink, model: Any) -> Any | None:
        """Resolve ``link``'s Party target to its ``model`` child (Person or Organization); ``None`` while unbound."""

        target = link.target
        if target is None:
            if link.target_content_type_id is not None or link.target_object_id:
                raise ValidationError("The contact's linked target no longer exists.")
            return None
        child = concrete_child(target, model) if isinstance(target, apps.get_model("parties", "Party")) else None
        if child is None:
            raise ValidationError(f"A contact target must be a Party of kind {model.__name__}.")
        return child

    def holding_links(self, link: RecordLink, replaces: Sequence[RecordLink] = (), *, readmitted: bool) -> set[Any]:
        """Return the keys of ``link`` and of the links it ``replaces`` whose party stays or passes to it unadmitted.

        Such a link is bound and observing (neither tombstoned nor withdrawn)
        and, for a replaced one, of ``link``'s stream: ``link`` keeps its own
        party, and a merge at the source hands the replaced record's over. Where
        ``link`` returning from a tombstone is ``readmitted`` (a contact of a
        directory that adopts, ``DirectoryBackend.adopts_existing_people``), its
        party is a candidate admitted again (:meth:`adoption_blocker`), as is any
        other link's; elsewhere (a directory that adopts nobody, a stream that
        writes back, an organization) ``link`` keeps its bound party through a
        tombstone.
        """

        rows = (
            type(link)
            .objects.sudo(reason="parties.holding_links")
            .filter(
                pk__in=[link.pk, *(row.pk for row in replaces)],
                stream_id=link.stream_id,
                target_object_id__isnull=False,
            )
        )
        holding = set(rows.observing().values_list("pk", flat=True))
        if link.target_object_id and not readmitted:
            holding.add(link.pk)
        return holding

    def _naming_links(self, party_ids: Iterable[Any]) -> Any:
        """Return the links naming any of ``party_ids`` that have not withdrawn it, each with its stream.

        A tombstoned link still names its party: a record that returns lands
        there again once admitted, so no other contact of its source takes the
        party meanwhile.
        """

        parties = apps.get_model("parties", "Party")._base_manager.filter(pk__in=list(party_ids))
        return (
            apps.get_model("integrate", "RecordLink")
            .objects.sudo(reason="parties.naming_links")
            .filter(pk__in=parties.values("record_links"))
            .exclude(status=LinkStatus.WITHDRAWN)
            .select_related("stream")
        )

    def directory_merge_blocker(self, survivor: Party, merged: Sequence[Party]) -> str | None:
        """Return why merging ``merged`` into ``survivor`` would set two contacts of one directory on one person.

        A survivor takes every link its merged records had (a link follows its
        record's survivor). Two bound, live links of one pull-only directory,
        from different records, would put two records the source keeps
        separate on one person, and the directory's statements, keyed by its
        integration, would overwrite each other on every sync. A tombstoned or
        withdrawn link counts for nothing, and a pair already on one record is
        not this merge's. Any other merge is the merging person's to make: an
        account's own person, or a person a two-way card names, included
        (default pending the architect's ruling, O16). ``None`` when nothing
        refuses.
        """

        ids = [survivor.pk, *(row.pk for row in merged)]
        directory = apps.get_model("parties", "Directory")
        contacts: defaultdict[Any, set[str]] = defaultdict(set)
        for row in self._naming_links(ids).observing().select_related("stream__integration"):
            kind = row.stream.integration.declared_concrete_model()
            if row.stream.direction == StreamDirection.PULL and kind is not None and issubclass(kind, directory):
                contacts[row.stream.integration_id].add(row.target_object_id)
        if any(len(records) > 1 for records in contacts.values()):
            return (
                "These are two contacts of one directory, kept separate at its source: merged here, each sync would "
                "overwrite one with the other. Merge them at the source, or remove one there; once the directory "
                "has seen it gone, these two can be merged."
            )
        return None

    def adoption_blocker(
        self,
        person: Person,
        *,
        link: RecordLink,
        parsed: ParsedContact,
        lookup: EmailLookup | None = None,
        replaces: Sequence[RecordLink] = (),
    ) -> str | None:
        """Return why ``link``'s directory may not bind its contact ``parsed`` to the existing ``person``, or ``None``.

        Adopting a person is an access change: the directory becomes one of their
        statement sources and its contact readers read them. Every limit must hold
        (rulings R2 and R9), each read from rows loaded here under the contact
        locks the caller holds, never from the objects passed in: the link's
        stream is pull-only; the person is live (neither trashed nor merged away)
        and no account's own; no address the contact carries is a person
        account's sign-in address in any spelling (``lookup.sign_in``), whichever
        address found the person and whether or not the account has a person;
        the directory's owner holds ``adopt`` on them (their owner or an editor of their party:
        no platform-admin arm), checked for that user though sync runs as system
        work; no other contact of the directory names them, a tombstoned one
        included, since its statements are keyed by integration, not by link
        (the links of the records ``replaces`` folded into this one are this
        contact's own); and no live link of a stream that writes back names them,
        which would publish what this source states about them. ``lookup`` is
        the page's (:meth:`lookup_emails`) and keeps each verdict, so the target
        decision and the ingest that binds the person admit them once; without
        one the contact's addresses are looked up here.
        :meth:`ingest_contact` calls it for every person new to its link where
        the link's directory adopts (``DirectoryBackend.adopts_existing_people``).
        """

        stream = link.stream
        if stream.direction != StreamDirection.PULL:
            return "A directory that writes back adopts nobody."
        emails = [value for value, _label, _preferred in parsed.emails]
        lookup = self.lookup_emails(emails, lookup=lookup)
        if (person.pk, link.pk) in lookup.verdicts:
            return lookup.verdicts[person.pk, link.pk]
        candidate = apps.get_model("parties", "Person")._base_manager.filter(pk=person.pk).first()
        owner = stream.integration.owner
        refusal = None
        if candidate is None or candidate.is_trashed or candidate.merged_into_id is not None:
            refusal = "The person is no longer live."
        elif candidate.user_id is not None:
            refusal = "The person is a user account's own."
        elif lookup.signs_in(emails):
            refusal = "An address of the contact is a user account's sign-in address."
        elif owner is None or not candidate.with_actor(owner).has_access("adopt"):
            refusal = "The directory's owner may not adopt the person."
        else:
            naming = self._naming_links([person.pk]).exclude(pk__in=[link.pk, *(row.pk for row in replaces)])
            if any(row.stream.integration_id == stream.integration_id for row in naming):
                refusal = "Another contact of the directory is the person."
            elif any(row.stream.writes_back for row in naming.observing()):
                refusal = "A stream that writes back names the person."
        lookup.verdicts[person.pk, link.pk] = refusal
        return refusal

    def _target_is_new[M: models.Model](
        self,
        link: RecordLink,
        model: type[M],
        target: M | None,
        *,
        replaces: Sequence[RecordLink] = (),
        readmitted: bool,
    ) -> bool:
        """Return whether ``target``, the ``model`` row the caller decided ``link`` lands on, needs admission.

        Binding a link lends its party to the directory's contact readers, so a
        target must be a ``model`` row, and a link that holds a live row
        (:meth:`holding_links`) keeps it: an ingest never rebinds it. Its own row,
        or one a replaced link hands over, needs no admission; ``None`` mints
        one. Any other row is new to the link, and its verb admits it or
        refuses. Where a link returning from a tombstone is ``readmitted`` (the
        person of a contact whose directory adopts), the row it named is new to
        it too; where no rule admits a row (a directory that adopts nobody, an
        organization), the link keeps its own through a tombstone. A link whose
        row is trashed names nobody (:meth:`contact_people`).
        """

        if target is not None and not isinstance(target, model):
            raise ValidationError(f"A contact target must be a Party of kind {model.__name__}.")
        named = self._live_parties([link, *replaces], model)
        holding = self.holding_links(link, replaces, readmitted=readmitted)
        kept = named[link.pk] if link.pk in holding else None
        if kept is not None and (target is None or kept.pk != target.pk):
            raise ValidationError("A bound link keeps its target; ingestion never rebinds it.")
        return target is not None and not any(
            (row := named[pk]) is not None and row.pk == target.pk for pk in holding
        )

    def _adopted_through(self, party: Any, *, source: Any) -> Handle | None:
        """Return the handle ``source``'s identity statement on ``party`` cites: the one an adoption found them by."""

        handle_model = apps.get_model("parties", "Handle")
        statements = party.statements.model
        stated = (
            statements._base_manager.filter(
                subject_id=party.pk,
                aspect="identity",
                asserted_by=str(to_subject_ref(source)),
                status=statements.StatementStatus.ASSERTED,
                superseded__isnull=True,
            )
            .order_by("pk")
            .values_list("evidence", flat=True)
            .first()
        )
        ids = [
            ref.id
            for ref in (EvidenceReference.model_validate(value) for value in stated or ())
            if ref.model.lower() == handle_model._meta.label_lower
        ]
        found = instances_from_public_ids(handle_model, ids[:1], queryset=handle_model._base_manager.all())
        return cast("Handle", found[ids[0]].canonical()) if ids and ids[0] in found else None

    def _withdraw_link(self, link: RecordLink, *, lookup: EmailLookup | None = None) -> int:
        """Withdraw what ``link`` asserts on the person it leaves, their addresses and edges, never handing it on.

        ``RecordLink.withdraw_statements`` moves a link's assertions to another
        observing link of its source naming the same record; a contact link
        leaving a person must withdraw them instead, or they stay asserted on
        that person through the sibling. Every statement table a contact's link
        states into is a person's (consumer groups included), an address's or a
        relationship's. The email addresses it stated are marked changed in
        ``lookup``, as landing them does. Returns how many it withdrew.
        """

        stated = self._stated_emails(link)
        source = link.asserting_source()
        tables = {
            model: None
            for owner in ("Person", "Address", "Relationship")
            for model in apps.get_model("parties", owner).statement_models()
        }
        withdrawn = sum(model.objects.withdraw(source, link=link) for model in tables)
        if lookup is not None:
            lookup.changed.update(stated)
        return withdrawn

    def withdraw_contact(self, link: RecordLink, *, lookup: EmailLookup | None = None) -> int:
        """Withdraw what a contact's ``link`` states as its record leaves the source, the party kept.

        A tombstone's withdrawal (``RecordLink.withdraw_statements``: what
        another observing link of the source evidences moves there). The email
        addresses it stated are marked changed in ``lookup``, as landing them
        does, so a later record of the page reads their holders again. Returns
        how many it withdrew.
        """

        stated = self._stated_emails(link)
        withdrawn = link.withdraw_statements()
        if lookup is not None:
            lookup.changed.update(stated)
        return withdrawn

    def _stated_emails(self, link: RecordLink) -> set[str]:
        """Return the normalized email addresses ``link`` asserts: withdrawing it may change their holders."""

        email = apps.get_model("parties", "Handle").Platform.EMAIL
        statements = self.handle_statements()
        asserted = statements.filter(link=link, status=statements.model.StatementStatus.ASSERTED)
        return set(asserted.filter(handle__platform=email).values_list("handle__normalized_value", flat=True))

    def contact_handle_ids(self, contacts: Iterable[ParsedContact | ParsedOrganization]) -> set[Any]:
        """Return the handles the contacts' emails and phones name, every spelling of each (by normalized value).

        The upsert path keys on the exact value, so two spellings of one address
        can be two rows; a lock of an address's identities covers them all.
        """

        handle_model = apps.get_model("parties", "Handle")
        values: defaultdict[str, set[str]] = defaultdict(set)
        for contact in contacts:
            for platform, points in (("email", contact.emails), ("phone", contact.phones)):
                values[platform].update(handle_model.normalize_value(platform, item[0]) for item in points)
        if not any(values.values()):
            return set()
        spelled = Q(pk__in=[])
        for platform, normalized in values.items():
            spelled |= Q(platform=platform, normalized_value__in=sorted(normalized))
        with system_context(reason="parties.contact_handles"):
            return set(handle_model.objects.filter(spelled).values_list("pk", flat=True))

    def lock_contact(
        self,
        *parties: Any | None,
        parsed: ParsedContact | ParsedOrganization | None = None,
    ) -> None:
        """Lock the mapped contact aggregates of ``parties`` before a caller rechecks or lands one.

        Follow the shared Handle, Party, PartyStatement order (the handles the
        parties' statements and ``parsed`` name first, every spelling of each:
        :meth:`contact_handle_ids`) before locking each Person, Organization or
        Party row given and its mapped collections. The parent row lock also
        fences inserts through its foreign keys while the page transaction is
        open. ``None`` entries are skipped.
        """

        associations = apps.get_model("parties", "Party").objects
        rows = sorted((party for party in parties if party is not None), key=lambda row: row.pk)
        ids = [row.pk for row in rows]
        handle_ids = (
            set(associations.handle_statements().filter(subject_id__in=ids).values_list("handle_id", flat=True))
            if ids
            else set()
        )
        if parsed is not None:
            handle_ids.update(self.contact_handle_ids([parsed]))
        associations.lock_identity_rows(party_ids=ids, handle_ids=handle_ids)
        if not rows:
            return
        employee = apps.get_model("parties", "RelationshipKind").employee()
        for row in rows:
            list(type(row).objects.filter(pk=row.pk).order_by("pk").lock_if_supported())
        list(apps.get_model("parties", "Address").objects.filter(party_id__in=ids).order_by("pk").lock_if_supported())
        list(
            apps.get_model("parties", "Relationship")
            .objects.filter(party_id__in=ids, kind=employee)
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
        employee = apps.get_model("parties", "RelationshipKind").employee()
        employment = {
            edge.party_id: edge
            for edge in (
                apps.get_model("parties", "Relationship")
                .objects.canonical()
                .filter(party_id__in=ids, kind=employee, other_party__isnull=True)
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

    def prepare_contact[V: ParsedContact | ParsedOrganization](self, parsed: V) -> V:
        """Take a fetched contact's or organization's stored form before contact locks.

        Addresses take their stored form (:meth:`AddressManager.as_stored`), so the
        projection both sync sides compare reads a source's "USA" as the stored
        "US"; an organization has nothing else to prepare. A contact's photo is
        stored as the person's avatar (``File.objects.ingest_image``, which refuses
        a non-image or oversized photo) and keeps its content address.
        """

        addresses = apps.get_model("parties", "Address").objects
        parsed = replace(parsed, addresses=tuple(addresses.as_stored(address) for address in parsed.addresses))
        if not isinstance(parsed, ParsedContact):
            return parsed
        photo = parsed.photo
        if photo is None or photo.content_hash:
            return parsed
        if photo.uri:
            raise ValidationError("Contact photo URIs must be resolved before preparation.")
        if not photo.data:
            return replace(parsed, photo=None)
        avatar = apps.get_model("storage", "File").objects.ingest_image(photo.data)
        mime: Any = avatar.mime_type
        return replace(
            parsed,
            photo=ParsedPhoto(content_hash=avatar.content_hash, mime=mime.mime_type if mime is not None else ""),
        )

    def resolve_contact_photo(self, photo: ParsedPhoto | None) -> Any:
        """Resolve a prepared photo to its stored avatar image, validated under its row lock.

        Only the image drive holds avatars (``File.objects.images``), so a photo's
        content address never resolves to a user's file with the same bytes.
        """

        if photo is None:
            return None
        if not photo.content_hash or photo.data is not None or photo.uri:
            raise ValidationError("Contact photos must have a prepared content address.")
        avatar = (
            apps.get_model("storage", "File")
            .objects.images()
            .filter(content_hash=photo.content_hash, mime_type__mime_type=photo.mime)
            .lock_if_supported()
            .order_by("pk")
            .first()
        )
        if avatar is None:
            raise ValidationError("The prepared contact photo is no longer available.")
        return avatar

    def _land_handles(
        self,
        party: Any,
        parsed: ParsedContact | ParsedOrganization,
        *,
        display_name: str,
        source: Any,
        link: Any,
        created_by_id: Any,
        replaces: Sequence[Any] = (),
        through: Handle | None = None,
        claims: Collection[tuple[str, str]] = (),
    ) -> set[str]:
        """Assert the emails and phones ``link`` names for ``party``; withdraw the others it or ``replaces`` asserted.

        Each handle dedupes on its value and keeps the label and preference the
        source gives it, and the name the source gives its party (``display_name``;
        a blank one keeps the handle's); the Party row is the assertion's evidence.
        An email spelling ``through``'s address lands on that handle, the one an
        adoption found ``party`` by, never on a second row of another spelling.
        An email or phone whose ``(platform, normalized value)`` is in ``claims``
        is someone else's (:meth:`_contested_addresses`): it is stated as a claim for review, below
        ``Party.REVIEW_CONFIDENCE``, so it never resolves the handle, and the
        handle's display fields stay its holder's. ``replaces`` are the links of
        records a merge at the source folded into this one: a handle only they
        asserted on ``party`` is withdrawn, never moved. Returns the normalized
        email addresses it stated or withdrew, whose holders may have changed.
        """

        handle_model = apps.get_model("parties", "Handle")
        handles = handle_model.objects
        landed = set(
            self.handle_statements()
            .filter(
                subject_id=party.pk,
                link__in=[link, *replaces],
                status=self.handle_statements().model.StatementStatus.ASSERTED,
                handle__platform=handle_model.Platform.EMAIL,
            )
            .values_list("handle__normalized_value", flat=True)
        )
        supported = []
        for platform, points in (("email", parsed.emails), ("phone", parsed.phones)):
            for value, label, preferred in points:
                normalized = handle_model.normalize_value(platform, value)
                if through is not None and platform == through.platform and normalized == through.normalized_value:
                    value = through.value
                claim = (platform, normalized) in claims
                shown = {} if claim else {"display_name": display_name, "label": label, "is_preferred": preferred}
                handle = handles.upsert(platform=platform, value=value, created_by_id=created_by_id, **shown)
                mapped = {} if claim else {"label": label, "is_preferred": preferred}
                dirty = [name for name, value in mapped.items() if getattr(handle, name) != value]
                if dirty:
                    for name in dirty:
                        setattr(handle, name, mapped[name])
                    handle.save(update_fields=(*dirty, "updated_at"))
                confidence = self.model.CLAIM_CONFIDENCE if claim else 1.0
                self.assert_handle(
                    party, handle, asserted_by=source, link=link, evidence=[party], confidence=confidence
                )
                supported.append({"subject": party, "aspect": "handle", "handle": handle})
                if platform == handle_model.Platform.EMAIL:
                    landed.add(normalized)
        for stating in (link, *replaces):
            party.statements.withdraw_unsupported(source, supported, aspect="handle", link=stating)
        return landed

    def _contested_addresses(
        self, person: Person, parsed: ParsedContact, *, source: Any, lookup: EmailLookup
    ) -> tuple[set[tuple[str, str]], list[tuple[Any, Handle | None]]]:
        """Return the contact's addresses ``source`` may only claim for ``person``, and the parties they name instead.

        Claims are ``(platform, normalized value)`` pairs. A phone number names one
        account as an email does (``Handle.identifies_account``): one another live
        party is linked to (:meth:`same_person_links` with ``every_address``: a
        holding, or a standing statement by a source that states its own record)
        is theirs unless ``person`` holds it, so it is a claim and each such party
        comes back with its handle; a number four or more parties are linked to
        is still only claimed, and pairs nobody.

        What a contact may state about an email depends on whom ``lookup``
        (:meth:`lookup_emails`: :meth:`people_for_emails` on any basis, and the
        addresses a person account signs in with) finds by it. One that names a
        person other than ``person`` is theirs, unless ``person`` holds it too:
        stating it in full would move it, and what is attributed to it, away from
        them, so it is a claim for review, and each such person comes back once,
        with the handle that names them, to be proposed as ``person``'s same
        person. An account's sign-in address is the account's, whether or not it
        has a person yet, unless that person is ``person``: a claim too. So is an
        address a lapsed holder keeps (trashed, its statement of it current:
        :meth:`lapsed_holders`), which a restore gives back to them; they are
        not proposed. One ``source`` already claims on ``person`` stays a claim
        until ``person`` holds it through another statement or a person confirms
        the claim (either makes ``person`` its holder), so no lapse of its holder
        (trashed, or its statement withdrawn) and no second claimant turns the
        claim into a holding; the same holds for a number. Any other address is
        stated in full. Values are normalized (``Handle.normalize_value``).
        """

        handle_model = apps.get_model("parties", "Handle")
        email, phone = handle_model.Platform.EMAIL, handle_model.Platform.PHONE
        claimed = set(
            self.handle_statements()
            .filter(
                subject_id=person.pk,
                asserted_by=str(to_subject_ref(source)),
                status=self.handle_statements().model.StatementStatus.ASSERTED,
                confidence__lt=self.model.REVIEW_CONFIDENCE,
                handle__platform__in=(email, phone),
            )
            .values_list("handle__platform", "handle__normalized_value")
        )
        claims: set[tuple[str, str]] = set()
        others: dict[Any, tuple[Any, Handle | None]] = {}
        for value, _label, _preferred in parsed.emails:
            normalized = handle_model.normalize_value(email, value)
            found = lookup.named(value)
            if any(holder.basis == EmailBasis.HELD and holder.person.pk == person.pk for holder in found):
                continue
            rivals = [holder for holder in found if holder.person.pk != person.pk]
            account = normalized in lookup.sign_in and not any(
                holder.basis == EmailBasis.ACCOUNT and holder.person.pk == person.pk for holder in found
            )
            if rivals or account or lookup.lapsed.get(normalized) or (email, normalized) in claimed:
                claims.add((email, normalized))
            for holder in rivals:
                others.setdefault(holder.person.pk, (holder.person, holder.handle))
        numbers = {(phone, handle_model.normalize_value(phone, value)) for value, _label, _preferred in parsed.phones}
        numbers = {address for address in numbers if address[1]}
        if numbers:
            linked = self.same_person_links(addresses=numbers, every_address=True)
            held = set(
                handle_model._base_manager.filter(
                    platform=phone,
                    normalized_value__in=sorted(value for _platform, value in numbers),
                    party_id=person.pk,
                ).values_list("platform", "normalized_value")
            )
            rivals_by_address = {
                address: [link for link in links if link.party != person.pk] for address, links in linked.items()
            }
            parties = apps.get_model("parties", "Party")._base_manager.in_bulk(
                sorted({link.party for links in rivals_by_address.values() for link in links})
            )
            handles = handle_model._base_manager.in_bulk(
                sorted({link.handle for links in rivals_by_address.values() for link in links})
            )
            for address in sorted(numbers):
                linked_others = rivals_by_address.get(address, [])
                if address in held:
                    continue
                if linked_others or address in claimed:
                    claims.add(address)
                for link in linked_others:
                    others.setdefault(link.party, (parties[link.party], handles[link.handle]))
        return claims, list(others.values())

    def ingest_contact(
        self,
        parsed: ParsedContact,
        *,
        stream: Any,
        created_by_id: Any,
        link: Any,
        target: Person | None = None,
        replaces: Sequence[Any] = (),
        through: Handle | None = None,
        lookup: EmailLookup | None = None,
    ) -> Any:
        """Assert one link's contact facts; preserve other sources and person preferences.

        The link owns the contact's source identity (its external key) and what the
        source sent. ``stream`` is the link's contacts stream as the page holds it,
        so its container (the address book, whose folder belongs to the same
        directory) and integration resolve once per page. ``target`` is the person
        the caller decided the contact is, ``None`` to mint one; a link holding a
        live person keeps it (:meth:`_target_is_new`). A person new to the link
        is refused unless its directory adopts
        (``DirectoryBackend.adopts_existing_people``, off by default), and then
        binds only as adoption admits it (:meth:`adoption_blocker`), under the
        contact's locks, taken first. ``through`` is the handle an adoption
        found ``target`` by: the identity statement cites it as evidence,
        restated on every later record, and the contact's spelling of its address
        lands on it. On a pull-only stream, whether or not its directory adopts,
        ``lookup`` (the page's, :meth:`lookup_emails`; else one read here) says
        who each address names: an email another person is linked to (holds it,
        claims it, keeps it as a lapsed holder, or signs in with it) is only
        claimed, for review, and each such live person is proposed here as the
        contact's same person (:meth:`_contested_addresses`; a phone number
        another live party is linked to likewise): through the one
        same-person verb, citing the handle that names them
        (:meth:`propose_same_person_as_system`, once the page commits), or, for an
        account's sign-in address no handle of theirs carries, which links no
        one to it, through :meth:`propose_directory_match`; so a
        contact never moves an address away from anyone; the addresses it lands
        are marked changed in the lookup. A link leaving a person
        withdraws what it stated on them before it binds its new person (the
        addresses it withdrew are read again before the contact's are judged),
        and,
        when the person it leaves is trashed, the proposals pairing them with
        that person, or with whom its addresses now name. ``replaces`` are
        the links of records a merge at the source folded into this one: whatever
        they stated on the person that this record restates moves to ``link``
        (statements are keyed by source, subject and aspect), and the rest is
        withdrawn with this link's own unsupported handles, addresses and
        employers. Addresses land through :meth:`AddressManager.land_addresses`
        and the employer through :meth:`RelationshipManager.land_employment`;
        removed members withdraw only this source's statements instead of
        deleting rows.
        """
        container = stream.container if stream.container_id is not None else None
        folder = container.target if container is not None else None
        folders = apps.get_model("parties", "Folder")
        if (
            link.stream_id != stream.pk
            or not isinstance(folder, folders)
            or folder.directory_id != stream.integration_id
        ):
            raise ValidationError("Contact ingestion requires a link of its directory's address-book stream.")
        parsed = self.prepare_contact(parsed)
        people = apps.get_model("parties", "Person").objects
        source = stream.integration
        pull = stream.direction == StreamDirection.PULL
        # The folder belongs to the stream's own directory, checked above.
        adopts = pull and cast("Directory", folder.directory).backend.adopts_existing_people()
        with transaction.atomic():
            self.lock_contact(target, parsed=parsed)
            if adopts:
                lookup = self.lookup_emails((value for value, _label, _preferred in parsed.emails), lookup=lookup)
            new = self._target_is_new(link, people.model, target, replaces=replaces, readmitted=adopts)
            if new and target is not None:
                if not adopts:
                    raise ValidationError(
                        "This directory binds no existing person: a contact lands on its own person, on the person "
                        "of a record it replaced, or on a new one."
                    )
                blocker = self.adoption_blocker(target, link=link, parsed=parsed, lookup=lookup, replaces=replaces)
                if blocker is not None:
                    raise ValidationError(blocker)
            person = target or people.create(
                display_name=(parsed.display_name or parsed.family_name or people.model.PLACEHOLDER_NAME),
                created_by_id=created_by_id,
            )
            parent = apps.get_model("parties", "Party")._base_manager.get(pk=person.pk)
            through = through.canonical() if through is not None else self._adopted_through(parent, source=source)
            left = link.target if link.target_object_id and link.target_object_id != str(parent.pk) else None
            gone = left is not None and self.contact_people([link])[link.pk] is None
            if left is not None:
                # What the link stated on the person it leaves goes first.
                self._withdraw_link(link, lookup=lookup)
            apps.get_model("integrate", "RecordLink").objects.bind_target(link, parent)
            for subject, aspect, values, evidence in (
                (
                    parent,
                    "identity",
                    {"display_name": (parsed.display_name or parsed.family_name or people.model.PLACEHOLDER_NAME)},
                    [through] if through is not None else [],
                ),
                (
                    parent,
                    "contact",
                    {"notes": parsed.notes, "avatar": self.resolve_contact_photo(parsed.photo)},
                    [],
                ),
                (
                    person,
                    "name",
                    {name: getattr(parsed, name) for name in person.statements.model.statement_aspects["name"].fields},
                    [],
                ),
                (person, "dates", {"birthday": parsed.birthday, "anniversary": parsed.anniversary}, []),
            ):
                subject.statements.set(
                    aspect=aspect,
                    values=values,
                    asserted_by=source,
                    link=link,
                    evidence=[EvidenceReference.of(row) for row in evidence],
                )
            if pull:
                # What the link withdrew from the person it left may have been all that held an address.
                lookup = self.lookup_emails((value for value, _label, _preferred in parsed.emails), lookup=lookup)
            claims, others = (
                self._contested_addresses(person, parsed, source=source, lookup=lookup)
                if pull and lookup is not None
                else (set(), [])
            )
            landed = self._land_handles(
                parent,
                parsed,
                display_name=parsed.display_name,
                source=source,
                link=link,
                created_by_id=created_by_id,
                replaces=replaces,
                through=through,
                claims=claims,
            )
            if lookup is not None:
                lookup.changed.update(landed)
            for other, handle in others:
                if handle is not None:
                    # Linked to one address: the same-person verb proposes the pair once this page commits.
                    self.propose_same_person_as_system(
                        person, other, evidence=[handle], reason="parties.directory.contact_claim"
                    )
                else:
                    # An account's sign-in address that no handle of theirs carries links no one to it.
                    self.propose_directory_match(person, other, evidence=[link])
            if gone:
                # What the link proposed for the trashed person it left is moot: its new person is, or is
                # proposed as, them. A live person it left (refused on its return) stays proposed.
                for other in (person, *(row for row, _handle in others)):
                    self.withdraw_directory_match(left, other)
            apps.get_model("parties", "Address").objects.land_addresses(
                person,
                parsed.addresses,
                asserted_by=source,
                link=link,
                created_by_id=created_by_id,
                replaces=replaces,
            )
            apps.get_model("parties", "Relationship").objects.land_employment(
                person,
                [Employment(parsed.organization, parsed.title, parsed.role)] if parsed.organization else [],
                created_by_id=created_by_id,
                asserted_by=source,
                link=link,
                replaces=replaces,
            )
            person.refresh_from_db()
            return person

    def ingest_organization(
        self,
        parsed: ParsedOrganization,
        *,
        stream: Any,
        link: Any,
        created_by_id: Any,
        target: Organization | None = None,
    ) -> Any:
        """Assert one link's organization facts; preserve other sources and person decisions.

        The organization counterpart of :meth:`ingest_contact`, for a directory
        stream whose records are organizations. ``stream`` is the link's stream as
        the page holds it: its integration's kind is read from the row in hand
        (``Integration.declared_concrete_model``). ``target`` is the organization
        the caller decided the record is, ``None`` to mint one; a link holding a
        live organization keeps it, through a tombstone too, since no rule
        admits it again (:meth:`_target_is_new`). Until matching an
        existing organization is ruled, any other organization is refused: binding
        one would lend it to the directory's contact readers and make the source
        one of its statement sources with no admission rule. The record's locks
        are taken before that decision, as a contact's are. The
        link is bound before the facts are stated: identity, the legal identity,
        handles and addresses, each withdrawing this link's statements it no
        longer supports. The legal identity is stated whole (an omitted field is
        stated empty, so legal name and domain move together) when the source
        carries either field, and withdrawn when it carries neither, so the
        sources that know it keep it.
        """

        kind = stream.integration.declared_concrete_model()
        if link.stream_id != stream.pk or kind is None or not issubclass(kind, apps.get_model("parties", "Directory")):
            raise ValidationError("Organization ingestion requires a link of its directory's stream.")
        parsed = self.prepare_contact(parsed)
        organizations = apps.get_model("parties", "Organization").objects
        name = parsed.display_name or parsed.legal_name
        source = stream.integration
        with transaction.atomic():
            self.lock_contact(target, parsed=parsed)
            if self._target_is_new(link, organizations.model, target, readmitted=False):
                raise ValidationError("Organizations are not matched yet: a record binds its own or a new one.")
            organization: Any = target or organizations.create(
                display_name=name or organizations.model.PLACEHOLDER_NAME, created_by_id=created_by_id
            )
            parent = apps.get_model("parties", "Party")._base_manager.get(pk=organization.pk)
            apps.get_model("integrate", "RecordLink").objects.bind_target(link, parent)
            parent.statements.set(
                aspect="identity",
                values={"display_name": name or organizations.model.PLACEHOLDER_NAME},
                asserted_by=source,
                link=link,
            )
            legal = organization.statements.model.statement_aspects["legal_identity"].fields
            if any(getattr(parsed, field) for field in legal):
                organization.statements.set(
                    aspect="legal_identity",
                    values={field: getattr(parsed, field) for field in legal},
                    asserted_by=source,
                    link=link,
                )
            else:
                organization.statements.withdraw_unsupported(source, [], aspect="legal_identity", link=link)
            self._land_handles(
                parent, parsed, display_name=name, source=source, link=link, created_by_id=created_by_id
            )
            apps.get_model("parties", "Address").objects.land_addresses(
                organization, parsed.addresses, asserted_by=source, link=link, created_by_id=created_by_id
            )
            organization.refresh_from_db()
            return organization


def _user_display_name(user: Any) -> str:
    """Return a human display name for a user's Person from the native account, else its stable id."""

    for candidate in (user.get_full_name(), user.get_username(), user.email):
        if text := (candidate or "").strip():
            return text
    return str(user.pk)


class Employment(NamedTuple):
    """One employer a source names for a person, as :meth:`RelationshipManager.land_employment` lands it.

    ``employer`` is the counterparty: a tracked Organization, or the free-text
    name of one that is not tracked (``Relationship.other_name``). ``title`` and
    ``notes`` are the edge's own (a vCard ``TITLE`` and ``ROLE``).
    """

    employer: Organization | str
    title: str = ""
    notes: str = ""


class RelationshipManager[RelationshipT: Relationship](StatementSubjectManager[RelationshipT]):
    """Own imported connection edges and their source statements."""

    def _connection_kind(self, slug: str, kinds: dict[Any, Any]) -> Any:
        """Return a required relationship kind, resolved once per batch."""

        if slug not in kinds:
            kind_model = self.model._meta.get_field("kind").related_model
            try:
                kinds[slug] = kind_model.objects.get(slug=slug)
            except kind_model.DoesNotExist as error:
                raise ValidationError(
                    f"Social connection import requires the {slug!r} RelationshipKind master row."
                ) from error
        return kinds[slug]

    def land_acquaintance(
        self,
        anchor_party: Any,
        person: Any,
        *,
        connected_at: date | datetime | None,
        kinds: dict[Any, Any],
        created_by_id: Any,
        asserted_by: Any,
        link: Any,
        evidence: Any,
    ) -> None:
        """Record the source account's idempotent connection to ``person``."""

        if anchor_party.pk == person.pk:
            return
        edge, _ = self.get_or_create(
            party=anchor_party,
            other_party=person,
            kind=self._connection_kind("acquaintance", kinds),
            defaults={"created_by_id": created_by_id},
        )
        edge.statements.set(
            aspect="relationship",
            asserted_by=asserted_by,
            link=link,
            evidence=[EvidenceReference.of(evidence)],
            values={
                "other_name": "",
                "title": "",
                "started_at": connected_at.date() if isinstance(connected_at, datetime) else connected_at,
                "ended_at": None,
                "notes": "",
            },
        )

    def land_employment(
        self,
        person: Any,
        employers: Iterable[Employment],
        *,
        created_by_id: Any,
        asserted_by: Any,
        link: Any,
        evidence: Iterable[Any] = (),
        kinds: dict[Any, Any] | None = None,
        replaces: Sequence[Any] = (),
    ) -> None:
        """State the employers ``link`` names for ``person``; withdraw the link's edges it no longer names.

        An organization is the edge's tracked counterparty, one edge per pair
        (``uq_relationship_edge``) that every source naming it states, taken with
        an atomic get-or-create on that key. A free-text name is a value of the
        link's own edge, not its identity: a name the link already states keeps its
        edge, a new name renames in place an edge whose name the link no longer
        states, and only then is an edge created, so a source keeps one edge per
        employer it names. An employer named twice lands once, as its first
        naming states it. A dropped employer loses only this link's statement;
        rows are never deleted. Landing an employer first locks the person's
        identity rows (``Party.lock_subject``, the shared Handle, Party order),
        whatever the caller holds; a call naming none only withdraws, which locks
        each edge it changes. ``evidence`` names the records supporting the
        claims; ``kinds`` caches the employee kind across a batch. ``replaces`` are
        the links of records a merge at the source folded into ``link``'s: their
        edges are the link's own for reuse, and one it no longer names is withdrawn.
        """

        kinds = {} if kinds is None else kinds
        if "employee" not in kinds:
            kinds["employee"] = self.model._meta.get_field("kind").related_model.employee()
        kind = kinds["employee"]
        named: dict[Any, Employment] = {}
        for employment in employers:
            if not employment.employer:
                raise ValidationError("An employer is an organization or a non-empty name.")
            employer = employment.employer
            named.setdefault(employer if isinstance(employer, str) else ("organization", employer.pk), employment)
        employers = list(named.values())
        if kind is None:
            if employers:
                raise ValidationError("Employment requires the employee RelationshipKind master row.")
            return  # Without the kind no employment edge exists to withdraw.
        if employers:
            person.lock_subject()
        stating = (link, *replaces)
        stated = dict(
            self.model.statements.current()
            .filter(link__in=stating, subject__party_id=person.pk, subject__kind=kind)
            .values_list("subject_id", "other_name")
        )
        names = {employment.employer for employment in employers if isinstance(employment.employer, str)}
        # Only a free-text name reuses a stated edge row; a withdrawal needs the keys alone.
        edges = self.model._base_manager.in_bulk(list(stated)) if names else {}
        landed: dict[int, Any] = {}
        for index, employment in enumerate(employers):
            organization = employment.employer
            if not isinstance(organization, str):
                landed[index], _ = self.model._base_manager.get_or_create(
                    party_id=person.pk,
                    other_party=organization,
                    kind=kind,
                    defaults={"created_by_id": created_by_id},
                )
        # A free-text name takes an edge the link stated by name: its own first, then one to rename in place.
        claimed = {edge.pk for edge in landed.values()}
        pool = [edges[pk] for pk in sorted(edges) if stated[pk] and pk not in claimed]
        own: dict[str | None, Any] = {}
        for edge in pool:
            own.setdefault(stated[edge.pk], edge)
        spare = [edge for edge in pool if stated[edge.pk] not in names or own[stated[edge.pk]] is not edge]
        for index, employment in enumerate(employers):
            name = employment.employer
            if isinstance(name, str):
                landed[index] = (
                    own.pop(name, None)
                    or (spare.pop(0) if spare else None)
                    or self.create(party_id=person.pk, kind=kind, other_name=name, created_by_id=created_by_id)
                )
        refs = [EvidenceReference.of(row) for row in evidence]
        for index, employment in enumerate(employers):
            landed[index].statements.set(
                aspect="relationship",
                asserted_by=asserted_by,
                link=link,
                evidence=refs,
                values={
                    "other_name": employment.employer if isinstance(employment.employer, str) else "",
                    "title": employment.title,
                    "notes": employment.notes,
                    "started_at": None,
                    "ended_at": None,
                },
            )
        if dropped := sorted(set(stated) - {edge.pk for edge in landed.values()}):
            for each in stating:
                self.model.statements.withdraw(asserted_by, dropped, link=each)


class FolderManager[FolderT: Folder](AngeeHistoryManager[FolderT]):
    """Own address-book folder ingest and the name its source states."""

    def ingest_addressbook(self, link: Any, *, name: str) -> FolderT:
        """State an address book's name on the folder its link targets, creating it first.

        ``link`` is the address book's identity in its directory's ``addressbooks``
        stream. A new folder belongs to that directory and its owner; the link is
        bound before the statement so the directory is admitted to state it.
        Survivorship applies the name, so a person's rename keeps outranking it.
        A name longer than the folder's is truncated, never refused. Database
        work only, inside the stream's page transaction.
        """

        name = name[: self.model._meta.get_field("name").max_length]
        integration = link.stream.integration
        folder = link.target if isinstance(link.target, self.model) else None
        with transaction.atomic():
            if folder is None:
                folder = self.create(name=name, directory_id=integration.pk, created_by_id=integration.owner_id)
                apps.get_model("integrate", "RecordLink").objects.bind_target(link, folder)
            folder.statements.set(aspect="identity", values={"name": name}, asserted_by=integration, link=link)
            return folder


class DirectoryManager[DirectoryT: Directory](IntegrationManager[DirectoryT]):
    """Connect directories: the one sequence every vendor's connect verb runs."""

    def connect(
        self,
        owner: Any,
        *,
        backend_class: str,
        display_name: str,
        credential_kind: str,
        material: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> DirectoryT:
        """Connect ``owner``'s account at a contact source, tested before anything is saved, and queue its first sync.

        Called outside any transaction, since it reaches the network, and refused
        inside one. ``owner`` must hold ``create`` on a directory
        (``parties/directory``), checked first. ``backend_class`` is resolved
        next, an unknown one refused as itself. ``config`` is what a person
        submits, so this is where a directory's configuration is edited: it is
        validated strictly against the backend's config model, and a setting the
        model does not know is a ``ValidationError`` naming it (a save that
        edits nothing keeps what is stored, and a reader ignores what it does
        not know). A backend whose seeded vendor row is not loaded yet is an
        operator refusal that says to load resources; any configuration error
        building the unsaved credential (``credential_kind`` and its secret
        ``material``) and directory then stays itself. The backend admits the credential and tests the connection
        (``DirectoryBackend.test_connection``) outside any transaction, and its
        refusal is a ``ValidationError`` carrying the backend's own operator
        message, so a directory that could never sync is never saved. Then one
        transaction, as system work, creates the directory (its vendor is the
        backend's seeded one, ``IntegrationManager.build``) and a credential of
        its own, named after the backend, ``display_name`` and the directory's id
        so no connection or reconnect ever reuses one, connects the directory
        with it (``Integration.attach_connection``, which enters connection
        discovery), grants the owner the read of its contacts
        (``Directory.grant_owner_contact_read``) and claims the address the account signs in as as the owner's
        own handle (``DirectoryBackend.account_address``); held by someone the
        owner may not take it from, it is only claimed for review, and the
        connection stands. Once that commits, the
        first sync is requested if the directory is ready for work; a backend
        whose discovery is pending gets its first sync from the discovery's
        completion. A failure to queue it is logged, and the directory stays
        connected for its next scheduled sync. A vendor's connect mutation calls
        this; it has no GraphQL of its own.
        """

        if connection.in_atomic_block:
            raise RuntimeError("Connecting a directory runs outside any transaction: its connection test is remote.")
        resource_type = model_resource_type(self.model)
        if resource_type is None or not check_new(
            subject=to_subject_ref(owner), action="create", resource_type=resource_type
        ).allowed:
            raise PermissionDenied("This account may not connect a directory.")
        credentials = apps.get_model("integrate", "Credential").objects
        impl = self.model._meta.get_field("backend_class").resolve_class(backend_class)
        submitted = dict(config or {})
        impl.parse_config(submitted)
        if impl.config_model is not None:
            try:
                impl.config_model.model_validate(submitted, extra="forbid")
            except PydanticValidationError as error:
                unknown = sorted(".".join(map(str, issue["loc"])) for issue in error.errors(include_url=False))
                raise ValidationError({"config": f"Unknown settings: {', '.join(unknown)}."}) from None
        if not self._vendor_loaded(impl):
            # The backend's vendor is a seeded row (its master resource), absent until resources load.
            raise ValidationError(
                "This directory's vendor is not loaded on this server: run `manage.py resources load`, "
                "then connect again."
            )
        fields = {
            "backend_class": backend_class,
            "owner": owner,
            "display_name": display_name,
            "config": submitted,
            "created_by_id": owner.pk,
        }
        candidate = self.build(**fields, lifecycle=IntegrationLifecycle.CONNECTED)
        name = f"{impl.label} — {display_name}"
        unsaved = credentials.prepare_local_credential(owner, kind=credential_kind, name=name, material=material)
        candidate.attach_connection(unsaved)
        try:
            candidate.test_connection()
        except IntegrationError as error:
            raise ValidationError(error.public_message) from error
        with system_context(reason="parties.directory.connect"), transaction.atomic():
            directory = self.build(**fields, lifecycle=IntegrationLifecycle.DISCONNECTED)
            directory.save()
            suffix = f" ({directory.sqid})"
            limit = cast(int, credentials.model._meta.get_field("name").max_length) - len(suffix)
            credential = credentials.create_local_credential(
                owner, kind=credential_kind, name=f"{name[:limit]}{suffix}", material=material
            )
            directory.attach_connection(credential)
            directory.grant_owner_contact_read()
            if address := directory.backend.account_address():
                handles = apps.get_model("parties", "Handle").objects
                handles.claim_control(
                    owner,
                    platform=handles.model.Platform.for_value(address),
                    value=address,
                    asserted_by=owner,
                    evidence=[directory],
                    display_name=display_name,
                )
        if directory.discovery_ready:
            try:
                directory.request_sync()
            except Exception:  # noqa: BLE001 -- the committed directory stays connected; its next scheduled sync runs.
                logger.warning("The first sync of directory %s could not be queued.", directory.pk, exc_info=True)
        return directory

    def grant_owner_contact_reads(self) -> tuple[int, int]:
        """Grant every directory's owner the read of its contacts where missing; return ``(granted, held)``.

        The provision repair for directories connected before connecting granted
        it (``Directory.grant_owner_contact_read``, the one writer). Idempotent:
        a second run grants nothing. A directory without an owner is skipped.
        """

        granted = held = 0
        with system_context(reason="parties.directory.owner_contact_reads"):
            for directory in self.model._base_manager.filter(owner__isnull=False).order_by("pk"):
                if directory.grant_owner_contact_read():
                    granted += 1
                else:
                    held += 1
        return granted, held

    def _vendor_loaded(self, impl: Any) -> bool:
        """Whether the seeded vendor row the backend ``impl``'s defaults name is loaded (or it names none)."""

        slug = impl.effective_defaults().get("vendor")
        vendors = self.model._meta.get_field("vendor").related_model
        with system_context(reason="parties.directory.connect"):
            return not isinstance(slug, str) or vendors._base_manager.filter(slug=slug).exists()
