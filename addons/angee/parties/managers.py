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
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.db import transaction

from angee.base.models import AngeeManager

if TYPE_CHECKING:
    from angee.parties.backends import ParsedContact


class HandleManager(AngeeManager):
    """Factory + upsert for handles (the contact-point write path)."""

    def upsert(self, *, platform: str, value: str, owner_id: Any = None, **fields: Any) -> Any:
        """Get-or-create a handle by its ``(platform, value)`` dedup key, refreshing display fields."""

        handle, created = self.get_or_create(
            platform=platform,
            value=value,
            defaults={"created_by_id": owner_id, **fields},
        )
        if not created:
            dirty = [name for name, new in fields.items() if new and getattr(handle, name, None) != new]
            if dirty:
                for name in dirty:
                    setattr(handle, name, fields[name])
                handle.save(update_fields=[*dirty, "updated_at"])
        return handle


class PartyHandleManager(AngeeManager):
    """Owns the confidence link between a party and a handle, and the resolution."""

    def link(
        self,
        party: Any,
        handle: Any,
        *,
        confidence: float = 1.0,
        source: str = "manual",
        owner_id: Any = None,
    ) -> Any:
        """Link ``handle`` to ``party`` with ``confidence``, then resolve the handle's owner.

        Resolution only re-runs when the link is new or the handle's owner is not
        already this party, so a re-sync of an unchanged contact does no extra work.
        """

        link, created = self.get_or_create(
            party=party,
            handle=handle,
            defaults={"confidence": confidence, "source": source, "created_by_id": owner_id},
        )
        if created or handle.party_id != party.pk:
            self.resolve(handle)
        return link

    def resolve(self, handle: Any) -> None:
        """Materialise ``handle.party`` to the highest-confidence, non-dismissed link.

        The resolution ordering (``-is_confirmed, -confidence``) is the contacts
        rule: a human-confirmed link wins, then the strongest score. A handle with
        no surviving link is left unowned.
        """

        winner = (
            self.filter(handle=handle, is_dismissed=False)
            .order_by("-is_confirmed", "-confidence", "sqid")
            .select_related("party")
            .first()
        )
        resolved = winner.party if winner else None
        resolved_pk = resolved.pk if resolved else None
        if handle.party_id != resolved_pk:
            handle.party_id = resolved_pk
            handle.save(update_fields=["party", "updated_at"])
        if resolved is not None:
            self._recount(resolved)

    def _recount(self, party: Any) -> None:
        """Refresh ``party.handle_count`` from the handles resolved onto it (write only on change)."""

        handle_model = apps.get_model("parties", "Handle")
        count = handle_model.objects.filter(party_id=party.pk).count()
        if party.handle_count != count:
            party.handle_count = count
            party.save(update_fields=["handle_count", "updated_at"])


class PartyManager(AngeeManager):
    """Factory for parties, including the idempotent directory-sync ingest."""

    def ingest_contact(self, parsed: ParsedContact, *, folder: Any, owner_id: Any) -> Any:
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
        affiliation_model = apps.get_model("parties", "Affiliation")

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
                    "avatar": self._ingest_avatar(parsed, owner_id=owner_id),
                    "raw_vcard": parsed.raw_vcard,
                    "source_etag": parsed.etag,
                    "created_by_id": owner_id,
                },
            )

            handles = [
                handle_model.objects.upsert(
                    platform="email",
                    value=value,
                    owner_id=owner_id,
                    label=label,
                    is_preferred=is_preferred,
                    display_name=parsed.display_name,
                )
                for value, label, is_preferred in parsed.emails
            ] + [
                handle_model.objects.upsert(
                    platform="phone",
                    value=value,
                    owner_id=owner_id,
                    label=label,
                    is_preferred=is_preferred,
                    display_name=parsed.display_name,
                )
                for value, label, is_preferred in parsed.phones
            ]
            for handle in handles:
                party_handle_model.objects.link(person, handle, confidence=1.0, source="carddav", owner_id=owner_id)

            # Addresses and the affiliation carry no stable id, so mirror the parsed
            # set wholesale — idempotent because the result is exactly the source's.
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
                    created_by_id=owner_id,
                )

            affiliation_model.objects.filter(party=person).delete()
            if parsed.organization or parsed.title or parsed.role or parsed.department:
                affiliation_model.objects.create(
                    party=person,
                    organization_name=parsed.organization,
                    title=parsed.title,
                    role=parsed.role,
                    department=parsed.department,
                    is_primary=True,
                    created_by_id=owner_id,
                )

            return person

    def _ingest_avatar(self, parsed: ParsedContact, *, owner_id: Any) -> Any:
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
            owner_id=owner_id,
        )

    def purge_missing(self, *, folder: Any, keep_uids: set[str]) -> int:
        """Delete the folder's synced parties whose source UID is no longer present.

        This is how a contact deleted on the source is mirrored locally: anything in
        ``folder`` carrying a ``source_uid`` not in ``keep_uids`` is removed (the MTI
        child cascades with its parent). A handle shared with a surviving party is
        re-resolved afterwards, since its link to the deleted party cascades away.
        """

        party_handle_model = apps.get_model("parties", "PartyHandle")
        handle_model = apps.get_model("parties", "Handle")
        stale_pks = list(
            self.filter(folder=folder)
            .exclude(source_uid="")
            .exclude(source_uid__in=keep_uids)
            .values_list("pk", flat=True)
        )
        if not stale_pks:
            return 0
        orphaned_handle_ids = list(
            party_handle_model.objects.filter(party_id__in=stale_pks).values_list("handle_id", flat=True).distinct()
        )
        deleted, _by_model = self.filter(pk__in=stale_pks).delete()
        for handle in handle_model.objects.filter(pk__in=orphaned_handle_ids):
            party_handle_model.objects.resolve(handle)
        return deleted
