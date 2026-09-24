"""Managers that own the directory-sync write path for parties.

A directory backend parses a source into neutral ``ParsedContact`` rows; these
managers turn one into a ``Party`` (a ``Person``) and its ``Handle`` /
``PartyHandle`` / ``Address`` rows. A contact is keyed by its source UID within
its folder (the idempotent ``(folder, source_uid)`` upsert), handles dedupe on
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
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import combinations
from typing import Any, Self, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery, TextField, Value, When
from django.db.models.functions import Coalesce, NullIf
from phonenumbers import (
    NumberParseException,
    PhoneNumberMatcher,
    is_possible_number,
    is_valid_number,
    parse,
)
from rebac import PermissionDenied, actor_context, current_actor, system_context

from angee.base.identity import public_id_for
from angee.base.mixins import HierarchyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_model
from angee.base.scoping import read_scoped_queryset
from angee.base.serialization import canonical_json_sha256
from angee.parties.backends import ParsedAddress, ParsedContact, ParsedPhoto
from angee.parties.domains import GENERIC_EMAIL_DOMAINS
from angee.parties.mixins import LinkSource, ScoredLinkMixin
from angee.storage.models import UploadState

_SIGNATURE_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d(?:[\d \t()./\-]*\d)?(?!\w)")


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

    def owned_by(self, user: Any) -> Self:
        """Return the handles this user controls — the ``owner`` column, no joins."""

        return self.filter(owner=user)


class HandleManager(AngeeManager.from_queryset(HandleQuerySet)):  # type: ignore[misc]
    """Factory + upsert for handles (the contact-point write path)."""

    def renormalize_phone_values(self) -> int:
        """Repair stored phone comparison values after normalization rules change.

        The pass is idempotent and collision-safe because ``normalized_value`` is a
        comparison projection rather than a uniqueness key: equal E.164 results are
        deliberately retained on their distinct source handles for duplicate review.
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
        on every hit; blank values never clobber.
        """

        if "owner" in fields or "owner_id" in fields:
            raise TypeError("Handle control ownership must be written through claim_own().")
        if "normalized_value" in fields:
            raise TypeError("Handle.normalized_value is maintained by Handle.save().")
        normalized_value = self.model.normalize_value(platform, value)
        external_id = str(fields.get("external_id") or "")
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

        ``metadata`` merges key-wise instead of replacing: several producers
        stamp independent evidence on one handle (a message importer's identity
        confidence, the connections owner's provenance), and a whole-dict write
        from one would silently erase the others' — the same merge rule
        :meth:`PartyHandleManager.link` applies to link metadata.
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
        source: LinkSource,
        display_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Record ``value`` as ``user``'s own account handle: the control fact + a confirmed self-link.

        The one verb the connect flows (OIDC login, CardDAV/OAuth connect) call for
        the signed-in user's own address. It writes BOTH facts the model separates:
        the **control** fact (:attr:`Handle.owner` — this user sends/syncs as the
        address) and the **identity** fact (a confirmed :class:`PartyHandle` to the
        user's own :class:`~angee.parties.models.Person`, so the address resolves to
        them). Directory *contacts'* handles get neither. Idempotent.

        A claim is contested when another user already controls the row. The
        existing control owner is never reassigned; instead the competing user's
        identity link is recorded unconfirmed at ``0.3`` confidence and returned
        for later review. The whole decision is atomic and row-locked.
        """

        person_model = apps.get_model("parties", "Person")
        party_handle_model = apps.get_model("parties", "PartyHandle")
        link_owner = party_handle_model.objects
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
                link_owner.link(
                    person,
                    handle,
                    confidence=0.3,
                    source=source,
                    is_confirmed=False,
                    created_by_id=user.pk,
                )
                return handle
            if handle.owner_id is None:
                handle.owner = user
                handle.save(update_fields=["owner", "updated_at"])
            link_owner.link(
                person,
                handle,
                confidence=1.0,
                source=source,
                is_confirmed=True,
                created_by_id=user.pk,
            )
        return handle


class PartyHandleQuerySet(AngeeQuerySet):
    """Require association mutations to pass through the resolution owner."""

    def readable_party_name_expression(self, *, actor: Any) -> Any:
        """Return the linked Party label only when ``actor`` can read it."""

        if actor is None:
            return Value("", output_field=TextField())
        party_model = apps.get_model("parties", "Party")
        return (
            party_model.objects.with_actor(actor)
            .filter(pk=OuterRef("party_id"))
            .readable_scalar_subquery(
                "display_name",
                actor=actor,
                default="",
                output_field=TextField(),
            )
        )

    def readable_handle_value_expression(self, *, actor: Any) -> Any:
        """Return the linked Handle value only when ``actor`` can read it."""

        if actor is None:
            return Value("", output_field=TextField())
        handle_model = apps.get_model("parties", "Handle")
        return (
            handle_model.objects.with_actor(actor)
            .filter(pk=OuterRef("handle_id"))
            .readable_scalar_subquery(
                "value",
                actor=actor,
                default="",
                output_field=TextField(),
            )
        )

    def update(self, **kwargs: Any) -> int:
        """Keep association transitions on the owner that resolves handles."""

        raise TypeError("Party-handle transitions must use link(), confirm(), dismiss(), or delete().")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Require new associations to run the manager's resolution bookkeeping."""

        raise TypeError("Party-handle links must be created through PartyHandleManager.link().")


