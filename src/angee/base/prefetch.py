"""Batch per reader, answer per row: owner-computed native prefetches and the answers they remember."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import Any, ClassVar

from django.db import models
from django.db.models.query import EmptyQuerySet
from rebac import to_subject_ref
from rebac.actors import is_sudo
from rebac.relation_loading import relation_actor


class InstancePrefetch:
    """A relation its owner computes, read through Django's native instance-prefetch protocol.

    Assign an instance as a model class attribute and hint it as
    ``Prefetch("<attribute>", queryset=..., to_attr=...)``: Django reads the
    listed instances' related rows once, through :meth:`related`, and assigns each
    instance its rows (its row, or ``None``, when ``single``) to ``to_attr``.
    ``queryset`` is the hint's own queryset and ``actor`` the reader it names
    (REBAC's ``relation_actor``), or ``None`` without one or for an emptied hint
    (``require_rebac_queryset(..., actor=None)``), which reads for no reader.
    """

    single: ClassVar[bool] = True
    name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def related(
        self, instances: list[Any], queryset: models.QuerySet[Any], actor: Any,
    ) -> tuple[Iterable[Any], Callable[[Any], object], Callable[[Any], object]]:
        """Return the related rows, each row's key and each listed instance's key."""

        raise NotImplementedError

    def get_prefetch_querysets(
        self, instances: list[Any], querysets: list[models.QuerySet[Any]] | None = None,
    ) -> tuple[Iterable[Any], Callable[[Any], object], Callable[[Any], object], bool, str, bool]:
        """Django's protocol: one queryset and a ``to_attr`` select what :meth:`related` reads."""

        if querysets is None or len(querysets) != 1:
            raise ValueError(f"Prefetching {self.name or type(self).__name__} requires one queryset and to_attr.")
        queryset = querysets[0]
        actor = None if isinstance(queryset, EmptyQuerySet) else relation_actor(queryset)
        rows, row_key, instance_key = self.related(instances, queryset, actor)
        return rows, row_key, instance_key, self.single, self.name, False


def prefetched(row: models.Model, prefetch: models.Prefetch) -> Any:
    """``row``'s ``prefetch.to_attr``, read for ``row`` alone when no list read prefetched it."""

    if prefetch.to_attr is None:
        raise ValueError(f"Reading {prefetch.prefetch_through} for one row requires a to_attr.")
    if not hasattr(row, prefetch.to_attr):
        models.prefetch_related_objects([row], prefetch)
    return getattr(row, prefetch.to_attr)


class ReaderMemo:
    """Answers one reader's batch read remembered on each row it answers.

    A batch reads once for a reader and remembers each listed row's answer under
    the reader's subject reference; the row's own method answers from it, and any
    other actor asks the owning rule again. Nothing is remembered without an actor,
    under sudo (whose answers bypass the reader's policy) or as ``None``, which
    :meth:`get` returns for a row with no answer.
    """

    def __init__(self, attribute: str) -> None:
        self.attribute = attribute
        """The row attribute holding the answers; one per owner, so an owner forgets only its own."""

    def remember(self, row: Any, key: Hashable, actor: Any, value: Any) -> None:
        """Keep ``value`` as ``row``'s answer ``key`` for ``actor``."""

        if actor is not None and value is not None and not is_sudo():
            vars(row).setdefault(self.attribute, {})[key, to_subject_ref(actor)] = value

    def get(self, row: Any, key: Hashable, actor: Any) -> Any:
        """``row``'s answer ``key`` remembered for ``actor``, or ``None``."""

        if actor is None:
            return None
        return vars(row).get(self.attribute, {}).get((key, to_subject_ref(actor)))

    def forget(self, row: Any) -> None:
        """Drop every answer this memo remembered on ``row``."""

        vars(row).pop(self.attribute, None)
