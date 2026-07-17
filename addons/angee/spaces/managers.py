"""Managers for shared spaces groups."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied

from angee.base.fields import enum_member_for
from angee.base.mixins import HierarchyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet, bind_actor
from angee.parties.mixins import LinkSource


class GroupQuerySet(HierarchyQuerySet, AngeeQuerySet):
    """Group read scopes with hierarchy subtree and ancestor traversal."""


class GroupManager(AngeeManager.from_queryset(GroupQuerySet)):  # type: ignore[misc]
    """Manager for unscoped shared group trees."""


class MembershipManager(AngeeManager):
    """Own confirmed manual roster writes and their role-grant reconciliation."""

    def add_confirmed(self, *, group: Any, party: Any, role: Any) -> Any:
        """Create or confirm one manual membership with the selected group role.

        The row and its derived REBAC grant commit in one transaction through the
        model's ``save()`` reconciliation. Re-adding an existing suggestion promotes
        that durable row instead of competing with its unique group/party key.
        """

        role_member = enum_member_for(self.model.MembershipRole, role)
        if role_member is None:
            raise ValidationError({"role": ["Select a valid membership role."]})

        with transaction.atomic():
            membership = (
                self.get_queryset()
                .lock_if_supported()
                .filter(group=group, party=party)
                .first()
            )
            if membership is not None:
                if not membership.has_access("write"):
                    raise PermissionDenied("write access to this membership is required")
                membership.role = role_member
                membership.confidence = 1.0
                membership.source = LinkSource.MANUAL
                membership.is_confirmed = True
                membership.is_dismissed = False
                membership.save(
                    update_fields=[
                        "role",
                        "confidence",
                        "source",
                        "is_confirmed",
                        "is_dismissed",
                        "updated_at",
                    ]
                )
                return membership

            actor = self.check_create({"group": (group,)})
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
