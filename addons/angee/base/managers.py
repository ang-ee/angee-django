"""Managers shared by Angee model primitives."""

from __future__ import annotations

from django.db import models
from rebac.managers import RebacManager

from angee.base.querysets import AngeeQuerySet, AngeeUnscopedQuerySet, MergeableUnscopedQuerySet

_AngeeManagerBase = RebacManager.from_queryset(AngeeQuerySet)


class AngeeManager[T: models.Model](_AngeeManagerBase[T]):
    """Manager backed by :class:`AngeeQuerySet`."""

    model: type[T]


_AngeeUnscopedManagerBase = models.Manager.from_queryset(AngeeUnscopedQuerySet)


class AngeeUnscopedManager[T: models.Model](_AngeeUnscopedManagerBase[T]):
    """Manager backed by :class:`AngeeUnscopedQuerySet`."""

    model: type[T]


_MergeableUnscopedManagerBase = AngeeUnscopedManager.from_queryset(MergeableUnscopedQuerySet)


class MergeableUnscopedManager[T: models.Model](_MergeableUnscopedManagerBase[T]):
    """Unfiltered manager for a model's declared merge identity capability."""

    model: type[T]