class PartyHandleManager(AngeeManager.from_queryset(PartyHandleQuerySet)):  # type: ignore[misc]
    """Owns the confidence link between a party and a handle, and the resolution."""

    def lock_identity_rows(
        self,
        *,
        party_ids: Iterable[int],
        handle_ids: Iterable[int],
    ) -> tuple[dict[int, Any], dict[int, Any]]:
        """Lock identity rows in the total Handle, Party, PartyHandle order."""

        handle_model = self.model._meta.get_field("handle").remote_field.model
        party_model = self.model._meta.get_field("party").remote_field.model
        ordered_handle_ids = sorted(set(handle_ids))
        ordered_party_ids = sorted(set(party_ids))
        with system_context(reason="parties.party_handle.lock_identity_rows"):
            handles = {
                row.pk: row
                for row in handle_model.objects.filter(pk__in=ordered_handle_ids).order_by("pk").lock_if_supported()
            }
            ordered_party_ids = sorted(
                set(ordered_party_ids)
                .union(row.party_id for row in handles.values() if row.party_id is not None)
                .union(self.filter(handle_id__in=ordered_handle_ids).values_list("party_id", flat=True))
            )
            parties = {
                row.pk: row
                for row in party_model.objects.filter(pk__in=ordered_party_ids).order_by("pk").lock_if_supported()
            }
            list(
                self.filter(party_id__in=ordered_party_ids, handle_id__in=ordered_handle_ids)
                .order_by("pk")
                .lock_if_supported()
            )
        return handles, parties

    def _transition(self, link: Any, *, action: str, actor: Any) -> None:
        """Apply a confirmation transition under the canonical identity lock set."""

        if action not in {"confirm", "dismiss"}:
            raise ValueError("Unknown PartyHandle transition.")
        if actor is None:
            raise PermissionDenied("write access to the party-handle link is required")
        with transaction.atomic():
            self.lock_identity_rows(
                party_ids=(link.party_id,),
                handle_ids=(link.handle_id,),
            )
            locked = self.with_actor(actor).with_action("write").get(pk=link.pk)
            if not locked.has_access("write"):
                raise PermissionDenied("write access to the party-handle link is required")
            with system_context(reason=f"parties.party_handle.{action}"):
                getattr(ScoredLinkMixin, action)(locked)
            for field in ("confidence", "source", "is_confirmed", "is_dismissed", "updated_at"):
                setattr(link, field, getattr(locked, field))

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
        identity, while the Party association remains a reviewable ``PartyHandle``.
        Reusing a global Handle therefore requires read access and never refreshes
        its display fields. Repeating a dismissed Party association returns that
        durable anti-link unchanged; confirmation remains the explicit human action.
        """

        party.with_actor(actor)._require_record_access("write")
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
            created_by_id=getattr(actor, "pk", None),
        )
        candidate.full_clean(validate_unique=False, validate_constraints=False)

        with transaction.atomic(), actor_context(actor):
            handle_owner = handle_model.objects
            handle = (
                handle_owner.sudo(reason="parties.party_handle.propose_manual_contact.handle")
                .lock_if_supported()
                .filter(platform=normalized_platform, value=contact_value)
                .first()
            )
            if handle is None:
                handle_owner.check_create()
                with system_context(reason="parties.party_handle.propose_manual_contact.handle"):
                    handle = handle_owner.upsert(
                        platform=normalized_platform,
                        value=contact_value,
                        created_by_id=getattr(actor, "pk", None),
                    )
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            locked_party = parties[party.pk].with_actor(actor)
            locked_party._require_record_access("write")
            if not handle.with_actor(actor).has_access("read"):
                raise PermissionDenied("Denied: cannot add this contact point.")
            if normalized_label and not handle.label and handle.has_access("write"):
                handle.label = normalized_label
                handle.save(update_fields=("label", "updated_at"))

            existing = (
                self.sudo(reason="parties.party_handle.propose_manual_contact.lookup")
                .lock_if_supported()
                .filter(party_id=locked_party.pk, handle_id=handle.pk)
                .first()
            )
            if existing is not None:
                if not existing.with_actor(actor).has_access("read"):
                    raise PermissionDenied("Denied: cannot add this contact point.")
                return existing

            verified_link_actor = self.check_create()
            with system_context(reason="parties.party_handle.propose_manual_contact.link"):
                link = self.link(
                    locked_party,
                    handle,
                    confidence=0.4,
                    source=cast(LinkSource, LinkSource.MANUAL),
                    is_confirmed=False,
                    created_by_id=getattr(actor, "pk", None),
                )
            if not link.with_actor(actor).has_access("read"):
                raise PermissionDenied("Denied: cannot read the contact association.")
            return link.with_actor(verified_link_actor)

    def has_confirmed_association(self, handle: Any, *, actor: Any) -> bool:
        """Return whether this readable Handle has any confirmed owner, without disclosing it."""

        if actor is None:
            raise PermissionDenied("an actor is required to assess a party-handle association")
        handle.with_actor(actor)._require_record_access("read")
        with system_context(reason="parties.party_handle.has_confirmed_association"):
            return self.filter(
                handle_id=handle.pk,
                is_confirmed=True,
                is_dismissed=False,
            ).exists()

    def assess_claimed_handle(self, party: Any, handle: Any, *, actor: Any) -> HandleAssociationAssessment:
        """Assess a claimed Handle without disclosing inaccessible Party associations."""

        if actor is None:
            raise PermissionDenied("an actor is required to assess a party-handle association")
        party.with_actor(actor)._require_record_access("read")
        handle.with_actor(actor)._require_record_access("read")
        visible = read_scoped_queryset(self.model, actor)
        readable = (
            tuple(visible.filter(handle_id=handle.pk).select_related("party").order_by("pk"))
            if visible is not None
            else ()
        )
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
        with system_context(reason="parties.party_handle.assess_claimed_handle"):
            authoritative = tuple(
                self.filter(handle_id=handle.pk).only("party_id", "is_confirmed", "is_dismissed").order_by("pk")
            )
        same = tuple(link for link in authoritative if link.party_id == party.pk)
        if any(link.is_dismissed for link in same):
            status = HandleAssociationStatus.SAME_DISMISSED
        elif any(link.is_confirmed and not link.is_dismissed and link.party_id != party.pk for link in authoritative):
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
                (link.party_id == party.pk and link.is_dismissed)
                or (link.party_id != party.pk and link.is_confirmed and not link.is_dismissed)
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

        party.with_actor(actor)._require_record_access("write")
        handle.with_actor(actor)._require_record_access("read")
        evidence.with_actor(actor)._require_record_access("read")
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

        party.with_actor(actor)._require_record_access("write")
        if not 0 < confidence < 0.5:
            raise ValidationError({"confidence": "Claimed-handle proposals require confidence below 0.5."})
        evidence_model = canonical_record_model(type(evidence))
        evidence_ref = {
            "model": evidence_model._meta.label,
            "id": public_id_for(evidence_model, evidence.pk),
        }
        with system_context(reason="parties.party_handle.propose_claimed_handle"), transaction.atomic():
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            locked_handle = handles[handle.pk]
            locked_party = parties[party.pk]
            existing = self.filter(party=locked_party, handle=locked_handle).first()
            refs = list((existing.metadata or {}).get("evidence", ())) if existing is not None else []
            if evidence_ref not in refs:
                refs.append(evidence_ref)
            return self.link(
                locked_party,
                locked_handle,
                confidence=confidence,
                source=cast(LinkSource, LinkSource.EMAIL_MATCH),
                is_confirmed=False,
                metadata={
                    "claim": "source_sender",
                    "evidence": refs,
                },
                created_by_id=getattr(actor, "pk", None),
            )

    def link(
        self,
        party: Any,
        handle: Any,
        *,
        confidence: float = 1.0,
        source: LinkSource = cast(LinkSource, LinkSource.MANUAL),
        is_confirmed: bool = False,
        metadata: dict[str, Any] | None = None,
        created_by_id: Any = None,
    ) -> Any:
        """Link ``handle`` to ``party`` with ``confidence``, then resolve the handle's owner.

        ``is_confirmed`` records a human-strength decision (a connect flow claiming
        the signed-in user's own handle); it upgrades an existing weaker link to the
        confirmed self-link. Resolution only re-runs when the link is new, upgraded,
        or the handle's owner is not already this party, so a re-sync of an unchanged
        contact does no extra work. Source metadata merges onto the existing link so
        a later importer can add provenance without erasing prior evidence.
        """

        with transaction.atomic():
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            party = parties[party.pk]
            link, created = self.get_or_create(
                party=party,
                handle=handle,
                defaults={
                    "confidence": confidence,
                    "source": source,
                    "is_confirmed": is_confirmed,
                    "metadata": metadata or {},
                    "created_by_id": created_by_id,
                },
            )
            upgraded = False
            dirty: list[str] = []
            if not created and is_confirmed and not link.is_confirmed:
                link.confidence = confidence
                link.source = source
                link.is_confirmed = True
                link.is_dismissed = False
                dirty.extend(("confidence", "source", "is_confirmed", "is_dismissed"))
                upgraded = True
            merged_metadata = {**(link.metadata or {}), **(metadata or {})}
            if merged_metadata != link.metadata:
                link.metadata = merged_metadata
                dirty.append("metadata")
            if dirty:
                link.save(update_fields=[*dict.fromkeys(dirty), "updated_at"])
            if created or upgraded or handle.party_id != party.pk:
                link._resolve_link()
            return link

    def resolve(self, handle: Any) -> None:
        """Materialise ``handle.party`` and its confirmed state from the winning link.

        The resolution ordering (``-is_confirmed, -confidence``) is the contacts
        rule: a human-confirmed link wins, then the strongest score. A handle with
        no surviving link is left unowned. A demotion (a dismissed winner) recounts
        the previous owner too, so its ``handle_count`` never goes stale.
        """

        with transaction.atomic():
            handles, _parties = self.lock_identity_rows(
                party_ids=(),
                handle_ids=(handle.pk,),
            )
            handle = handles[handle.pk]
            previous_pk = handle.party_id
            winner = (
                self.filter(handle=handle, is_dismissed=False).order_by("-is_confirmed", "-confidence", "sqid").first()
            )
            resolved = winner.party if winner else None
            resolved_pk = resolved.pk if resolved else None
            is_confirmed = bool(winner and winner.is_confirmed)
            dirty = []
            if handle.party_id != resolved_pk:
                handle.party_id = resolved_pk
                dirty.append("party")
            if handle.party_link_confirmed != is_confirmed:
                handle.party_link_confirmed = is_confirmed
                dirty.append("party_link_confirmed")
            if dirty:
                handle.save(update_fields=[*dirty, "updated_at"])
            if resolved is not None:
                self.recount(resolved)
            if previous_pk is not None and previous_pk != resolved_pk:
                party_model = apps.get_model("parties", "Party")
                previous = party_model.objects.filter(pk=previous_pk).first()
                if previous is not None:
                    self.recount(previous)
            handle._party_links_resolved()

    def recount(self, party: Any) -> None:
        """Refresh ``party.handle_count`` from the handles resolved onto it (write only on change).

        Idempotent, so it doubles as the repair pass for the drift the counter
        signals cannot see (``bulk_create`` / ``QuerySet.update`` skip signals).
        """

        handle_model = apps.get_model("parties", "Handle")
        count = handle_model.objects.filter(party_id=party.pk).count()
        if party.handle_count != count:
            party.handle_count = count
            party.save(update_fields=["handle_count", "updated_at"])

    def suggest_for(self, handle: Any) -> Any:
        """Propose a party for a freshly-seen, unresolved ``handle`` (the EMAIL_MATCH producer).

        Three branches, all leaving links unconfirmed for review and never
        duplicating an existing pair: indexed normalized twins contribute distinct
        candidate parties (the first at ``1.0``, competing parties at ``0.3``);
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
            link = self.link(
                candidate.party,
                handle,
                confidence=1.0 if strongest is None else 0.3,
                source=cast(LinkSource, LinkSource.EMAIL_MATCH),
                created_by_id=handle.created_by_id,
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
                return self.link(
                    org,
                    handle,
                    confidence=0.4,
                    source=cast(LinkSource, LinkSource.RULE),
                    created_by_id=handle.created_by_id,
                )
        return None

    def suggest_from_signature(
        self,
        *,
        text: str,
        party_ids: Iterable[Any],
        fragment_hash: str,
        owner_id: Any,
    ) -> int:
        """Mine one unique signature fragment for weak party-to-phone suggestions.

        ``text`` and its content hash are neutral evidence supplied by the scheduled
        task; this manager owns phone extraction, Handle creation, link provenance,
        owner partition, and the durable-pair check. Every mined link records its
        fragment evidence at ``0.3`` confidence. An existing pair, including a
        dismissed anti-link, is never changed.
        """

        handle_model = apps.get_model("parties", "Handle")
        party_model = apps.get_model("parties", "Party")
        if owner_id is None:
            return 0
        parties = tuple(
            party_model.objects.filter(
                pk__in=frozenset(party_ids),
                created_by_id=owner_id,
            ).order_by("sqid")
        )
        if not parties:
            return 0
        created = 0
        metadata = {
            "evidence": {
                "kind": "signature_phone",
                "fragment_hash": fragment_hash,
            }
        }
        for value in self._signature_phone_values(text, handle_model=handle_model):
            handle = handle_model.objects.upsert(
                platform=handle_model.Platform.PHONE,
                value=value,
                created_by_id=owner_id,
            )
            if handle.created_by_id != owner_id:
                # Handles are globally unique by source identity. Evidence owned by
                # one directory must never attach another owner's pre-existing row.
                continue
            for party in parties:
                created += self._suggest(
                    party,
                    handle,
                    confidence=0.3,
                    metadata=metadata,
                    created_by_id=party.created_by_id,
                )
        return created

    def suggest_from_display_names(self) -> int:
        """Pool normalized display names per audit owner into weak identity links.

        A resolved handle supplies evidence only to an unresolved handle on another
        platform. Each distinct candidate party receives one ``0.4`` rule link;
        existing pairs, including dismissed links, remain untouched.
        """

        handle_model = apps.get_model("parties", "Handle")
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
            existing_pairs = set(self.filter(handle__created_by_id=owner_id).values_list("party_id", "handle_id"))
            pools: defaultdict[str, list[Any]] = defaultdict(list)
            for handle in handles:
                normalized_name = handle_model.normalize_display_name(handle.display_name)
                if normalized_name:
                    pools[normalized_name].append(handle)

            for handle in handles:
                if handle.party_id is not None:
                    continue
                normalized_name = handle_model.normalize_display_name(handle.display_name)
                seen_parties: set[Any] = set()
                for candidate in pools.get(normalized_name, ()):
                    if (
                        candidate.party_id is None
                        or candidate.party.created_by_id != owner_id
                        or candidate.platform == handle.platform
                        or candidate.party_id in seen_parties
                    ):
                        continue
                    seen_parties.add(candidate.party_id)
                    if (candidate.party_id, handle.pk) in existing_pairs:
                        continue
                    existing_pairs.add((candidate.party_id, handle.pk))
                    created += self._suggest(
                        candidate.party,
                        handle,
                        confidence=0.4,
                        metadata={
                            "evidence": {
                                "kind": "display_name",
                                "normalized_display_name": normalized_name,
                                "source_handle": str(candidate.sqid),
                            }
                        },
                        created_by_id=owner_id,
                    )
        return created

    def _suggest(
        self,
        party: Any,
        handle: Any,
        *,
        confidence: float,
        metadata: dict[str, Any],
        created_by_id: Any,
    ) -> int:
        """Create one unconfirmed rule link, or skip its durable existing pair."""

        with transaction.atomic():
            handles, parties = self.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=(handle.pk,),
            )
            locked_handle = handles[handle.pk]
            locked_party = parties[party.pk]
            _, created = self.get_or_create(
                party_id=locked_party.pk,
                handle_id=locked_handle.pk,
                defaults={
                    "confidence": confidence,
                    "source": LinkSource.RULE,
                    "metadata": metadata,
                    "created_by_id": created_by_id,
                },
            )
            if not created:
                return 0
            self.resolve(locked_handle)
            return 1

    @staticmethod
    def _signature_phone_values(text: str, *, handle_model: Any) -> tuple[str, ...]:
        """Return deterministic canonical phone values mined from signature text."""

        values = {
            handle_model.normalize_value(
                handle_model.Platform.PHONE,
                match.raw_string,
            )
            for match in PhoneNumberMatcher(text or "", None)
        }
        for match in _SIGNATURE_PHONE_CANDIDATE.finditer(text or ""):
            normalized = handle_model.normalize_value(handle_model.Platform.PHONE, match.group())
            digits = sum(character.isdigit() for character in normalized)
            if 7 <= digits <= 15:
                values.add(normalized)
        return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class DuplicatePartyCandidate:
    """One deterministic duplicate candidate and the normalized handle it shares."""

    left: Any
    right: Any
    normalized_value: str


class MergeVetoManager(AngeeManager):
    """Own canonical keep-separate pair lookup and creation."""

    def forbids(self, a: Any, b: Any) -> bool:
        """Return whether the canonical pair ``a``/``b`` has a durable veto."""

        party_a_id, party_b_id = self._ordered_ids(a, b)
        with system_context(reason="parties.merge_veto.forbids"):
            return self.model._base_manager.filter(
                party_a_id=party_a_id,
                party_b_id=party_b_id,
            ).exists()

    def forbidden_pairs(self, party_ids: set[Any]) -> set[tuple[Any, Any]]:
        """Return vetoed canonical pairs whose two endpoints are in ``party_ids``."""

        if not party_ids:
            return set()
        with system_context(reason="parties.merge_veto.forbidden_pairs"):
            rows = self.model._base_manager.filter(
                party_a_id__in=party_ids,
                party_b_id__in=party_ids,
            ).values_list("party_a_id", "party_b_id")
            return set(rows)

    def veto(self, a: Any, b: Any, *, actor: Any = None) -> Any:
        """Persist the canonical keep-separate fact after locking both writable parties.

        The pair lock is the same lock, in the same order, that :meth:`PartyManager.merge`
        takes. A simultaneous merge and veto therefore serialize around the human
        identity decision instead of both committing after independent checks.
        """

        party_a_id, party_b_id = self._ordered_ids(a, b)
        actor = actor or current_actor()
        party_model = apps.get_model("parties", "Party")
        with transaction.atomic(), actor_context(actor):
            locked = {
                party.pk: party
                for party in party_model.objects.lock_if_supported()
                .filter(pk__in=(party_a_id, party_b_id))
                .order_by("pk")
            }
            if party_a_id not in locked or party_b_id not in locked:
                raise ValidationError("One of the parties no longer exists.")
            party_a = locked[party_a_id].with_actor(actor)
            party_b = locked[party_b_id].with_actor(actor)
            if party_a.merged_into_id is not None or party_b.merged_into_id is not None:
                raise ValidationError("Only canonical parties can be kept separate.")
            if not party_a.has_access("write") or not party_b.has_access("write"):
                raise PermissionDenied("write access to both parties is required")

            with system_context(reason="parties.merge_veto.lookup"):
                existing = self.model._base_manager.filter(
                    party_a_id=party_a_id,
                    party_b_id=party_b_id,
                ).first()
            if existing is not None:
                return existing.with_actor(actor) if actor is not None else existing

            verified_actor = self.check_create()
            veto = self.model(party_a_id=party_a_id, party_b_id=party_b_id)
            veto.full_clean(validate_unique=False, validate_constraints=False)
            veto.sudo(reason="parties.merge_veto.create")
            try:
                with transaction.atomic():
                    veto.save()
            except IntegrityError:
                # Retain idempotence on databases without row-level pair locks.
                with system_context(reason="parties.merge_veto.concurrent_lookup"):
                    veto = self.model._base_manager.get(
                        party_a_id=party_a_id,
                        party_b_id=party_b_id,
                    )
            return veto.with_actor(verified_actor)

    @staticmethod
    def _ordered_ids(a: Any, b: Any) -> tuple[Any, Any]:
        """Return saved, distinct party primary keys in canonical order."""

        if a.pk is None or b.pk is None:
            raise ValidationError("Both parties must be saved.")
        if a.pk == b.pk:
            raise ValidationError("A party cannot be kept separate from itself.")
        party_a_id, party_b_id = sorted((a.pk, b.pk))
        return party_a_id, party_b_id


class PartyQuerySet(AngeeQuerySet):
    """Party read scopes: canonical (unmerged) rows and organisation membership."""

    def canonical(self) -> Self:
        """Return only the canonical (unmerged) parties.

        Writes flatten every merge chain to its terminal (``Party.merge_into``), so a party
        is canonical exactly when it points nowhere — one indexed filter that stays
        correct even against a longer chain, since a terminal always points nowhere.
        """

        return self.filter(merged_into__isnull=True)

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
        """Return canonical parties with an undecided low-confidence handle link."""

        party_handle_model = apps.get_model("parties", "PartyHandle")
        visible_review_link = (
            party_handle_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                party_id=OuterRef("pk"),
                confidence__lt=0.5,
                is_confirmed=False,
                is_dismissed=False,
            )
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
        relationships = apps.get_model("parties", "Relationship").objects.all()
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

    def duplicate_candidates(self, *, limit: int = 50) -> list[DuplicatePartyCandidate]:
        """Return bounded actor-visible party pairs sharing a normalized handle.

        Candidate order is deterministic by normalized value then primary-key pair.
        A pair appears once even if it shares several handles, and any durable
        :class:`~angee.parties.models.MergeVeto` removes it from the queue.
        """

        bounded = max(0, min(int(limit), 101))
        if bounded == 0:
            return []

        handle_model = apps.get_model("parties", "Handle")
        merge_veto_model = apps.get_model("parties", "MergeVeto")
        visible_party_ids = self.canonical().scoped_for_aggregate().values("pk")
        handles = (
            handle_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                party_id__in=Subquery(visible_party_ids),
            )
            .exclude(normalized_value="")
        )
        shared_handles = list(
            handles.values("platform", "normalized_value")
            .annotate(party_count=Count("party_id", distinct=True))
            .filter(party_count__gt=1)
            .order_by("normalized_value", "platform")
            .values_list("platform", "normalized_value")[:bounded]
        )
        if not shared_handles:
            return []

        shared_filter = Q()
        for platform, normalized_value in shared_handles:
            shared_filter |= Q(platform=platform, normalized_value=normalized_value)

        parties_by_handle: dict[tuple[str, str], list[Any]] = {}
        for platform, normalized_value, party_id in (
            handles.filter(shared_filter)
            .values_list("platform", "normalized_value", "party_id")
            .distinct()
            .order_by("normalized_value", "platform", "party_id")
        ):
            parties_by_handle.setdefault((platform, normalized_value), []).append(party_id)

        candidate_party_ids = {party_id for party_ids in parties_by_handle.values() for party_id in party_ids}
        forbidden = merge_veto_model.objects.forbidden_pairs(candidate_party_ids)
        pairs: list[tuple[str, Any, Any]] = []
        seen: set[tuple[Any, Any]] = set()
        for (_platform, normalized_value), party_ids in parties_by_handle.items():
            for party_a_id, party_b_id in combinations(party_ids, 2):
                pair = (party_a_id, party_b_id)
                if pair in seen or pair in forbidden:
                    continue
                seen.add(pair)
                pairs.append((normalized_value, *pair))
                if len(pairs) >= bounded:
                    break
            if len(pairs) >= bounded:
                break

        paired_party_ids = {
            party_id for _normalized_value, party_a_id, party_b_id in pairs for party_id in (party_a_id, party_b_id)
        }
        parties = {party.pk: party for party in self.canonical().filter(pk__in=paired_party_ids)}
        return [
            DuplicatePartyCandidate(
                left=parties[party_a_id],
                right=parties[party_b_id],
                normalized_value=normalized_value,
            )
            for normalized_value, party_a_id, party_b_id in pairs
            if party_a_id in parties and party_b_id in parties
        ]


