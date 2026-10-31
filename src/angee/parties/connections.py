"""Parties-owned landing for social-export connection facts.

Adapters emit :class:`ParsedConnection` values and never write parties rows.
This owner upserts the account handle, materializes a Person behind its
normal PartyStatement (an export is a source, never confirmed), records the source account's acquaintance edge, and maps
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
    parsed_connections: Iterable[tuple[ParsedConnection, Any]],
    *,
    created_by_id: Any,
    asserted_by: Any,
) -> list[Any]:
    """Historical adapter entrypoint for the native Party manager's import factory."""

    return apps.get_model("parties", "Party").objects.ingest_connections(
        parsed_connections,
        created_by_id=created_by_id,
        asserted_by=asserted_by,
    )
