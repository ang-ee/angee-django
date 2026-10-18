"""Parties-owned landing for social-export connection facts.

Adapters emit :class:`ParsedConnection` values and never write parties rows.
This owner upserts the account handle, materializes a Person behind its
normal PartyStatement (an export is a source, never confirmed), records the account owner's acquaintance edge, and maps
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
from rebac.actors import to_subject_ref

from angee.base.actors import source_kind_for
from angee.base.evidence import EvidenceReference
from angee.base.models import SourceKind


class ConnectionHandle(Protocol):
    """The neutral parsed-handle fields a connection adapter supplies.

    Read-only properties so a frozen adapter DTO (``messaging``'s
    ``ParsedHandle``) satisfies the protocol without parties importing it.
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

    @property
    def confidence(self) -> float | None: ...


@dataclass(frozen=True)
class ParsedConnection:
    """One social account's connection to a person, neutral of export format.

    ``handle.metadata`` carries adapter evidence about the account handle.
    The batch's identified integration supplies the asserting principal.
    """

    handle: ConnectionHandle
    email: str = ""
    organization: str = ""
    title: str = ""
    connected_at: date | datetime | None = None


def ingest_connections(
    parsed_connections: Iterable[tuple[ParsedConnection, Any]], *, created_by_id: Any, asserted_by: Any
) -> list[Any]:
    """Idempotently land parsed connections and return their Person rows.

    The batch entry point: the source user, its anchor Party, and each required
    ``RelationshipKind`` resolve once for the whole run — a friends export is
    thousands of records and must not repeat loop-invariant lookups.
    Each item pairs the parsed value with its observed RecordLink. Records land
    in separate transactions, so one bad record fails alone.
    """

    if source_kind_for(to_subject_ref(asserted_by)) != SourceKind.IMPORT:
        raise ValidationError("Connection exports require an identified IMPORT integration.")
    party_model = apps.get_model("parties", "Party")
    kind_model = apps.get_model("parties", "RelationshipKind")
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    with system_context(reason="parties.connection.ingest"):
        user = user_model._base_manager.get(pk=created_by_id)
        owner_party = party_model.objects.for_user(user)
        kinds: dict[Any, Any] = {}
        return [
            _ingest_one_connection(
                parsed,
                link=link,
                owner_party=owner_party,
                kind_model=kind_model,
                kinds=kinds,
                created_by_id=created_by_id,
                asserted_by=asserted_by,
            )
            for parsed, link in parsed_connections
        ]


