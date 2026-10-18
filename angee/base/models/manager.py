"""Managers shared by Angee model primitives."""

from __future__ import annotations

from typing import Any, cast

from django.db import models
from rebac.managers import RebacManager

from angee.base.models.query import AngeeQuerySet, AngeeUnscopedQuerySet


class AngeeManager(RebacManager.from_queryset(AngeeQuerySet)):  # type: ignore[misc]
    """Manager backed by :class:`AngeeQuerySet`."""

    def get_queryset(self) -> AngeeQuerySet[Any]:
        """Return the base Angee queryset for this manager's model."""

        return cast(AngeeQuerySet[Any], super().get_queryset())


class AngeeUnscopedManager(models.Manager.from_queryset(AngeeUnscopedQuerySet)):  # type: ignore[misc]
    """Manager backed by :class:`AngeeUnscopedQuerySet`."""

    def get_queryset(self) -> AngeeUnscopedQuerySet[Any]:
        """Return the base unscoped Angee queryset for this manager's model."""

        return cast(AngeeUnscopedQuerySet[Any], super().get_queryset())

