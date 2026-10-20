"""The contribution contract behind the record timeline.

A kind is one event vocabulary an addon owns (``workflows.run``,
``provenance.change``). It names the source models whose changes re-project it,
resolves affected targets, enumerates targets for a backfill, and projects each
target's complete, current set of events. The timeline addon
stores those projections in the ``Event`` ledger and reads them back in one query;
bodies, verbs and composers stay with the owning addon's aside tab.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.dispatch import receiver
from rebac import SubjectRef
from rebac.resources import model_for_subject_type

from angee.impl import resolve_hooks

HOOK_SETTING = "ANGEE_TIMELINE_KINDS"
type RecordIdentity = tuple[type[models.Model], int]


@dataclass(frozen=True)
class EventData:
    """One occurrence as its owner projects it: a pointer, never a body."""

    kind: str
    target: models.Model
    """The saved row whose complete projection is reconciled."""
    record: RecordIdentity
    """Model and primary key only; the ledger resolves canonical records in bulk."""
    at: datetime
    title: str
    occurrence: str = "1"
    detail: str = ""
    actor: str = ""
    """The acting subject as a stored subject reference; empty for the system."""
    actor_label: str = ""
    fold: models.Model | None = None
    """Fold under lines targeting this row on the same record; never an event key."""


class TimelineKind(ABC):
    """One event kind's owner: what it projects, from which rows."""

    id: str
    """The kind's id, namespaced by its owner (``workflows.run``); also its ``timeline#kinds`` child id."""
    sequence: int
    """Where the kind sorts among the scope chips."""
    source_models: tuple[str, ...] | Mapping[str, Iterable[str]]
    """Observed models, including MTI descendants. A mapping narrows partial saves
    to the named fields; creates, deletes and full saves always re-project.
    """
    explicit_publication = False
    """Observe owner publications only, without adapting model saves/deletes."""

    def observes(self, model: type[models.Model]) -> bool:
        """Observe the same inherited source facts when a concrete child saves."""

        return any(owner._meta.label in self.source_models for owner in (model, *model._meta.get_parent_list()))

    def relevant(self, model: type[models.Model], update_fields: Iterable[str] | None) -> bool:
        """Decide relevance before discovering potentially many dependent targets."""

        if update_fields is None or not isinstance(self.source_models, Mapping):
            return True
        fields = {
            name
            for owner in (model, *model._meta.get_parent_list())
            for field_name in self.source_models.get(owner._meta.label, ())
            for field in (owner._meta.get_field(field_name),)
            for name in (field.name, field.attname)
        }
        return not fields.isdisjoint(update_fields)

    def readable(self, actor: Any) -> Q:
        """All rows when the record alone owns read policy; override for independent targets."""

        return Q()

    def waiting_on(self, actor: Any) -> Q:
        """Readable targets whose owner waits on this actor; this implies readable()."""

        return Q(pk__in=())

    @abstractmethod
    def eligible(self, model: type[models.Model]) -> bool:
        """Whether rows of ``model`` can carry this kind at all.

        A kind that projects onto any touched record composes
        :func:`timeline_subject` so a model's own opt-out holds on write and on read.
        """

    @abstractmethod
    def rows(self) -> Iterable[models.Model]:
        """Every target row for a backfill; the projection of each is ``events``."""

    @abstractmethod
    def events(self, row: models.Model) -> Iterable[EventData]:
        """The complete, current events that a change to ``row`` affects.

        ``row`` is a target, resolved from a source through ``targets`` when needed.
        The result is complete for this target: missing occurrences are removed.
        """

    def targets(self, row: models.Model) -> Iterable[models.Model]:
        """Targets affected by this source, including targets whose projection is now empty.

        Include targets even when their current projection is empty. The ledger
        calls ``events(target)`` and reconciles each target, so removing a source
        relationship can remove its last event without inventing a tombstone.
        """

        return (row,)


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
    """A user's or stored subject's display label, with a retained-reference fallback."""

    if user is None or user == "":
        return ""
    if isinstance(user, str | SubjectRef):
        subject = SubjectRef.parse(user) if isinstance(user, str) else user
        resolved = model_for_subject_type(subject.subject_type)
        if resolved is None or subject.subject_id in ("", "*") or subject.optional_relation:
            return str(subject)
        model, id_attr = resolved
        row = model._base_manager.filter(**{id_attr: subject.subject_id}).first()
        return str(row) if row is not None else str(subject)
    return str(user)


def target_scope(rows: Any) -> Q:
    """Correlate a native, already actor-scoped target queryset with ledger rows."""

    return Q(target_model=rows.model._meta.label) & Q(Exists(rows.scoped().filter(pk=OuterRef("target_pk"))))


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


def timeline_subject(model: type[models.Model]) -> bool:
    """Whether a model's rows have a timeline of their own.

    A record declares ``timeline_subject = False`` when its rows are bookkeeping read
    through another record (a sync link's history is evidence on its target), so no kind
    writes events about them and no read admits any.
    """

    return bool(getattr(model, "timeline_subject", True))
