"""Addon-owned source queries and presentation for the record timeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar, Protocol, runtime_checkable

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.db import models
from django.dispatch import receiver

from angee.base.hooks import resolve_hooks

HOOK_SETTING = "ANGEE_TIMELINE_KINDS"
type RecordIdentity = tuple[type[models.Model], int]


@dataclass(frozen=True)
class Presentation:
    """The small display payload, hydrated only for the selected source rows."""

    title: str
    detail: str = ""
    actor: str = ""


class TimelineKind(ABC):
    """One addon-owned branch of the record chronology.

    A branch selects one row per occurrence on a record, with its owner's read
    policy applied. SQL owns ordering, folding and attention before pagination;
    presentation loads only the selected source rows. Source observation exists
    solely to invalidate open record reads after commit; it stores no projection.
    """

    id: str
    sequence: int
    source_models: tuple[str, ...] | Mapping[str, Iterable[str]]
    explicit_publication = False
    occurrence = "1"
    at = models.F("created_at")

    def observes(self, model: type[models.Model]) -> bool:
        return any(owner._meta.label in self.source_models for owner in (model, *model._meta.get_parent_list()))

    def relevant(self, model: type[models.Model], update_fields: Iterable[str] | None) -> bool:
        if update_fields is None or not isinstance(self.source_models, Mapping):
            return True
        fields: set[str] = set()
        for owner in (model, *model._meta.get_parent_list()):
            for field_name in self.source_models.get(owner._meta.label, ()):
                field = owner._meta.get_field(field_name)
                if not isinstance(field, models.Field):
                    raise ImproperlyConfigured(
                        f"Timeline kind {self.id!r} must observe a stored field: {owner._meta.label}.{field_name}."
                    )
                fields.update((field.name, field.attname))
        return not fields.isdisjoint(update_fields)

    @abstractmethod
    def eligible(self, model: type[models.Model]) -> bool:
        """Whether this record model can carry the kind."""

    @abstractmethod
    def occurs(self, record: models.Model) -> bool:
        """Whether this record has structural occurrences, before authorizing a branch.

        Query the source's record predicate through its unscoped base manager.
        Share that predicate with ``queryset`` at its manager/queryset owner;
        document any deliberately broader structural population on the kind.
        Return only existence: no permission scope, system context or audit row.
        A positive result never grants read access; ``queryset`` still owns that.
        """

    @abstractmethod
    def queryset(self, record: models.Model, actor: Any) -> models.QuerySet:
        """Readable occurrences on this record, one per source primary key."""

    @abstractmethod
    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        """Describe the selected source rows, batching any additional display dependencies."""

    def waiting_on(self, record: models.Model, actor: Any) -> models.QuerySet | None:
        """Own attention, or return None when no attention is possible on this record.

        Before building a distinct attention authorization scope, probe candidates
        through the source's unscoped base manager using the same record predicate
        as ``occurs`` and ``queryset``. Return only existence: no permission scope,
        system context or audit row. A negative probe returns None; a positive
        probe still requires the attention owner's unchanged authorization.
        The reader correlates only its existence to an already readable source row.
        Use structural base-manager rows without an audit bypass or repeated read
        scope; retain any distinct authorization the attention rule requires.
        """
        return None

    def fold(self, record: models.Model) -> tuple[Any, Any] | None:
        """Content type and primary key expressions for a possible parent target."""
        return None

    def targets(self, row: Any) -> Iterable[models.Model]:
        """Resolve source rows inside the invalidation owner's audited system context."""
        return (row,)

    @abstractmethod
    def records(self, row: Any) -> Iterable[RecordIdentity]:
        """Resolve record identities in that same audited invalidation context."""


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
    """A selected user's display label, empty when no actor is recorded."""
    return str(user) if user is not None else ""




@runtime_checkable
class TimelineSubject(Protocol):
    """Optional model declaration controlling admission to its own timeline."""

    timeline_subject: ClassVar[bool] = True


def timeline_subject(model: type[models.Model]) -> bool:
    """Whether a model's rows have a timeline of their own.

    A record declares ``timeline_subject = False`` when its rows are bookkeeping read
    through another record (a sync link's history is evidence on its target), so no kind
    admits them to the chronology.
    """

    return bool(model.timeline_subject if isinstance(model, TimelineSubject) else TimelineSubject.timeline_subject)
