"""The contribution contract behind the record timeline.

A kind is one event vocabulary an addon owns (``workflows.run``,
``provenance.change``). It names the source models whose saves re-project it,
enumerates its source rows for a backfill, and projects one source row into the
complete, current set of events that row's change affects. The timeline addon
stores those projections in the ``Event`` ledger and reads them back in one query;
bodies, verbs and composers stay with the owning addon's aside tab.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.db import models
from django.db.models import Q
from django.dispatch import receiver

from angee.impl import resolve_hooks

HOOK_SETTING = "ANGEE_TIMELINE_KINDS"


@dataclass(frozen=True)
class EventData:
    """One occurrence as its owner projects it: a pointer, never a body."""

    key: str
    """The event's stable identity, unique across kinds: ``<kind>:<target>:<occurrence>``."""
    kind: str
    at: datetime
    record: models.Model
    """The record the event is on; the ledger stores its canonical reference."""
    target_model: str
    target_id: str
    """The row the event's drill-in opens, by model label and public id."""
    title: str
    detail: str = ""
    actor: str = ""
    """The acting subject as a stored subject reference; empty for the system."""
    actor_label: str = ""
    parent_key: str | None = None
    """The event this one folds under (a run's decision folds under the run)."""
    needs: tuple[Any, ...] = field(default_factory=tuple)
    """User ids the event waits on right now; empty once resolved."""


@runtime_checkable
class TimelineKind(Protocol):
    """One event kind's owner: what it projects, from which rows."""

    id: str
    """The kind's id, namespaced by its owner (``workflows.run``); also its ``timeline#kinds`` child id."""
    sequence: int
    """Where the kind sorts among the scope chips."""
    models: tuple[str, ...]
    """Model labels whose saves re-project the kind; the web refetches on their live changes too."""

    def eligible(self, model: type[models.Model]) -> bool:
        """Whether rows of ``model`` can carry this kind at all."""

    def rows(self) -> Iterable[models.Model]:
        """Every source row, for a backfill; the projection of each is ``events``."""

    def events(self, row: models.Model) -> Iterable[EventData]:
        """The complete, current events that a change to ``row`` affects.

        ``row`` is an instance of one of ``models``; the result is idempotent (syncing
        it twice changes nothing) and complete for its targets (a key missing from
        it is removed from the ledger).
        """


@lru_cache(maxsize=1)
def timeline_kinds() -> tuple[TimelineKind, ...]:
    """Resolve every contributed kind in sequence order; each hook returns an iterable of them."""

    kinds: dict[str, TimelineKind] = {}
    for hook in resolve_hooks(HOOK_SETTING):
        for kind in hook():
            if not isinstance(kind, TimelineKind):
                raise ImproperlyConfigured(f"{HOOK_SETTING}: {kind!r} is not a TimelineKind.")
            if kind.id in kinds:
                raise ImproperlyConfigured(f"{HOOK_SETTING}: two kinds claim the id {kind.id!r}.")
            kinds[kind.id] = kind
    return tuple(sorted(kinds.values(), key=lambda kind: (kind.sequence, kind.id)))


@receiver(setting_changed)
def _forget_kinds(*, setting: str, **_: Any) -> None:
    if setting == HOOK_SETTING:
        timeline_kinds.cache_clear()


def actor_label(user: Any) -> str:
    """A user's display label for a line."""

    if user is None:
        return ""
    return str(user.get_full_name() or user.get_username())


@dataclass(frozen=True)
class Cursor:
    """A keyset cursor on the page order ``(at desc, kind asc, pk desc)``."""

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

    def predicate(self) -> Q:
        """Events strictly after this cursor on the page order."""

        return (
            Q(at__lt=self.at)
            | Q(at=self.at, kind__gt=self.kind)
            | Q(at=self.at, kind=self.kind, pk__lt=self.pk)
        )