def _ingest_one_connection(
    parsed: ParsedConnection,
    *,
    link: Any,
    owner_party: Any,
    kind_model: Any,
    kinds: dict[Any, Any],
    created_by_id: Any,
    asserted_by: Any,
) -> Any:
    """Land one parsed connection and return its Person.

    The source account's user is the relationship anchor. The imported person
    becomes its ``acquaintance`` from ``connected_at`` onward; normal handle
    assertions name the supplied IMPORT integration and are never preferred: an
    export is a source, not the person's decision. It decides the handle's owner
    (confidence 1.0) only when the handle names an account by a real identifier
    (:func:`_identified`); a name alone lands at 0.4, for review. Optional email
    and employment facts compose the same Handle/PartyStatement/Relationship owners.
    """

    handle_model = apps.get_model("parties", "Handle")
    person_model = apps.get_model("parties", "Person")
    parties = apps.get_model("parties", "Party").objects
    relationship_model = apps.get_model("parties", "Relationship")
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
        person = _connection_person(
            handle,
            display_name=parsed.handle.display_name or parsed.handle.value,
            asserted_by=asserted_by,
            person_model=person_model,
            created_by_id=created_by_id,
        )
        parent = apps.get_model("parties", "Party")._base_manager.get(pk=person.pk)
        apps.get_model("integrate", "RecordLink").objects.bind_target(link, parent)
        parties.assert_handle(
            person,
            handle,
            confidence=1.0 if _identified(parsed.handle, handle_model) else 0.4,
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
            parties.assert_handle(
                person,
                email,
                confidence=1.0,
                asserted_by=asserted_by,
                link=link,
                evidence=[handle],
            )
        _land_acquaintance(
            owner_party,
            person,
            connected_at=parsed.connected_at,
            relationship_model=relationship_model,
            kind_model=kind_model,
            kinds=kinds,
            created_by_id=created_by_id,
            asserted_by=asserted_by,
            link=link,
            evidence=handle,
        )
        _land_employment(
            person,
            organization=parsed.organization,
            title=parsed.title,
            relationship_model=relationship_model,
            kind_model=kind_model,
            kinds=kinds,
            created_by_id=created_by_id,
            asserted_by=asserted_by,
            link=link,
            evidence=handle,
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


def _identified(parsed: ConnectionHandle, handle_model: Any) -> bool:
    """Whether a connection names its account by a real identifier, not by a display name only.

    An email address, a phone number or a platform id identifies one account.
    An adapter may override that inference with its typed confidence.
    """

    if parsed.confidence is not None:
        return parsed.confidence >= 1.0
    return bool(parsed.external_id) or parsed.platform in {handle_model.Platform.EMAIL, handle_model.Platform.PHONE}


def _connection_person(
    handle: Any,
    *,
    display_name: str,
    asserted_by: Any,
    person_model: Any,
    created_by_id: Any,
) -> Any:
    """Return the handle's Person; identity statements own subsequent names.

    The handle's owner when it has one; otherwise the party an earlier run of the
    same import linked it to, since a claim awaiting review, or one the person
    dismissed, owns nothing; a re-import therefore never mints a second person.
    An existing name is never overwritten: it may be user-curated, and export
    text must not clobber curation on every re-run.
    """

    party_id = handle.party_id or (
        apps.get_model("parties", "PartyStatement")
        ._base_manager.filter(handle=handle, aspect="handle", asserted_by=str(to_subject_ref(asserted_by)))
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


def _relationship_kind(kind_model: Any, slug: str, kinds: dict[Any, Any]) -> Any:
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
    relationship_model: Any,
    kind_model: Any,
    kinds: dict[Any, Any],
    created_by_id: Any,
    asserted_by: Any,
    link: Any,
    evidence: Any,
) -> None:
    """Record the source account's idempotent connection to ``person``."""

    if owner_party.pk == person.pk:
        return
    kind = _relationship_kind(kind_model, "acquaintance", kinds)
    edge, _ = relationship_model.objects.get_or_create(
        party=owner_party,
        other_party=person,
        kind=kind,
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
            "started_at": _connection_date(connected_at),
            "ended_at": None,
            "notes": "",
        },
    )


def _land_employment(
    person: Any,
    *,
    organization: str,
    title: str,
    relationship_model: Any,
    kind_model: Any,
    kinds: dict[Any, Any],
    created_by_id: Any,
    asserted_by: Any,
    link: Any,
    evidence: Any,
) -> None:
    """Land the existing free-text employee relationship shape when present."""

    organization = str(organization or "").strip()
    if not organization:
        return
    accessor = kind_model.employee
    if accessor not in kinds:
        kinds[accessor] = accessor()
    kind = kinds[accessor]
    if kind is None:
        raise ValidationError("The employee RelationshipKind master row is required.")
    edge, _ = relationship_model.objects.get_or_create(
        party=person,
        other_party=None,
        other_name=organization,
        kind=kind,
        defaults={"created_by_id": created_by_id},
    )
    edge.statements.set(
        aspect="relationship",
        asserted_by=asserted_by,
        link=link,
        evidence=[EvidenceReference.of(evidence)],
        values={
            "other_name": organization,
            "title": str(title or ""),
            "notes": "",
            "started_at": None,
            "ended_at": None,
        },
    )


def _connection_date(value: date | datetime | None) -> date | None:
    """Return a DateField-ready connection date without losing plain dates."""

    return value.date() if isinstance(value, datetime) else value
