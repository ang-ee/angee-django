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
from django.db.models import Q

from angee.impl import resolve_hooks

HOOK_SETTING = "ANGEE_TIMELINE_SOURCES"


@dataclass(frozen=True)
class TimelineEntry:
    """One line on a record's timeline: a pointer, never a body."""

    key: str
    """The event's stable identity, unique across kinds: ``<kind>:<target>:<occurrence>``."""
    at: datetime
    kind: str
    title: str
    pk: int
    """The source row's primary key; with ``at`` and ``kind`` it orders the page totally."""
    actor: str = ""
    detail: str = ""
    needs_me: bool = False
    target_model: str | None = None
    target_id: str | None = None
    folded: str = ""
    """What happened inside this event, summarised (a run's "3 changes · 5 decisions")."""

    @property
    def order_key(self) -> tuple[float, str, int]:
        """Ascending page order: newest first, then kind, then the newest row."""
        return (-self.at.timestamp(), self.kind, -self.pk)


@dataclass(frozen=True)
class Cursor:
    """A keyset cursor on the total order ``(at desc, kind asc, pk desc)``."""

    at: datetime
    kind: str
    pk: int

    def encode(self) -> str:
        return f"{self.at.isoformat()}|{self.kind}|{self.pk}"

    @classmethod
    def decode(cls, value: str) -> Cursor:
        at, kind, pk = value.split("|", 2)
        if not kind or not pk.isdigit():
            raise ValueError("A timeline cursor is '<iso datetime>|<kind>|<pk>'.")
        return cls(at=datetime.fromisoformat(at), kind=kind, pk=int(pk))

    @classmethod
    def after(cls, entry: TimelineEntry) -> Cursor:
        return cls(at=entry.at, kind=entry.kind, pk=entry.pk)

    def predicate(self, kind: str, at_field: str, pk_field: str = "pk") -> Q:
        """Rows of ``kind`` strictly older than this cursor on the total order."""

        older = Q(**{f"{at_field}__lt": self.at})
        if kind > self.kind:
            return older | Q(**{at_field: self.at})
        if kind == self.kind:
            return older | Q(**{at_field: self.at, f"{pk_field}__lt": self.pk})
        return older

    def admits(self, entry: TimelineEntry) -> bool:
        """Whether ``entry`` comes after this cursor on the page order."""
        return entry.order_key > (-self.at.timestamp(), self.kind, -self.pk)


@runtime_checkable
class TimelineSource(Protocol):
    """One event kind's projection onto the record timeline."""

    kind: str
    """The kind's id, namespaced by its owner (``workflows.run``); also its ``timeline#kinds`` child id."""
    sequence: int
    """Where the kind sorts among the scope chips."""
    models: tuple[str, ...]
    """Model labels whose live changes refetch the rail."""

    def eligible(self, model: type[models.Model]) -> bool:
        """Whether rows of ``model`` can carry this kind at all."""

    def entries(
        self, record: models.Model, actor: Any, *, before: Cursor | None, limit: int, needs_me: bool,
    ) -> Iterable[TimelineEntry]:
        """Up to ``limit`` newest entries for ``record`` older than ``before``; any order.

        With ``needs_me`` only entries that wait on the actor; a kind with none yields nothing.
        """

    def count(self, record: models.Model, actor: Any) -> int:
        """How many entries ``record`` carries, for the scope chip."""


def timeline_sources() -> tuple[TimelineSource, ...]:
    """Resolve every contributed source in sequence order; each hook returns an iterable of them."""

    sources: dict[str, TimelineSource] = {}
    for hook in resolve_hooks(HOOK_SETTING):
        for source in hook():
            if not isinstance(source, TimelineSource):
                raise ImproperlyConfigured(f"{HOOK_SETTING}: {source!r} is not a TimelineSource.")
            if source.kind in sources:
                raise ImproperlyConfigured(f"{HOOK_SETTING}: two sources claim the kind {source.kind!r}.")
            sources[source.kind] = source
    return tuple(sorted(sources.values(), key=lambda source: (source.sequence, source.kind)))


def actor_label(user: Any) -> str:
    """A user's display label for a line."""

    if user is None:
        return ""
    return str(user.get_full_name() or user.get_username())
