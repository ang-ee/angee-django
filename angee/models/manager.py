"""Managers shared by Angee model primitives."""

from __future__ import annotations

from typing import cast

from django.db import models
from rebac.managers import RebacManager

from angee.models.query import AngeeQuerySet, AngeeUnscopedQuerySet

_AngeeManagerBase = RebacManager.from_queryset(AngeeQuerySet)


class AngeeManager[T: models.Model](_AngeeManagerBase[T]):
    """Manager backed by :class:`AngeeQuerySet`."""

    model: type[T]

    def get_queryset(self) -> AngeeQuerySet[T]:
        """Preserve the framework queryset API on the inherited native factory."""

        return cast(AngeeQuerySet[T], super().get_queryset())


_AngeeUnscopedManagerBase = models.Manager.from_queryset(AngeeUnscopedQuerySet)


class AngeeUnscopedManager[T: models.Model](_AngeeUnscopedManagerBase[T]):
    """Manager backed by :class:`AngeeUnscopedQuerySet`."""

    model: type[T]

    def get_queryset(self) -> AngeeUnscopedQuerySet[T]:
        """Preserve the framework queryset API on the inherited native factory."""

        return cast(AngeeUnscopedQuerySet[T], super().get_queryset())
