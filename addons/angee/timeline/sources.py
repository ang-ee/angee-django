"""The contribution contract behind the record timeline.

A source owns one event kind. It projects its own rows into one-line entries for
a record and reports how many it has; the timeline read merges every eligible
source's entries by time behind one keyset cursor. Bodies, verbs and composers
stay with the owning addon's aside tab; the timeline only points at them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from angee.impl import resolve_hooks

HOOK_SETTING = "ANGEE_TIMELINE_SOURCES"


@dataclass(frozen=True)
class TimelineEntry:
    """One line on a record's timeline."""

    id: str
    """The target row's public id; unique within its kind."""
    at: datetime
    kind: str
    title: str
    actor: str = ""
    detail: str = ""
    needs_me: bool = False
    target_model: str | None = None
    target_id: str | None = None
    group_id: str | None = None
    """The run (its public id) this event happened inside; the rail nests it under that run."""

    @property
    def order_key(self) -> tuple[datetime, str]:
        return (self.at, self.id)


@dataclass(frozen=True)
class Cursor:
    """A keyset cursor on ``(at, id)``; ``before`` pages older entries."""

    at: datetime
    id: str

    def encode(self) -> str:
        return f"{self.at.isoformat()}|{self.id}"

    @classmethod
    def decode(cls, value: str) -> Cursor:
        at, separator, id = value.partition("|")
        if not separator or not id:
            raise ValueError("A timeline cursor is '<iso datetime>|<id>'.")
        return cls(at=datetime.fromisoformat(at), id=id)

    def before(self, field: str) -> dict[str, Any]:
        """Queryset lookups for rows at or before this cursor's time.

        Ties at the same instant are settled by the merge on ``(at, id)``.
        """

        return {f"{field}__lte": self.at}

    def admits(self, entry: TimelineEntry) -> bool:
        return entry.order_key < (self.at, self.id)


@runtime_checkable
class TimelineSource(Protocol):
    """One event kind's projection onto the record timeline."""

    kind: str

    def eligible(self, model: type[models.Model]) -> bool:
        """Whether rows of ``model`` can carry this kind at all."""

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int,
    ) -> Iterable[TimelineEntry]:
        """Up to ``limit`` newest entries for ``record`` at or before ``before``; any order."""

    def count(self, record: models.Model, actor: Any) -> int:
        """How many entries ``record`` carries, for the scope chip."""


def timeline_sources() -> tuple[TimelineSource, ...]:
    """Resolve every contributed source; each hook returns an iterable of them."""

    sources: dict[str, TimelineSource] = {}
    for hook in resolve_hooks(HOOK_SETTING):
        for source in hook():
            if not isinstance(source, TimelineSource):
                raise ImproperlyConfigured(f"{HOOK_SETTING}: {source!r} is not a TimelineSource.")
            if source.kind in sources:
                raise ImproperlyConfigured(f"{HOOK_SETTING}: two sources claim the kind {source.kind!r}.")
            sources[source.kind] = source
    return tuple(sources.values())
