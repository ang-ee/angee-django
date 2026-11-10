"""Neutral social-export connection values an adapter hands to parties.

Adapters emit :class:`ParsedConnection` values and never write parties rows;
``PartyManager.ingest_connections`` lands them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


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
