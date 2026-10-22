"""Managers shared by Angee model primitives."""

from __future__ import annotations

from django.db import models
from rebac.managers import RebacManager

from angee.models.query import AngeeQuerySet, AngeeUnscopedQuerySet

_AngeeManagerBase = RebacManager.from_queryset(AngeeQuerySet)


class AngeeManager[T: models.Model](_AngeeManagerBase[T]):
    """Manager backed by :class:`AngeeQuerySet`."""

    model: type[T]


_AngeeUnscopedManagerBase = models.Manager.from_queryset(AngeeUnscopedQuerySet)


class AngeeUnscopedManager[T: models.Model](_AngeeUnscopedManagerBase[T]):
    """Manager backed by :class:`AngeeUnscopedQuerySet`."""

    model: type[T]
