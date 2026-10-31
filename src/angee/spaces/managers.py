"""Managers for shared spaces groups."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.core.exceptions import ValidationError
from django.db import router, transaction
from rebac import PermissionDenied, SubjectRef, check_new, current_actor, database_context, to_subject_ref
from rebac.actors import is_sudo
from rebac.errors import MissingActorError
from rebac.resources import model_resource_type

from angee.base.fields.state import enum_member_for
from angee.base.managers import AngeeManager
from angee.base.models.hierarchy import HierarchyQuerySet
from angee.base.querysets import AngeeQuerySet
from angee.messaging.managers import ThreadManager, ThreadQuerySet

if TYPE_CHECKING:
    from angee.messaging.models import Thread
    from angee.spaces.models import Group, Membership


class GroupQuerySet[GroupT: Group](HierarchyQuerySet[GroupT], AngeeQuerySet[GroupT]):
    """Group read scopes with hierarchy subtree and ancestor traversal."""


_GroupManagerBase = AngeeManager.from_queryset(GroupQuerySet)


class GroupManager[GroupT: Group](_GroupManagerBase[GroupT]):
    """Keep a stable import identity for shared group tree managers."""


class MembershipQuerySet[MembershipT: Membership](AngeeQuerySet[MembershipT]):
    """Canonical roster selections shared by admission and the team audience."""

    def confirmed(self) -> Self:
        """Return accepted roster rows that have not been dismissed."""

        return self.filter(is_confirmed=True, is_dismissed=False)


_MembershipManagerBase = AngeeManager.from_queryset(MembershipQuerySet)


class MembershipManager[MembershipT: Membership](_MembershipManagerBase[MembershipT]):
    """Own confirmed manual roster writes under the roster's role permissions."""

    def check_role_create(self, *, group: Any, role: Any) -> SubjectRef:
        """Check the selected role through the same REBAC preflight as admission."""

        actor = current_actor()
        resource_type = model_resource_type(self.model)
        if actor is None:
            raise MissingActorError(f"Creating {resource_type or self.model._meta.label_lower} requires an actor.")
        relationships = {"group": (to_subject_ref(group),)}
        if role in (self.model.MembershipRole.MEMBER, self.model.MembershipRole.VIEWER):
            relationships[f"{role.value}_of"] = (to_subject_ref(group),)
        if resource_type is None or not check_new(
            subject=actor,
            action="create",
            resource_type=resource_type,
            relationships=relationships,
        ).allowed:
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type or self.model._meta.label_lower}")
        return actor

    def available_roles(self, *, group: Any) -> list[Any]:
        """Return roles the ambient actor may create on this group's roster."""

        if current_actor() is None:
            return []
        roles = []
        for role in self.model.MembershipRole:
            try:
                self.check_role_create(group=group, role=role)
            except PermissionDenied:
                continue
            roles.append(role)
        return roles

    def add_confirmed(self, *, group: Group, party: Any, role: Any) -> MembershipT:
        """Create or confirm one manual membership with the selected group role.

        Access follows the persisted row through live REBAC backing. Re-adding an
        existing suggestion confirms that row instead of competing with its unique
        group/party key.
        """

        return self._confirm(group=group, party=party, role=role)

    def provision_confirmed(self, *, group: Group, party: Any, role: Any, provisioned_by: Any) -> MembershipT:
        """Provision a roster seat under an explicit system context with attribution."""

        if not is_sudo():
            raise PermissionDenied("System context is required to provision a roster seat.")
        return self._confirm(group=group, party=party, role=role, provisioned_by=provisioned_by)

    def _confirm(self, *, group: Group, party: Any, role: Any, provisioned_by: Any = None) -> MembershipT:
        """Persist one confirmation for both authorized admission and system provisioning."""

        role_member = enum_member_for(self.model.MembershipRole, role)
        if role_member is None:
            raise ValidationError({"role": ["Select a valid membership role."]})

        using = self._db or group._state.db or router.db_for_write(self.model, instance=group)
        for related in (group, party):
            if related._state.db is not None and related._state.db != using:
                raise ValidationError("Roster members must share their group's database.")
        with database_context(using), transaction.atomic(using=using):
            type(group).system_queryset(lock=("self",)).using(using).get(pk=group.pk)
            memberships = self.get_queryset().using(using).lock_if_supported().filter(group=group, party=party)
            membership = memberships.first()
            if membership is not None:
                if provisioned_by is None and not membership.has_access("write"):
                    raise PermissionDenied("write access to this membership is required")
                if provisioned_by is not None and (
                    membership.role == role_member and membership.confidence == 1.0
                    and membership.is_confirmed and not membership.is_dismissed
                ):
                    return membership
                update_fields = ["confidence", "is_confirmed", "is_dismissed", "updated_at"]
                if membership.role != role_member:
                    membership.role = role_member
                    update_fields.append("role")
                membership.confidence = 1.0
                membership.is_confirmed = True
                membership.is_dismissed = False
                membership.save(using=using, update_fields=update_fields)
                return membership

            actor = self.check_role_create(group=group, role=role_member) if provisioned_by is None else None
            membership = self.model(
                group=group,
                party=party,
                role=role_member,
                confidence=1.0,
                is_confirmed=True,
                is_dismissed=False,
            )
            membership._state.db = using
            if provisioned_by is not None:
                membership.created_by_id = provisioned_by.pk
                membership.updated_by_id = provisioned_by.pk
            membership.full_clean(validate_unique=False, validate_constraints=False)
            membership.sudo(reason="spaces.membership.add_confirmed")
            membership.save(using=using)
            if actor is not None:
                membership.with_actor(actor)
            return membership


class ThreadSpaceQuerySet[ThreadT: Thread](ThreadQuerySet[ThreadT]):
    """Actor-scoped group-thread population contributed by Spaces."""

    def bound_to_groups(self) -> Self:
        """Select group conversations with at least one governed group audience."""

        return self.filter(modality=self.model.Modality.GROUP, groups__isnull=False).distinct()


ThreadSpaceManager = ThreadManager.from_queryset(ThreadSpaceQuerySet)
