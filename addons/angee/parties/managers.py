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
from typing import TYPE_CHECKING, Any, Self, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context
from rebac.managers import RebacManager

from angee.base.mixins import HierarchyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.parties.mixins import LinkSource

if TYPE_CHECKING:
    from angee.parties.backends import ParsedContact


class CircleQuerySet(HierarchyQuerySet, AngeeQuerySet):
    """Circle read scopes: the hierarchy subtree vocabulary over the Angee base."""


class CircleManager(RebacManager.from_queryset(CircleQuerySet)):  # type: ignore[misc]
    """Manager for circles — subtree scopes ride in through :class:`CircleQuerySet`."""


class HandleQuerySet(AngeeQuerySet):
    """Handle read scopes over the Angee base."""

    def owned_by(self, user: Any) -> Self:
        """Return the handles this user controls — the ``owner`` column, no joins."""

        return self.filter(owner=user)


class HandleManager(AngeeManager.from_queryset(HandleQuerySet)):  # type: ignore[misc]
    """Factory + upsert for handles (the contact-point write path)."""

    def upsert(self, *, platform: str, value: str, created_by_id: Any = None, **fields: Any) -> Any:
        """Get-or-create a handle by its ``(platform, value)`` dedup key, refreshing display fields.

        ``created_by_id`` stamps the audit owner. Control ownership is deliberately
        excluded from the generic refresh loop; :meth:`claim_own` is its only write
        path, so a routine upsert cannot silently transfer an account between users.
        """

        if "owner" in fields or "owner_id" in fields:
            raise TypeError("Handle control ownership must be written through claim_own().")
        if "normalized_value" in fields:
            raise TypeError("Handle.normalized_value is maintained by Handle.save().")
        normalized_value = self.model.normalize_value(platform, value)
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
            dirty = [name for name, new in fields.items() if new and getattr(handle, name, None) != new]
            if handle.normalized_value != normalized_value:
                handle.normalized_value = normalized_value
                dirty.append("normalized_value")
            if dirty:
                for name in dirty:
                    if name in fields:
                        setattr(handle, name, fields[name])
                handle.save(update_fields=[*dirty, "updated_at"])
        return handle

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
        otherwise an email whose domain matches a tracked
        :attr:`Organization.domain` contributes a rule suggestion at ``0.4``;
        otherwise no-op. Returns the strongest created/existing link, or ``None``.
        """

        if handle.party_id is not None:
            return None
        handle_model = apps.get_model("parties", "Handle")
        twins = (
            handle_model.objects.filter(
                platform=handle.platform,
                normalized_value=handle.normalized_value,
                party__isnull=False,
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
            org = organization_model.objects.filter(domain__iexact=domain).first() if domain else None
            if org is not None:
                return self.link(
                    org,
                    handle,
                    confidence=0.4,
                    source=cast(LinkSource, LinkSource.RULE),
                    created_by_id=handle.created_by_id,
                )
        return None


class PartyQuerySet(AngeeQuerySet):
    """Party read scopes: canonical (unmerged) rows and organisation membership."""

    def canonical(self) -> Self:
        """Return only the canonical (unmerged) parties.

        Writes flatten every merge chain to its terminal (``Party.merge_into``), so a party
        is canonical exactly when it points nowhere — one indexed filter that stays
        correct even against a longer chain, since a terminal always points nowhere.
        """

        return self.filter(merged_into__isnull=True)

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


class PartyManager(AngeeManager.from_queryset(PartyQuerySet)):  # type: ignore[misc]
    """Factory for parties, including the idempotent directory-sync ingest.

    Also the effective manager of the MTI children (``Person`` / ``Organization``
    inherit the parent's concrete default manager), so the Person-per-user factory
    :meth:`for_user` lives here.
    """

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
