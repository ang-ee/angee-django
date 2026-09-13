"""Shared groups, their canonical role-bearing roster, and group-thread binding.

``Group`` is a shared tree rather than a user-scoped organising list. Its
``visibility`` is a persisted fact whose owner maintains the public
``reader@auth/user:*`` tuple. ``Membership`` is the one canonical roster edge;
confirmed rows reach platform users live through the schema's filtered relation
paths. A party without a platform user remains valid and grants nothing.

``ThreadSpace`` contributes the group audience onto the existing
``messaging.Thread`` row through a same-row many-to-many field. It is an abstract
extension, never another first-class runtime model.

Group visibility remains an explicit wildcard tuple. Roster and thread-group
reach are live field-backed facts, so bulk edits, cascades, and identity changes
cannot leave mirrored relationship rows stale.
"""

from __future__ import annotations

from typing import Any, cast

from django.db import models, transaction
from django.utils.text import slugify
from rebac import (
    RelationshipTuple,
    SubjectRef,
    delete_relationships,
    to_object_ref,
    write_relationships,
)
from rebac.types import RelationshipFilter

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, HierarchyMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.parties.mixins import ScoredLinkMixin
from angee.spaces.managers import GroupManager, MembershipManager

PUBLIC_READER_RELATION = "reader"
"""Wildcard-subject relation opening a public group to authenticated actors."""

_EVERYONE = SubjectRef.of("auth/user", "*")
_NEVER_LOADED = object()


class Group(HierarchyMixin, SqidMixin, AuditMixin, AngeeModel):
    """A shared group with one canonical roster and an unscoped parent tree."""

    _loaded_visibility: object
    runtime = True
    sqid_prefix = "grp_"

    class GroupVisibility(models.TextChoices):
        """Whether membership is required to read the group and its threads."""

        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    name = models.CharField(max_length=200)
    slug = models.SlugField(blank=True, unique=True)
    description = models.TextField(blank=True, default="")
    visibility = StateField(
        choices_enum=GroupVisibility,
        default=GroupVisibility.PRIVATE,
    )

    objects = GroupManager()

    class Meta(HierarchyMixin.Meta):
        """Django options carrying the hierarchy path index and REBAC identity."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "spaces/group"

    def __str__(self) -> str:
        """Return the group name for Django displays."""

        return self.name

    @classmethod
    def from_db(cls, db: Any, field_names: Any, values: Any) -> Group:
        """Load a row and snapshot visibility for save-time tuple reconciliation."""

        instance = super().from_db(db, field_names, values)
        instance._loaded_visibility = (
            instance.visibility if "visibility" in field_names else _NEVER_LOADED
        )
        return cast(Group, instance)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the group with a unique slug and reconcile its reader tuple."""

        adding = self._state.adding
        loaded_visibility = getattr(self, "_loaded_visibility", _NEVER_LOADED)
        with transaction.atomic():
            if not self.slug:
                self.slug = self._available_slug()
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = tuple(
                        dict.fromkeys((*update_fields, "slug"))
                    )
            super().save(*args, **kwargs)
            if (
                adding
                or loaded_visibility is _NEVER_LOADED
                or loaded_visibility != self.visibility
            ):
                self._reconcile_public_reader()
        self._loaded_visibility = self.visibility

    def _available_slug(self) -> str:
        """Return the first name-derived slug unused in the shared Group table."""

        slug_field = self._meta.get_field("slug")
        max_length = slug_field.max_length or 50
        base = slugify(self.name)[:max_length] or "group"
        owner_model = slug_field.model
        candidates = owner_model.system_queryset(lock=())
        if self.pk is not None:
            candidates = candidates.exclude(pk=self.pk)

        candidate = base
        suffix = 1
        while candidates.filter(slug=candidate).exists():
            suffix += 1
            ending = f"-{suffix}"
            candidate = f"{base[: max_length - len(ending)]}{ending}"
        return candidate

    def _reconcile_public_reader(self) -> None:
        """Grant or revoke this group's ``reader@auth/user:*`` relationship."""

        resource = to_object_ref(self)
        if self.visibility == self.GroupVisibility.PUBLIC:
            write_relationships(
                [
                    RelationshipTuple(
                        resource=resource,
                        relation=PUBLIC_READER_RELATION,
                        subject=_EVERYONE,
                    )
                ]
            )
            return
        delete_relationships(
            RelationshipFilter(
                resource_type=resource.resource_type,
                resource_id=resource.resource_id,
                relation=PUBLIC_READER_RELATION,
                subject_type=_EVERYONE.subject_type,
                subject_id=_EVERYONE.subject_id,
            )
        )


class Membership(ScoredLinkMixin, SqidMixin, AuditMixin, AngeeModel):
    """One party's role-bearing roster row in a shared group.

    Confirmation resolves live through the canonical ``Person.user`` identity
    link. External parties without platform users remain roster rows but grant no
    REBAC access.
    """

    runtime = True
    sqid_prefix = "mbr_"

    class MembershipRole(models.TextChoices):
        """The access role a confirmed roster row grants on its group."""

        OWNER = "owner", "Owner"
        MODERATOR = "moderator", "Moderator"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    group = models.ForeignKey(
        "spaces.Group",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="space_memberships",
    )
    role = StateField(choices_enum=MembershipRole, default=MembershipRole.MEMBER)
    objects = MembershipManager()

    class Meta:
        """Django options for the canonical group roster edge."""

        abstract = True
        ordering = ("group", "role", "sqid")
        rebac_resource_type = "spaces/membership"
        constraints = (
            models.UniqueConstraint(
                fields=("group", "party"),
                name="uq_%(app_label)s_membership_group_party",
            ),
        )

    def __str__(self) -> str:
        """Return a readable membership description for Django displays."""

        return f"{self.party_id}∈{self.group_id} ({self.role})"


class ThreadSpace(models.Model):
    """Group audience contributed onto ``messaging.Thread`` as a same-row field."""

    extends = "messaging.Thread"

    groups = models.ManyToManyField(
        "spaces.Group",
        blank=True,
        related_name="threads",
    )

    class Meta:
        """Abstract same-row extension composed into ``messaging.Thread``."""

        abstract = True