class PartyManager(AngeeManager.from_queryset(PartyQuerySet)):  # type: ignore[misc]
    """Factory for parties, including the idempotent directory-sync ingest.

    Also the effective manager of the MTI children (``Person`` / ``Organization``
    inherit the parent's concrete default manager), so the Person-per-user factory
    :meth:`for_user` lives here.
    """

    def identity_snapshot(self, party_id: str, *, actor: Any, lock: bool = False) -> tuple[Any, dict[str, Any]]:
        """Read the actor-visible identity basis, optionally locking its entire row set."""

        parties = self.with_actor(actor)
        party = parties.from_public_id(party_id)
        if party is None:
            raise ValidationError({"party_id": "Party was not found."})
        party.with_actor(actor)._require_record_access("read")
        addresses = apps.get_model("parties", "Address").objects.with_actor(actor).filter(party=party)
        readable_handles = apps.get_model("parties", "Handle").objects.with_actor(actor).scoped().values("pk")
        link_owner = apps.get_model("parties", "PartyHandle").objects
        links = link_owner.with_actor(actor).filter(party=party, handle_id__in=Subquery(readable_handles))
        if lock:
            handle_ids = tuple(links.order_by("handle_id").values_list("handle_id", flat=True))
            _handles, locked_parties = link_owner.lock_identity_rows(
                party_ids=(party.pk,),
                handle_ids=handle_ids,
            )
            party = locked_parties[party.pk].with_actor(actor)
            addresses = addresses.order_by("pk").lock_if_supported()
            links = links.order_by("pk").lock_if_supported()
        return party, party.identity_values(
            list(addresses.order_by("is_primary", "sqid")),
            list(links.select_related("handle").order_by("sqid")),
        )

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

        A replay after a successful change returns conflict because the retained
        basis hash no longer matches; unchanged choices are idempotent no-ops.
        """

        allowed = {
            "name_action": {"keep", "replace"},
            "address_action": {"keep", "add", "replace"},
            "handle_action": {"keep", "confirm", "dismiss"},
        }
        if set(choices) != set(allowed) or any(value not in allowed[name] for name, value in choices.items()):
            raise ValidationError({"choices": "Identity choices are invalid."})
        with transaction.atomic(), actor_context(actor):
            party, current = self.identity_snapshot(party_id, actor=actor, lock=True)
            party._require_record_access("write")
            if canonical_json_sha256(current) != expected_facts_hash:
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
                expected = addresses.with_actor(actor).from_public_id(primary["id"]) if primary else None
                results["address_result"], _ = addresses.replace_primary_exact(
                    party=party,
                    values=proposed["address"],
                    actor=actor,
                    expected_id=expected.pk if expected else None,
                    label=proposed["address"]["label"],
                )
            if choices["handle_action"] != "keep":
                link = (
                    apps.get_model("parties", "PartyHandle")
                    .objects.with_actor(actor)
                    .lock_if_supported()
                    .from_public_id(proposed["handle"]["party_handle_id"])
                )
                if link is None or link.party_id != party.pk:
                    raise ValidationError({"handle_action": "The proposed PartyHandle changed during review."})
                getattr(link.with_actor(actor), choices["handle_action"])()
                results["handle_result"] = f"{choices['handle_action']}ed"
            outcome = "applied" if any(value not in {"kept", "matched"} for value in results.values()) else "unchanged"
            return outcome, results

    @staticmethod
    def prepare_duplicate_pairs(proposed: Any, approved: Any) -> list[dict[str, str]]:
        """Validate the fixed pair basis and retain only supported pair operations."""

        if not isinstance(proposed, list) or not isinstance(approved, list) or len(proposed) != len(approved):
            raise ValidationError({"pairs": "Duplicate review must preserve every proposed pair."})
        rows = []
        for expected, resolved in zip(proposed, approved, strict=True):
            if not isinstance(expected, Mapping) or not isinstance(resolved, Mapping):
                raise ValidationError({"pairs": "Duplicate pairs must be objects."})
            if any(
                resolved.get(key) != expected.get(key)
                for key in ("left", "right", "left_name", "right_name", "evidence")
            ):
                raise ValidationError({"pairs": "Duplicate review changed a proposed pair."})
            action, survivor = resolved.get("action"), resolved.get("survivor")
            if action not in {"merge", "skip", "keep_separate"} or survivor not in {"left", "right"}:
                raise ValidationError({"pairs": "Duplicate review carries an unsupported choice."})
            if action != "skip":
                rows.append(
                    {
                        "left": str(expected["left"]),
                        "right": str(expected["right"]),
                        "action": action,
                        "survivor": survivor,
                    }
                )
        return rows

    def apply_duplicate_pair(self, *, left_id: str, right_id: str, survivor: str, action: str, actor: Any) -> str:
        """Apply one merge or durable veto using plain values and an explicit actor."""

        if survivor not in {"left", "right"} or action not in {"merge", "keep_separate"}:
            raise ValidationError({"pair": "Unsupported duplicate pair operation."})
        with transaction.atomic(), actor_context(actor):
            left = self.with_actor(actor).from_public_id(left_id)
            right = self.with_actor(actor).from_public_id(right_id)
            if left is None or right is None:
                raise ValidationError({"pair": "Duplicate pair references a missing party."})
            if action == "keep_separate":
                apps.get_model("parties", "MergeVeto").objects.veto(left, right, actor=actor)
                return "vetoed"
            locked = {
                row.pk: row.with_actor(actor)
                for row in self.lock_if_supported().filter(pk__in=[left.pk, right.pk]).order_by("pk")
            }
            into, source = (
                (locked[left.pk], locked[right.pk]) if survivor == "left" else (locked[right.pk], locked[left.pk])
            )
            already_merged = source.merged_into_id == into.pk
            self.merge(into=into, source=source, actor=actor)
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

    def search_display_name(self, query: str, *, limit: int = 20) -> list[Any]:
        """Return a bounded actor-visible people list filtered by display name."""

        bounded = max(1, min(int(limit), 100))
        return list(
            self.canonical()
            .with_circle_names()
            .filter(display_name__icontains=query.strip())
            .order_by("display_name", "sqid")[:bounded]
        )

    def merge(self, *, into: Any, source: Any, field_overrides: Any = None, actor: Any = None) -> Any:
        """Merge ``source`` into ``into`` with vetted scalar overrides in one transaction."""

        if into.pk is None or source.pk is None:
            raise ValidationError("Both parties must be saved before merging.")
        if into.pk == source.pk:
            raise ValidationError("A party cannot be merged into itself.")

        actor = actor or current_actor()
        merge_veto_model = apps.get_model("parties", "MergeVeto")
        with transaction.atomic(), actor_context(actor):
            locked = {
                party.pk: party for party in self.lock_if_supported().filter(pk__in=(into.pk, source.pk)).order_by("pk")
            }
            if into.pk not in locked or source.pk not in locked:
                raise ValidationError("One of the parties no longer exists.")
            survivor = locked[into.pk].with_actor(actor)
            merged = locked[source.pk].with_actor(actor)
            if not survivor.has_access("write") or not merged.has_access("write"):
                raise PermissionDenied("write access to both parties is required")
            if merged.merged_into_id == survivor.pk and survivor.merged_into_id is None:
                return survivor
            if survivor.merged_into_id is not None or merged.merged_into_id is not None:
                raise ValidationError("Only canonical parties can be merged.")
            if merge_veto_model.objects.forbids(survivor, merged):
                raise ValidationError("These parties have been marked to stay separate.")
            survivor.apply_merge_field_overrides(merged, field_overrides)
            return merged.merge_into(survivor)

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

        Follow the shared Handle, Party, PartyHandle order before locking the
        Person child and its mapped collections. The parent row lock also fences
        inserts through its foreign keys while the page transaction is open.
        """

        associations = apps.get_model("parties", "PartyHandle").objects
        handles = apps.get_model("parties", "Handle").objects
        handle_ids = (
            set(associations.filter(party_id=person.pk).values_list("handle_id", flat=True))
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
                source=LinkSource.CARDDAV,
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
        """

        people = tuple(apps.get_model("parties", "Person").objects.filter(pk__in=[row.pk for row in people]))
        ids = [person.pk for person in people]
        if not ids:
            return {}
        platforms = apps.get_model("parties", "Handle").Platform
        handles: dict[Any, dict[str, list[tuple[str, str, bool]]]] = defaultdict(lambda: defaultdict(list))
        for row in (
            apps.get_model("parties", "PartyHandle")
            .objects.filter(
                party_id__in=ids, is_dismissed=False, handle__platform__in=(platforms.EMAIL, platforms.PHONE)
            )
            .values("party_id", "handle__platform", "handle__value", "handle__label", "handle__is_preferred")
        ):
            handles[row["party_id"]][row["handle__platform"]].append(
                (row["handle__value"], row["handle__label"], row["handle__is_preferred"])
            )
        addresses: dict[Any, list[ParsedAddress]] = defaultdict(list)
        for address in apps.get_model("parties", "Address").objects.filter(party_id__in=ids):
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
                apps.get_model("parties", "Relationship").objects.filter(
                    party_id__in=ids, kind__slug="employee", source=LinkSource.CARDDAV, other_party__isnull=True
                )
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
                uid=person.source_uid,
                etag=person.source_etag,
                raw_vcard=person.raw_vcard,
                display_name=person.display_name,
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
        """Store fetched photo bytes before contact locks, retaining their address."""

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
        target: Any = None,
    ) -> Any:
        """Upsert a person and its handles/addresses from one parsed contact.

        Keyed on ``(folder, source_uid)`` so a re-sync updates the same row instead
        of forking a duplicate, and the whole contact is written in one transaction
        so a partial card is never half-applied. Emails/phones still upsert as shared
        ``Handle`` rows and link to the person, but the person's identity is the
        source UID, not handle overlap. A contact with no ``source_uid`` has no stable
        key and is skipped — without it the ``(folder, "")`` upsert would collapse
        every keyless card onto one row. ``target`` binds a previously local
        Person after its first push; only a blank or matching source UID may
        adopt the observed identity, and it must remain in the same folder.
        """

        if not parsed.uid:
            return None
        parsed = self.prepare_contact(parsed, created_by_id=created_by_id)

        person_model = apps.get_model("parties", "Person")
        handle_model = apps.get_model("parties", "Handle")
        party_handle_model = apps.get_model("parties", "PartyHandle")
        address_model = apps.get_model("parties", "Address")
        relationship_model = apps.get_model("parties", "Relationship")
        relationship_kind_model = apps.get_model("parties", "RelationshipKind")

        with transaction.atomic():
            if target is not None:
                self.lock_contact(target, parsed=parsed)
            handles = []
            for platform, values in (
                (handle_model.Platform.EMAIL, parsed.emails),
                (handle_model.Platform.PHONE, parsed.phones),
            ):
                for value, label, is_preferred in values:
                    handle = handle_model.objects.upsert(
                        platform=platform,
                        value=value,
                        created_by_id=created_by_id,
                        label=label,
                        is_preferred=is_preferred,
                        display_name=parsed.display_name,
                    )
                    # Unlike a generic enrichment upsert, the source mapping also
                    # represents a removed label or preference.
                    mapped = {"label": label, "is_preferred": is_preferred}
                    dirty = [name for name, value in mapped.items() if getattr(handle, name) != value]
                    if dirty:
                        for name in dirty:
                            setattr(handle, name, mapped[name])
                        handle.save(update_fields=[*dirty, "updated_at"])
                    handles.append(handle)
            people = person_model.objects
            identity = {"folder": folder, "source_uid": parsed.uid}
            if target is not None:
                target = people.lock_if_supported().filter(pk=target.pk).first()
                if target is None or target.folder_id != folder.pk or target.source_uid not in ("", parsed.uid):
                    raise ValidationError("The linked contact no longer has the expected folder and source identity.")
                if people.filter(folder=folder, source_uid=parsed.uid).exclude(pk=target.pk).exists():
                    raise ValidationError("Another contact already has this directory source identity.")
                identity = {"pk": target.pk, "folder": folder}
            try:
                with transaction.atomic():
                    person, _created = people.update_or_create(
                        **identity,
                        defaults={
                            "source_uid": parsed.uid,
                            "display_name": parsed.display_name or parsed.family_name or person_model.PLACEHOLDER_NAME,
                            "name_prefix": parsed.name_prefix,
                            "given_name": parsed.given_name,
                            "additional_name": parsed.additional_name,
                            "family_name": parsed.family_name,
                            "name_suffix": parsed.name_suffix,
                            "nickname": parsed.nickname,
                            "notes": parsed.notes,
                            "birthday": parsed.birthday,
                            "anniversary": parsed.anniversary,
                            # Preparation stored the bytes before this transaction.
                            "avatar": self.resolve_contact_photo(parsed.photo, lock=True),
                            "raw_vcard": parsed.raw_vcard,
                            "source_etag": parsed.etag,
                            "created_by_id": created_by_id,
                        },
                    )
            except IntegrityError as exc:
                if (
                    target is not None
                    and people.filter(
                        folder=folder,
                        source_uid=parsed.uid,
                    )
                    .exclude(pk=target.pk)
                    .exists()
                ):
                    raise ValidationError("Another contact already has this directory source identity.") from exc
                raise

            for handle in handles:
                party_handle_model.objects.link(
                    person,
                    handle,
                    confidence=1.0,
                    source=LinkSource.CARDDAV,
                    created_by_id=created_by_id,
                )
            # Retire only this source's vanished associations. Shared Handle rows
            # and links established by another source retain their own lifecycle.
            party_handle_model.objects.filter(
                party_id=person.pk,
                source=LinkSource.CARDDAV,
            ).exclude(handle_id__in=[handle.pk for handle in handles]).delete()

            # Addresses carry no stable id, so mirror the parsed set wholesale —
            # idempotent because the result is exactly the source's.
            address_model.objects.filter(party=person).delete()
            for addr in parsed.addresses:
                address_model.objects.create(
                    party=person,
                    label=addr.label,
                    po_box=addr.po_box,
                    extended=addr.extended,
                    street=addr.street,
                    city=addr.city,
                    region=addr.region,
                    postal_code=addr.postal_code,
                    country=addr.country,
                    created_by_id=created_by_id,
                )

            # Employment maps to a typed edge: the vCard ORG is the counterparty
            # (free-text — a synced card names an employer, not a tracked org row),
            # TITLE rides on the edge, and a non-empty ROLE folds into its notes.
            # The catalogue kind is part of the mapper contract; without it the
            # source cannot be represented truthfully, so fail the contact atomically.
            try:
                employee_kind = relationship_kind_model.objects.get(slug="employee")
            except relationship_kind_model.DoesNotExist as exc:
                raise ValidationError(
                    "CardDAV employment sync requires the employee RelationshipKind master row."
                ) from exc
            employment = relationship_model.objects.filter(
                party=person,
                kind=employee_kind,
                source=LinkSource.CARDDAV,
                other_party__isnull=True,
            )
            if not parsed.organization:
                employment.delete()
            else:
                edge, created = relationship_model.objects.get_or_create(
                    party=person,
                    kind=employee_kind,
                    source=LinkSource.CARDDAV,
                    other_party=None,
                    defaults={
                        "other_name": parsed.organization,
                        "title": parsed.title,
                        "notes": parsed.role,
                        "created_by_id": created_by_id,
                    },
                )
                if not created:
                    employment_values = {
                        "other_name": parsed.organization,
                        "title": parsed.title,
                        "notes": parsed.role,
                    }
                    dirty = [name for name, value in employment_values.items() if getattr(edge, name) != value]
                    if dirty:
                        for name in dirty:
                            setattr(edge, name, employment_values[name])
                        edge.save(update_fields=[*dirty, "updated_at"])

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
