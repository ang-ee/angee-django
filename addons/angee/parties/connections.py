"""Parties-owned landing for social-export connection facts.

Adapters emit :class:`ParsedConnection` values and never write parties rows.
This owner upserts the account handle, materializes a Person behind its
PartyHandle (scored, never confirmed), records the account owner's acquaintance edge, and maps
an optional employer onto parties' existing ``employee`` Relationship shape.

Organization names in social exports are free text, not stable organization
identities. They therefore land as ``Relationship.other_name`` exactly like
CardDAV employment rather than minting an Organization row from a label.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context

from angee.parties.mixins import LinkSource


class ConnectionHandle(Protocol):
    """The neutral parsed-handle fields a connection adapter supplies.

    Read-only properties so a frozen adapter DTO (``messaging``'s
    ``ParsedHandle``) satisfies the protocol without parties importing it.
    ``metadata`` is the adapter's identity evidence for the handle — the one
    home for that fact.
    """

    @property
    def platform(self) -> str: ...

    @property
    def value(self) -> str: ...

    @property
    def external_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ParsedConnection:
    """One social account's connection to a person, neutral of export format.

    ``handle.metadata`` carries adapter evidence about the account handle;
    ``provenance`` names the connection record source independently.
    """

    handle: ConnectionHandle
    email: str = ""
    organization: str = ""
    title: str = ""
    connected_at: date | datetime | None = None
    provenance: str = ""


def ingest_connections(parsed_connections: Iterable[ParsedConnection], *, created_by_id: Any) -> list[Any]:
    """Idempotently land parsed connections and return their Person rows.

    The batch entry point: the source user, its anchor Party, and each required
    ``RelationshipKind`` resolve once for the whole run — a friends export is
    thousands of records and must not repeat loop-invariant lookups. Each
    record still lands in its own transaction, so one bad record fails alone.
    """

    party_model = apps.get_model("parties", "Party")
    kind_model = apps.get_model("parties", "RelationshipKind")
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    with system_context(reason="parties.connection.ingest"):
        user = user_model._base_manager.get(pk=created_by_id)
        owner_party = party_model.objects.for_user(user)
        kinds: dict[str, Any] = {}
        return [
            _ingest_one_connection(
                parsed,
                owner_party=owner_party,
                kind_model=kind_model,
                kinds=kinds,
                created_by_id=created_by_id,
            )
            for parsed in parsed_connections
        ]


def ingest_connection(
    parsed: ParsedConnection,
    *,
    created_by_id: Any,
) -> Any:
    """Idempotently land one parsed connection and return its Person."""

    return ingest_connections([parsed], created_by_id=created_by_id)[0]


def _ingest_one_connection(
    parsed: ParsedConnection,
    *,
    owner_party: Any,
    kind_model: Any,
    kinds: dict[str, Any],
    created_by_id: Any,
) -> Any:
    """Land one parsed connection and return its Person.

    The source account's user is the relationship anchor. The imported person
    becomes its ``acquaintance`` from ``connected_at`` onward. The handle link
    carries generic ``import`` provenance plus the adapter's precise label in
    metadata, and is never confirmed: an export is a source, not the person's
    decision. It decides the handle's owner (confidence 1.0) only when the
    handle names an account by a real identifier (:func:`_identified`); a name
    alone lands at 0.4, for review. Optional email and employment facts compose
    the same Handle/PartyHandle/Relationship owners.
    """

    handle_model = apps.get_model("parties", "Handle")
    person_model = apps.get_model("parties", "Person")
    party_handle_model = apps.get_model("parties", "PartyHandle")
    relationship_model = apps.get_model("parties", "Relationship")
    provenance = str(parsed.provenance or "social_takeout")
    link_metadata = {"provenance": provenance}

    with transaction.atomic():
        handle = handle_model.objects.upsert(
            platform=parsed.handle.platform,
            value=parsed.handle.value,
            external_id=parsed.handle.external_id,
            display_name=parsed.handle.display_name,
            metadata={
                **(parsed.handle.metadata or {}),
                "connection_import": link_metadata,
            },
            created_by_id=created_by_id,
        )
        person = _connection_person(
            handle,
            display_name=parsed.handle.display_name or parsed.handle.value,
            provenance=provenance,
            person_model=person_model,
            party_handle_model=party_handle_model,
            created_by_id=created_by_id,
        )
        party_handle_model.objects.link(
            person,
            handle,
            confidence=1.0 if _identified(parsed.handle, handle_model) else 0.4,
            source=LinkSource.IMPORT,
            metadata=link_metadata,
            created_by_id=created_by_id,
        )
        if parsed.email:
            email = handle_model.objects.upsert(
                platform=handle_model.Platform.EMAIL,
                value=parsed.email,
                display_name=person.display_name,
                created_by_id=created_by_id,
            )
            party_handle_model.objects.link(
                person,
                email,
                confidence=1.0,
                source=LinkSource.IMPORT,
                metadata=link_metadata,
                created_by_id=created_by_id,
            )
        _land_acquaintance(
            owner_party,
            person,
            connected_at=parsed.connected_at,
            provenance=provenance,
            relationship_model=relationship_model,
            kind_model=kind_model,
            kinds=kinds,
            created_by_id=created_by_id,
        )
        _land_employment(
            person,
            organization=parsed.organization,
            title=parsed.title,
            provenance=provenance,
            relationship_model=relationship_model,
            kind_model=kind_model,
            kinds=kinds,
            created_by_id=created_by_id,
        )
        return person


def _identified(parsed: ConnectionHandle, handle_model: Any) -> bool:
    """Whether a connection names its account by a real identifier, not by a display name only.

    An email address, a phone number or a platform id identifies one account; an
    adapter whose export carries names alone declares ``identity_confidence:
    name_only``, and a name can match a stranger.
    """

    if (parsed.metadata or {}).get("identity_confidence") == "name_only":
        return False
    return bool(parsed.external_id) or parsed.platform in {handle_model.Platform.EMAIL, handle_model.Platform.PHONE}


def _connection_person(
    handle: Any,
    *,
    display_name: str,
    provenance: str,
    person_model: Any,
    party_handle_model: Any,
    created_by_id: Any,
) -> Any:
    """Return the handle's Person, filling a blank display name only.

    The handle's owner when it has one; otherwise the party an earlier run of the
    same import linked it to, since a link awaiting review, or one the person
    dismissed, owns nothing; a re-import therefore never mints a second person.
    An existing name is never overwritten: it may be user-curated, and export
    text must not clobber curation on every re-run.
    """

    party_id = handle.party_id or (
        party_handle_model._base_manager.filter(
            handle=handle, source=LinkSource.IMPORT, metadata__provenance=provenance
        )
        .order_by("pk")
        .values_list("party_id", flat=True)
        .first()
    )
    if party_id is not None:
        person = person_model._base_manager.filter(pk=party_id).first()
        if person is not None:
            if display_name and not person.display_name:
                person.display_name = display_name
                person.save(update_fields=["display_name", "updated_at"])
            return person
    return person_model.objects.create(
        display_name=display_name or person_model.PLACEHOLDER_NAME,
        created_by_id=created_by_id,
    )


def _relationship_kind(kind_model: Any, slug: str, kinds: dict[str, Any]) -> Any:
    """Return a required parties relationship kind, resolved once per batch."""

    if slug in kinds:
        return kinds[slug]
    try:
        kinds[slug] = kind_model.objects.get(slug=slug)
    except kind_model.DoesNotExist as error:
        raise ValidationError(f"Social connection import requires the {slug!r} RelationshipKind master row.") from error
    return kinds[slug]


def _land_acquaintance(
    owner_party: Any,
    person: Any,
    *,
    connected_at: date | datetime | None,
    provenance: str,
    relationship_model: Any,
    kind_model: Any,
    kinds: dict[str, Any],
    created_by_id: Any,
) -> None:
    """Record the source account's idempotent connection to ``person``."""

    if owner_party.pk == person.pk:
        return
    kind = _relationship_kind(kind_model, "acquaintance", kinds)
    edge, created = relationship_model.objects.get_or_create(
        party=owner_party,
        other_party=person,
        kind=kind,
        defaults={
            "source": LinkSource.IMPORT,
            "started_at": _connection_date(connected_at),
            "notes": provenance,
            "created_by_id": created_by_id,
        },
    )
    if created or edge.source != LinkSource.IMPORT:
        return
    values = {
        "started_at": edge.started_at or _connection_date(connected_at),
        "notes": edge.notes or provenance,
    }
    dirty = [name for name, value in values.items() if getattr(edge, name) != value]
    if dirty:
        for name in dirty:
            setattr(edge, name, values[name])
        edge.save(update_fields=[*dirty, "updated_at"])


def _land_employment(
    person: Any,
    *,
    organization: str,
    title: str,
    provenance: str,
    relationship_model: Any,
    kind_model: Any,
    kinds: dict[str, Any],
    created_by_id: Any,
) -> None:
    """Land the existing free-text employee relationship shape when present."""

    organization = str(organization or "").strip()
    if not organization:
        return
    kind = _relationship_kind(kind_model, "employee", kinds)
    edge, created = relationship_model.objects.get_or_create(
        party=person,
        other_party=None,
        other_name=organization,
        kind=kind,
        source=LinkSource.IMPORT,
        defaults={
            "title": str(title or ""),
            "notes": provenance,
            "created_by_id": created_by_id,
        },
    )
    if created:
        return
    values = {"title": str(title or ""), "notes": edge.notes or provenance}
    dirty = [name for name, value in values.items() if getattr(edge, name) != value]
    if dirty:
        for name in dirty:
            setattr(edge, name, values[name])
        edge.save(update_fields=[*dirty, "updated_at"])


def _connection_date(value: date | datetime | None) -> date | None:
    """Return a DateField-ready connection date without losing plain dates."""

    return value.date() if isinstance(value, datetime) else value
