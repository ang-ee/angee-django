"""Managers for shared spaces groups."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied, SubjectRef, check_new, current_actor, to_subject_ref
from rebac.errors import MissingActorError
from rebac.resources import model_resource_type

from angee.fields import enum_member_for
from angee.models import AngeeManager, AngeeQuerySet, HierarchyQuerySet

if TYPE_CHECKING:
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

        role_member = enum_member_for(self.model.MembershipRole, role)
        if role_member is None:
            raise ValidationError({"role": ["Select a valid membership role."]})

        with transaction.atomic():
            memberships = self.get_queryset().lock_if_supported().filter(group=group, party=party)
            membership = memberships.first()
            if membership is not None:
                if not membership.has_access("write"):
                    raise PermissionDenied("write access to this membership is required")
                update_fields = ["confidence", "is_confirmed", "is_dismissed", "updated_at"]
                if membership.role != role_member:
                    membership.role = role_member
                    update_fields.append("role")
                membership.confidence = 1.0
                membership.is_confirmed = True
                membership.is_dismissed = False
                membership.save(update_fields=update_fields)
                return membership

            actor = self.check_role_create(group=group, role=role_member)
            membership = self.model(
                group=group,
                party=party,
                role=role_member,
                confidence=1.0,
                is_confirmed=True,
                is_dismissed=False,
            )
            membership.full_clean(validate_unique=False, validate_constraints=False)
            membership.sudo(reason="spaces.membership.add_confirmed")
            membership.save()
            if actor is not None:
                membership.with_actor(actor)
            return membership
