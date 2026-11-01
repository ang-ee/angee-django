"""Managers shared by Angee model primitives."""

from __future__ import annotations

from django.db import models

from angee.base.querysets import AngeeQuerySet, MergeableUnscopedQuerySet

_AngeeManagerBase = models.Manager.from_queryset(AngeeQuerySet)


class AngeeManager[T: models.Model](_AngeeManagerBase[T]):
    """Manager backed by :class:`AngeeQuerySet`."""

    model: type[T]


_MergeableUnscopedManagerBase = AngeeManager.from_queryset(MergeableUnscopedQuerySet)


class MergeableUnscopedManager[T: models.Model](_MergeableUnscopedManagerBase[T]):
    """Unfiltered manager for a model's declared merge identity capability."""

    model: type[T]
