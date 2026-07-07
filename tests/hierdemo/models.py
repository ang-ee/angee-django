"""Concrete demo models exercising :class:`~angee.base.mixins.HierarchyMixin`.

``HierNode`` is a plain (REBAC-untyped) tree so the pure hierarchy behaviours —
inclusive ``subtree_of`` / exclusive ``ancestors_of``, padded-segment prefix
correctness, single-UPDATE reparent cascade, cycle rejection, and the
pattern-ops index — read without company/actor scaffolding. ``ScopedHierNode``
adds :class:`~angee.iam.models.CompanyScopedMixin` so the cross-company parent
rejection has a real ``company`` field to test against. Both are concrete
(``abstract = False``) so pytest-django builds their tables on demand; the app
carries no ``addon.toml``, so the composer never sees them.
"""

from __future__ import annotations

from django.db import models

from angee.base.mixins import HierarchyMixin, HierarchyQuerySet
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.iam.models import CompanyScopedMixin


class HierNodeQuerySet(HierarchyQuerySet["HierNode"], AngeeQuerySet["HierNode"]):
    """Angee queryset for the plain hierarchy demo, with the subtree scopes."""


class ScopedHierNodeQuerySet(HierarchyQuerySet["ScopedHierNode"], AngeeQuerySet["ScopedHierNode"]):
    """Angee queryset for the company-scoped hierarchy demo."""


HierNodeManager = AngeeManager.from_queryset(HierNodeQuerySet)
ScopedHierNodeManager = AngeeManager.from_queryset(ScopedHierNodeQuerySet)


class HierNode(HierarchyMixin, AngeeDataModel):
    """A plain materialized-path tree node for the hierarchy behaviour tests."""

    sqid_prefix = "hnd_"

    name = models.CharField(max_length=100, blank=True, default="")

    objects = HierNodeManager()

    class Meta(HierarchyMixin.Meta):
        """Concrete demo node carrying the inherited prefix-serving index."""

        abstract = False
        app_label = "hierdemo"
        db_table = "test_hierdemo_node"
        ordering = ("path", "sqid")


class ScopedHierNode(HierarchyMixin, CompanyScopedMixin, AngeeDataModel):
    """A company-scoped tree node for the cross-company parent rejection test."""

    sqid_prefix = "shn_"

    hierarchy_scope_fields = ("company",)

    name = models.CharField(max_length=100, blank=True, default="")

    objects = ScopedHierNodeManager()

    class Meta(HierarchyMixin.Meta):
        """Concrete company-scoped demo node carrying the inherited index."""

        abstract = False
        app_label = "hierdemo"
        db_table = "test_hierdemo_scoped_node"
        ordering = ("path", "sqid")
