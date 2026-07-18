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
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING, Any, Self, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from phonenumbers import PhoneNumberMatcher
from rebac import PermissionDenied, current_actor, system_context

from angee.base.mixins import HierarchyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.parties.domains import GENERIC_EMAIL_DOMAINS
from angee.parties.mixins import LinkSource

if TYPE_CHECKING:
    from angee.parties.backends import ParsedContact


_SIGNATURE_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d(?:[\d \t()./\-]*\d)?(?!\w)")


class CircleQuerySet(HierarchyQuerySet, AngeeQuerySet):
    """Circle read scopes: the hierarchy subtree vocabulary over the Angee base."""

    def with_member_counts(self) -> Self:
        """Annotate each circle with its distinct-party count across its subtree."""

        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        person_model = apps.get_model("parties", "Person")
        visible_person_ids = (
            person_model.objects.all().scoped_for_aggregate().canonical().values("pk")
        )
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

    def upsert(self, *, platform: str, value: str, created_by_id: Any = None, **fields: Any) -> Any:
        """Get-or-create a handle on the identity it actually has, refreshing display fields.

        A source-stable ``external_id`` (in ``fields``, when the source has one)
        is the stronger identity — the model's conditional unique key — so the
        write serializes on whichever identity is present: ``get_or_create`` on
        ``(platform, external_id)`` when given, else ``(platform, value)``. That
        means an address whose human-readable ``value`` drifts (a chat account
        behind a changed number) refreshes the existing row instead of forking a
        duplicate or crashing a concurrent insert on the external-id constraint.
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
            if not created:
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
        """Apply the non-blank ``fields`` that differ; one save, only when dirty."""

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
        with system_context(reason="parties.handle.claim_own"), transaction.atomic():
            handle = self.upsert(
                platform=platform,
                value=value,
                created_by_id=user.pk,
                display_name=display_name,
                metadata=metadata or {},
            )
            handle = self.lock_if_supported().get(pk=handle.pk)
            person = person_model.objects.for_user(user)
            if handle.owner_id is not None and handle.owner_id != user.pk:
                party_handle_model.objects.link(
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
            party_handle_model.objects.link(
                person,
                handle,
                confidence=1.0,
                source=source,
                is_confirmed=True,
                created_by_id=user.pk,
            )
        return handle


class PartyHandleManager(AngeeManager):
    """Owns the confidence link between a party and a handle, and the resolution."""

    def link(
        self,
        party: Any,
        handle: Any,
        *,
        confidence: float = 1.0,
        source: LinkSource = cast(LinkSource, LinkSource.MANUAL),
        is_confirmed: bool = False,
        created_by_id: Any = None,
    ) -> Any:
        """Link ``handle`` to ``party`` with ``confidence``, then resolve the handle's owner.

        ``is_confirmed`` records a human-strength decision (a connect flow claiming
        the signed-in user's own handle); it upgrades an existing weaker link to the
        confirmed self-link. Resolution only re-runs when the link is new, upgraded,
        or the handle's owner is not already this party, so a re-sync of an unchanged
        contact does no extra work.
        """

        link, created = self.get_or_create(
            party=party,
            handle=handle,
            defaults={
                "confidence": confidence,
                "source": source,
                "is_confirmed": is_confirmed,
                "created_by_id": created_by_id,
            },
        )
        upgraded = False
        if not created and is_confirmed and not link.is_confirmed:
            link.confidence = confidence
            link.source = source
            link.is_confirmed = True
            link.is_dismissed = False
            link.save(update_fields=["confidence", "source", "is_confirmed", "is_dismissed", "updated_at"])
            upgraded = True
        if created or upgraded or handle.party_id != party.pk:
            self.resolve(handle)
        return link

    def resolve(self, handle: Any) -> None:
        """Materialise ``handle.party`` and its confirmed state from the winning link.

        The resolution ordering (``-is_confirmed, -confidence``) is the contacts
        rule: a human-confirmed link wins, then the strongest score. A handle with
        no surviving link is left unowned. A demotion (a dismissed winner) recounts
        the previous owner too, so its ``handle_count`` never goes stale.
        """

        previous_pk = handle.party_id
        winner = (
            self.filter(handle=handle, is_dismissed=False)
            .order_by("-is_confirmed", "-confidence", "sqid")
            .select_related("party")
            .first()
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
                self.filter(handle__created_by_id=owner_id).values_list("party_id", "handle_id")
            )
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

        _, created = self.get_or_create(
            party=party,
            handle=handle,
            defaults={
                "confidence": confidence,
                "source": LinkSource.RULE,
                "metadata": metadata,
                "created_by_id": created_by_id,
            },
        )
        if not created:
            return 0
        self.resolve(handle)
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

    def veto(self, a: Any, b: Any) -> Any:
        """Persist the canonical keep-separate fact after locking both writable parties.

        The pair lock is the same lock, in the same order, that :meth:`PartyManager.merge`
        takes. A simultaneous merge and veto therefore serialize around the human
        identity decision instead of both committing after independent checks.
        """

        party_a_id, party_b_id = self._ordered_ids(a, b)
        actor = current_actor()
        party_model = apps.get_model("parties", "Party")
        with transaction.atomic():
            locked = {
                party.pk: party
                for party in party_model.objects.lock_if_supported()
                .filter(pk__in=(party_a_id, party_b_id))
                .order_by("pk")
            }
            if party_a_id not in locked or party_b_id not in locked:
                raise ValidationError("One of the parties no longer exists.")
            party_a = locked[party_a_id]
            party_b = locked[party_b_id]
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
        visible_circle_ids = (
            circle_model.objects.all().scoped_for_aggregate().values("pk")
        )
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
        visible_circle_ids = (
            circle_model.objects.all().scoped_for_aggregate().values("pk")
        )
        visible_membership = (
            circle_member_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                party_id=OuterRef("pk"),
                circle_id__in=Subquery(visible_circle_ids),
            )
        )
        return self.canonical().annotate(
            _has_visible_circle=Exists(visible_membership),
        ).filter(_has_visible_circle=False)

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
        return self.canonical().annotate(
            _has_visible_review_link=Exists(visible_review_link),
        ).filter(_has_visible_review_link=True)

    def in_circle(self, circle: Any) -> Self:
        """Return canonical parties in ``circle`` or any of its descendants."""

        circle_model = apps.get_model("parties", "Circle")
        circle_member_model = apps.get_model("parties", "CircleMember")
        subtree_ids = (
            circle_model.objects.all()
            .subtree_of(circle)
            .scoped_for_aggregate()
            .values("pk")
        )
        visible_subtree_membership = (
            circle_member_model.objects.all()
            .scoped_for_aggregate()
            .filter(
                party_id=OuterRef("pk"),
                circle_id__in=Subquery(subtree_ids),
            )
        )
        return self.canonical().annotate(
            _in_visible_circle_subtree=Exists(visible_subtree_membership),
        ).filter(_in_visible_circle_subtree=True)

    def members_of(self, organization: Any) -> Self:
        """Return the parties whose relationships name ``organization`` as counterparty.

        The successor to the removed ``Organization.members`` reverse accessor: a
        member is any party anchoring a current (open-ended)
        :class:`Relationship` whose tracked counterparty is this organisation
        (employment and other org-typed edges).
        """

        return self.filter(
            relationships__other_party=organization,
            relationships__ended_at__isnull=True,
        ).distinct()

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
        visible_circle_ids = (
            circle_model.objects.all().scoped_for_aggregate().values("pk")
        )
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
        person, _created = person_model._default_manager.get_or_create(
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

    def merge(self, *, into: Any, source: Any, field_overrides: Any = None) -> Any:
        """Merge ``source`` into ``into`` with vetted scalar overrides in one transaction."""

        if into.pk is None or source.pk is None:
            raise ValidationError("Both parties must be saved before merging.")
        if into.pk == source.pk:
            raise ValidationError("A party cannot be merged into itself.")

        merge_veto_model = apps.get_model("parties", "MergeVeto")
        with transaction.atomic():
            locked = {
                party.pk: party for party in self.lock_if_supported().filter(pk__in=(into.pk, source.pk)).order_by("pk")
            }
            if into.pk not in locked or source.pk not in locked:
                raise ValidationError("One of the parties no longer exists.")
            survivor = locked[into.pk]
            merged = locked[source.pk]
            if survivor.merged_into_id is not None or merged.merged_into_id is not None:
                raise ValidationError("Only canonical parties can be merged.")
            if not survivor.has_access("write") or not merged.has_access("write"):
                raise PermissionDenied("write access to both parties is required")
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

    def ingest_contact(self, parsed: ParsedContact, *, folder: Any, created_by_id: Any) -> Any:
        """Upsert a person and its handles/addresses from one parsed contact.

        Keyed on ``(folder, source_uid)`` so a re-sync updates the same row instead
        of forking a duplicate, and the whole contact is written in one transaction
        so a partial card is never half-applied. Emails/phones still upsert as shared
        ``Handle`` rows and link to the person, but the person's identity is the
        source UID, not handle overlap. A contact with no ``source_uid`` has no stable
        key and is skipped — without it the ``(folder, "")`` upsert would collapse
        every keyless card onto one row.
        """

        if not parsed.uid:
            return None

        person_model = apps.get_model("parties", "Person")
        handle_model = apps.get_model("parties", "Handle")
        party_handle_model = apps.get_model("parties", "PartyHandle")
        address_model = apps.get_model("parties", "Address")
        relationship_model = apps.get_model("parties", "Relationship")
        relationship_kind_model = apps.get_model("parties", "RelationshipKind")

        with transaction.atomic():
            person, _created = person_model.objects.update_or_create(
                folder=folder,
                source_uid=parsed.uid,
                defaults={
                    "display_name": parsed.display_name or parsed.family_name or "Unknown",
                    "name_prefix": parsed.name_prefix,
                    "given_name": parsed.given_name,
                    "additional_name": parsed.additional_name,
                    "family_name": parsed.family_name,
                    "name_suffix": parsed.name_suffix,
                    "nickname": parsed.nickname,
                    "notes": parsed.notes,
                    "birthday": parsed.birthday,
                    "anniversary": parsed.anniversary,
                    # Mirror the source's photo: re-syncing identical bytes dedups to
                    # the same File, and a removed photo clears the avatar.
                    "avatar": self._ingest_avatar(parsed, created_by_id=created_by_id),
                    "raw_vcard": parsed.raw_vcard,
                    "source_etag": parsed.etag,
                    "created_by_id": created_by_id,
                },
            )

            handles = [
                handle_model.objects.upsert(
                    platform=handle_model.Platform.EMAIL,
                    value=value,
                    created_by_id=created_by_id,
                    label=label,
                    is_preferred=is_preferred,
                    display_name=parsed.display_name,
                )
                for value, label, is_preferred in parsed.emails
            ] + [
                handle_model.objects.upsert(
                    platform=handle_model.Platform.PHONE,
                    value=value,
                    created_by_id=created_by_id,
                    label=label,
                    is_preferred=is_preferred,
                    display_name=parsed.display_name,
                )
                for value, label, is_preferred in parsed.phones
            ]
            for handle in handles:
                party_handle_model.objects.link(
                    person,
                    handle,
                    confidence=1.0,
                    source=LinkSource.CARDDAV,
                    created_by_id=created_by_id,
                )

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
                    values = {
                        "other_name": parsed.organization,
                        "title": parsed.title,
                        "notes": parsed.role,
                    }
                    dirty = [name for name, value in values.items() if getattr(edge, name) != value]
                    if dirty:
                        for name in dirty:
                            setattr(edge, name, values[name])
                        edge.save(update_fields=[*dirty, "updated_at"])

            return person

    def _ingest_avatar(self, parsed: ParsedContact, *, created_by_id: Any) -> Any:
        """Persist a parsed contact photo through the storage File owner, or return None.

        Delegates to ``File.objects.ingest_bytes`` — the storage owner's server-side
        byte intake — so the avatar lands content-addressed (identical photos dedup)
        and ``Party.avatar`` resolves. A URI photo is already resolved to bytes by
        the directory backend's transport step before it reaches here.
        """

        photo = parsed.photo
        if photo is None or not photo.data:
            return None
        file_model = apps.get_model("storage", "File")
        extension = mimetypes.guess_extension(photo.mime) if photo.mime else ""
        return file_model.objects.ingest_bytes(
            photo.data,
            filename=f"avatar{extension or '.bin'}",
            owner_id=created_by_id,
        )

    def purge_missing(self, *, folder: Any, keep_uids: set[str]) -> int:
        """Delete the folder's synced parties whose source UID is no longer present.

        This is how a contact deleted on the source is mirrored locally: anything in
        ``folder`` carrying a ``source_uid`` not in ``keep_uids`` is removed (the MTI
        child cascades with its parent). Cascaded PartyHandle deletes re-resolve
        shared handles through the delete-path signal owner.
        """

        stale_pks = list(
            self.filter(folder=folder)
            .exclude(source_uid="")
            .exclude(source_uid__in=keep_uids)
            .values_list("pk", flat=True)
        )
        if not stale_pks:
            return 0
        # PartyHandle's post_delete receiver owns cascade re-resolution.
        deleted, _by_model = self.filter(pk__in=stale_pks).delete()
        return deleted


def _user_display_name(user: Any) -> str:
    """Return a human display name for a user's Person, falling back to a stable id."""

    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    username = user.get_username() if hasattr(user, "get_username") else getattr(user, "username", "")
    for candidate in (full_name, username, getattr(user, "email", "")):
        text = (candidate or "").strip()
        if text:
            return text
    return str(user.pk)
