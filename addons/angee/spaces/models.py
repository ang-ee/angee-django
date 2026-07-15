"""Shared groups, their canonical role-bearing roster, and group-thread binding.

``Group`` is a shared tree rather than a user-scoped organising list. Its
``visibility`` is a persisted fact whose owner maintains the public
``reader@auth/user:*`` tuple. ``Membership`` is the one canonical roster edge;
confirmed rows whose party resolves to a platform ``Person.user`` maintain one
direct group-role tuple. The granted platform user is persisted on the membership
so later revocation uses the identity that actually received the grant, never a
fresh interpretation of a changed Party. A party without a platform user remains
a valid roster row and deliberately grants nothing.

``ThreadSpace`` contributes the nullable group pointer onto the existing
``messaging.Thread`` row. It is an abstract same-row extension, never another
runtime model or table.

**Pitfalls.** Group visibility and membership grants are reconciled by instance
``save()``; bulk APIs that bypass it (``bulk_create`` and ``QuerySet.update``)
also bypass tuple upkeep. The spaces-owned Person receiver covers ordinary
``Person.save()`` changes to ``user_id``, but a queryset update or a database
``SET_NULL`` cascade does not emit that signal; those paths can leave a stale
tuple and require an explicit repair pass. Reconciliation is idempotent and runs
inside the row transaction, but it does not lock a membership loaded earlier;
concurrent saves can interleave and the last committed writer's state wins.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.conf import settings
from django.db import models, transaction
from rebac import (
    RelationshipTuple,
    SubjectRef,
    delete_relationship,
    delete_relationships,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.types import RelationshipFilter

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, HierarchyMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.parties.mixins import ScoredLinkMixin
from angee.spaces.managers import GroupManager

PUBLIC_READER_RELATION = "reader"
"""Wildcard-subject relation opening a public group to authenticated actors."""

_EVERYONE = SubjectRef.of("auth/user", "*")
_NEVER_LOADED = object()
_MEMBERSHIP_RECONCILE_FIELDS = (
    "party_id",
    "role",
    "is_confirmed",
    "is_dismissed",
    "granted_user_id",
)


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
    slug = models.SlugField(unique=True)
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
        rebac_id_attr = "sqid"

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
        return instance

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the group and reconcile its public-reader tuple atomically."""

        adding = self._state.adding
        loaded_visibility = getattr(self, "_loaded_visibility", _NEVER_LOADED)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if (
                adding
                or loaded_visibility is _NEVER_LOADED
                or loaded_visibility != self.visibility
            ):
                self._reconcile_public_reader()
        self._loaded_visibility = self.visibility

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

    Confirmation resolves the party through the canonical ``Person.user`` identity
    link and grants exactly one matching role on the group. External parties with
    no platform user are intentionally retained in the roster but grant no REBAC
    relationship.
    """

    runtime = True
    sqid_prefix = "mbr_"

    class MembershipRole(models.TextChoices):
        """The access role a confirmed roster row grants on its group."""

        OWNER = "owner", "Owner"
        MODERATOR = "moderator", "Moderator"
        MEMBER = "member", "Member"

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
    granted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    """The platform user that received this membership's current role grant."""
    granted_user_id: Any

    class Meta:
        """Django options for the canonical group roster edge."""

        abstract = True
        ordering = ("group", "role", "sqid")
        rebac_resource_type = "spaces/membership"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("group", "party"),
                name="uq_%(app_label)s_membership_group_party",
            ),
        )

    def __str__(self) -> str:
        """Return a readable membership description for Django displays."""

        return f"{self.party_id}∈{self.group_id} ({self.role})"

    @classmethod
    def from_db(cls, db: Any, field_names: Any, values: Any) -> Membership:
        """Load a row and snapshot only fields that can change its role grant."""

        instance = super().from_db(db, field_names, values)
        instance._loaded_reconcile_state = (
            tuple(getattr(instance, name) for name in _MEMBERSHIP_RECONCILE_FIELDS)
            if all(name in field_names for name in _MEMBERSHIP_RECONCILE_FIELDS)
            else _NEVER_LOADED
        )
        return instance

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row and reconcile its confirmed role relationship atomically.

        Unrelated saves return after the row write. A relevant save resolves the
        desired user once, persists that derived FK with the row, revokes only the
        previously stored user, and writes the desired tuple before the surrounding
        transaction can commit.
        """

        adding = self._state.adding
        loaded_state = getattr(self, "_loaded_reconcile_state", _NEVER_LOADED)
        current_state = self._reconcile_state()
        should_reconcile = adding or loaded_state is _NEVER_LOADED or loaded_state != current_state

        with transaction.atomic():
            previous_user_id = self._previous_granted_user_id(adding, loaded_state)
            desired = self._role_subject() if should_reconcile and self.is_confirmed and not self.is_dismissed else None
            desired_user_id = desired[0].pk if desired is not None else None
            if should_reconcile and self.granted_user_id != desired_user_id:
                self.granted_user_id = desired_user_id
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = [*update_fields, "granted_user"]

            super().save(*args, **kwargs)
            if should_reconcile:
                self._reconcile_role_relationship(
                    previous_user_id=previous_user_id,
                    desired_subject=desired[1] if desired is not None else None,
                )

        self._loaded_reconcile_state = self._reconcile_state()

    def _reconcile_state(self) -> tuple[Any, ...]:
        """Return the persisted inputs that own this membership's role grant."""

        return tuple(getattr(self, name) for name in _MEMBERSHIP_RECONCILE_FIELDS)

    def _previous_granted_user_id(self, adding: bool, loaded_state: object) -> Any:
        """Return the stored pre-save grantee, querying only without a snapshot."""

        if adding:
            return None
        if loaded_state is not _NEVER_LOADED:
            return loaded_state[-1]  # type: ignore[index]
        return (
            type(self)._base_manager.filter(pk=self.pk).values_list("granted_user_id", flat=True).first()
        )

    def _reconcile_role_relationship(
        self,
        *,
        previous_user_id: Any,
        desired_subject: SubjectRef | None,
    ) -> None:
        """Make direct group roles match the saved row from inside ``save()``."""

        previous_subject = self._subject_for_granted_user(previous_user_id)
        if previous_subject is not None:
            self._revoke_role_relationships(previous_subject)
        if desired_subject is None:
            return
        write_relationships(
            [
                RelationshipTuple(
                    resource=to_object_ref(self.group),
                    relation=self.role,
                    subject=desired_subject,
                )
            ]
        )

    def revoke_role_relationships(self) -> None:
        """Revoke every group role from the persisted user that received it."""

        subject = self._subject_for_granted_user(self.granted_user_id)
        if subject is not None:
            self._revoke_role_relationships(subject)

    def _revoke_role_relationships(self, subject: SubjectRef) -> None:
        """Revoke all roster role variants for one resolved platform subject."""

        resource = to_object_ref(self.group)
        for relation in GROUP_ROLE_RELATIONS:
            delete_relationship(
                RelationshipTuple(
                    resource=resource,
                    relation=relation,
                    subject=subject,
                )
            )

    def _role_subject(self) -> tuple[Any, SubjectRef] | None:
        """Resolve the current party through the parties-owned user accessor."""

        party_model = apps.get_model("parties", "Party")
        user = party_model.objects.user_for(self.party)
        if user is None:
            return None
        return user, to_subject_ref(user)

    def _subject_for_granted_user(self, user_id: Any) -> SubjectRef | None:
        """Resolve a subject from the persisted granted-user FK, never from Party."""

        if user_id is None:
            return None
        user_model = self._meta.get_field("granted_user").remote_field.model
        user = user_model._base_manager.filter(pk=user_id).first()
        return None if user is None else to_subject_ref(user)


GROUP_ROLE_RELATIONS = tuple(Membership.MembershipRole.values)
"""Direct group relations a confirmed roster membership may own."""


class ThreadSpace(AngeeModel):
    """Group pointer contributed onto ``messaging.Thread`` as a same-row field."""

    extends = "messaging.Thread"

    group = models.ForeignKey(
        "spaces.Group",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="threads",
    )

    class Meta:
        """Abstract same-row extension composed into ``messaging.Thread``."""

        abstract = True
