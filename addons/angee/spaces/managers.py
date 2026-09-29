"""Managers for shared spaces groups."""

from __future__ import annotations

from typing import Any, Self

from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied, SubjectRef, current_actor

from angee.base.fields import enum_member_for
from angee.base.mixins import HierarchyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.scoping import bind_actor
from angee.parties.mixins import LinkSource


class GroupQuerySet(HierarchyQuerySet, AngeeQuerySet):
    """Group read scopes with hierarchy subtree and ancestor traversal."""


class GroupManager(AngeeManager.from_queryset(GroupQuerySet)):  # type: ignore[misc]
    """Manager for unscoped shared group trees."""


class MembershipQuerySet(AngeeQuerySet):
    """Canonical roster selections shared by admission and the team audience."""

    def confirmed(self) -> Self:
        """Return accepted roster rows that have not been dismissed."""

        return self.filter(is_confirmed=True, is_dismissed=False)


class MembershipManager(AngeeManager.from_queryset(MembershipQuerySet)):  # type: ignore[misc]
    """Own confirmed manual roster writes under the roster's role permissions."""

    def check_role_create(self, *, group: Any, role: Any) -> SubjectRef:
        """Check the selected role through the same REBAC preflight as admission."""

        relationships = {"group": (group,)}
        if role in (self.model.MembershipRole.MEMBER, self.model.MembershipRole.VIEWER):
            relationships[f"{role.value}_of"] = (group,)
        return self.check_create(relationships)

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

    def add_confirmed(self, *, group: Any, party: Any, role: Any) -> Any:
        """Create or confirm one manual membership with the selected group role.

        Access follows the persisted row through live REBAC backing. Re-adding an
        existing suggestion confirms that row instead of competing with its unique
        group/party key.
        """

        role_member = enum_member_for(self.model.MembershipRole, role)
        if role_member is None:
            raise ValidationError({"role": ["Select a valid membership role."]})

        with transaction.atomic():
            memberships = (
                self.get_queryset()
                .lock_if_supported()
                .filter(group=group, party=party)
            )
            membership = memberships.first()
            if membership is not None:
                if not membership.has_access("write"):
                    raise PermissionDenied("write access to this membership is required")
                update_fields = ["confidence", "source", "is_confirmed", "is_dismissed", "updated_at"]
                if membership.role != role_member:
                    membership.role = role_member
                    update_fields.append("role")
                membership.confidence = 1.0
                membership.source = LinkSource.MANUAL
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
                source=LinkSource.MANUAL,
                is_confirmed=True,
                is_dismissed=False,
            )
            membership.full_clean(validate_unique=False, validate_constraints=False)
            membership.sudo(reason="spaces.membership.add_confirmed")
            membership.save()
            bind_actor(membership, actor)
            return membership
