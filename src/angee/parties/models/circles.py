"""Native circles model owners for the contacts domain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

from angee.base.fields.text import ColorField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.hierarchy import HierarchyMixin
from angee.base.models.ownership import OwnerMixin
from angee.parties.managers import (
    CircleManager,
)
from angee.parties.mixins import MembershipDecisionMixin

if TYPE_CHECKING:
    pass


class Circle(HierarchyMixin, OwnerMixin, AngeeDataModel, concrete=True):
    """A private, overlapping grouping of parties — how the owner organises people.

    Circles are the curated counterpart of :class:`Folder` (which mirrors a synced
    address book): "Family", "Inner Circle", a climbing crew. A party may belong to
    many circles (:class:`CircleMember`), and circles nest as a tree — overlap
    comes from multi-membership, never from multiple parents. The tree is
    :class:`~angee.base.models.hierarchy.HierarchyMixin`'s materialized path, so "everyone in
    this circle including sub-circles" is an indexed prefix scan
    (``Circle.objects.subtree_of(circle)``), and ``hierarchy_scope_fields`` keeps a
    personal circle tree from straddling owners: the transferable ``owner`` scopes
    the tree, so a transfer never leaves a circle's parent or children under
    another owner.

    Circles are an organising surface only — they never gate visibility or
    sharing; REBAC stays the one authorization owner.
    """

    sqid_prefix = "cir_"
    hierarchy_scope_fields = ("owner",)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    color = ColorField(max_length=32, blank=True, default="")
    """Display colour for chips/dots; presentation only."""

    icon = models.CharField(max_length=128, blank=True, default="")
    """Icon registry name for navigation; presentation only."""

    position = models.PositiveIntegerField(default=0)

    objects = CircleManager()

    class Meta(HierarchyMixin.Meta):
        """Django model options carrying the hierarchy path index."""

        abstract = True
        ordering = ("position", "name", "sqid")
        rebac_resource_type = "parties/circle"

    def __str__(self) -> str:
        """Return the circle name for Django displays."""

        return self.name


class CircleMember(MembershipDecisionMixin, AuditMixin, AngeeDataModel, concrete=True):
    """A party's reviewable membership of one circle.

    Membership is a plain decision row so a suggester (community detection, an
    org-domain rule, an LLM) can propose a weak membership for review instead of
    silently filing people; a human decision records acceptance at full confidence.
    One row per ``(circle, party)`` — re-suggesting an existing membership updates
    the row rather than duplicating it.
    """

    sqid_prefix = "cme_"

    circle = models.ForeignKey(
        "parties.Circle",
        on_delete=models.CASCADE,
        related_name="members",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="circle_members",
    )

    class Meta:
        """Django model options for the circle-membership source model."""

        abstract = True
        ordering = ("circle", "sqid")
        rebac_resource_type = "parties/circle_member"
        constraints = (
            models.UniqueConstraint(
                fields=("circle", "party"),
                name="uq_circle_member",
            ),
        )

    def __str__(self) -> str:
        """Return a readable membership description for Django displays."""

        return f"{self.party_id}∈{self.circle_id}"
