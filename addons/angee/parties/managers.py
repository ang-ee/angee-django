"""Managers that own the directory-sync write path for parties.

A directory backend parses a source into neutral ``ParsedContact`` rows; these
managers turn one into a ``Party`` (a ``Person``) and its ``Handle`` /
``PartyHandle`` / ``Address`` rows — deduplicating a handle by ``(platform,
value)`` and resolving the party an existing handle already belongs to, so every
directory source shares one write path (the map lives on the models, not in each
backend). The resolved owner and the ``handle_count`` denormalisation are
maintained here, in the same transaction as the link. The sync runs under
``system_context``, so ``created_by`` is set explicitly to the directory owner.
"""

from __future__ import annotations

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
        """Link ``handle`` to ``party`` with ``confidence``, then resolve the handle's owner."""

        link, _created = self.get_or_create(
            party=party,
            handle=handle,
            defaults={"confidence": confidence, "source": source, "created_by_id": owner_id},
        )
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
        """Refresh ``party.handle_count`` from the handles resolved onto it."""

        handle_model = apps.get_model("parties", "Handle")
        party.handle_count = handle_model.objects.filter(party_id=party.pk).count()
        party.save(update_fields=["handle_count", "updated_at"])


class PartyManager(AngeeManager):
    """Factory for parties, including the directory-sync ingest."""

    def ingest_contact(self, parsed: ParsedContact, *, owner_id: Any) -> Any:
        """Create or update a person and its handles/addresses from one parsed contact.

        Dedup is by handle: an existing handle's resolved party is reused, otherwise
        a new ``Person`` is created. Runs in one transaction so a partial contact is
        never half-written.
        """

        person_model = apps.get_model("parties", "Person")
        handle_model = apps.get_model("parties", "Handle")
        party_handle_model = apps.get_model("parties", "PartyHandle")
        address_model = apps.get_model("parties", "Address")

        with transaction.atomic():
            handles = [
                handle_model.objects.upsert(
                    platform="email", value=value, owner_id=owner_id, label=label, display_name=parsed.display_name
                )
                for value, label in parsed.emails
            ] + [
                handle_model.objects.upsert(
                    platform="phone", value=value, owner_id=owner_id, label=label, display_name=parsed.display_name
                )
                for value, label in parsed.phones
            ]

            party = next((handle.party for handle in handles if handle.party_id), None)
            if party is None:
                party = person_model.objects.create(
                    display_name=parsed.display_name or parsed.family_name or "Unknown",
                    name_prefix=parsed.name_prefix,
                    given_name=parsed.given_name,
                    additional_name=parsed.additional_name,
                    family_name=parsed.family_name,
                    name_suffix=parsed.name_suffix,
                    nickname=parsed.nickname,
                    notes=parsed.notes,
                    raw_vcard=parsed.raw_vcard,
                    created_by_id=owner_id,
                )
            elif parsed.display_name and party.display_name != parsed.display_name:
                party.display_name = parsed.display_name
                party.save(update_fields=["display_name", "updated_at"])

            for handle in handles:
                party_handle_model.objects.link(
                    party, handle, confidence=1.0, source="carddav", owner_id=owner_id
                )

            for addr in parsed.addresses:
                address_model.objects.update_or_create(
                    party=party,
                    label=addr.label,
                    street=addr.street,
                    defaults={
                        "po_box": addr.po_box,
                        "extended": addr.extended,
                        "city": addr.city,
                        "region": addr.region,
                        "postal_code": addr.postal_code,
                        "country": addr.country,
                        "created_by_id": owner_id,
                    },
                )

            return party
