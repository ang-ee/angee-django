"""Managers for shared spaces groups."""

from __future__ import annotations

from angee.base.mixins import HierarchyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet


class GroupQuerySet(HierarchyQuerySet, AngeeQuerySet):
    """Group read scopes with hierarchy subtree and ancestor traversal."""


class GroupManager(AngeeManager.from_queryset(GroupQuerySet)):  # type: ignore[misc]
    """Manager for unscoped shared group trees."""
