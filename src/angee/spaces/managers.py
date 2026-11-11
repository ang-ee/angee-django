"""Managers for shared spaces groups."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, TypedDict

from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import PermissionDenied, SubjectRef, check_new, current_actor, permission_expression, to_subject_ref
from rebac.actors import is_sudo
from rebac.errors import MissingActorError
from rebac.resources import model_resource_type

from angee.base.fields.state import enum_member_for
from angee.base.managers import AngeeManager
from angee.base.models.hierarchy import HierarchyQuerySet
from angee.base.querysets import AngeeQuerySet
from angee.messaging.managers import ThreadManager, ThreadQuerySet
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations

    from angee.messaging.models import Thread
    from angee.spaces.models import Group, Membership


class GroupQuerySet[GroupT: Group](HierarchyQuerySet[GroupT], AngeeQuerySet[GroupT]):
    """Group read scopes with hierarchy subtree and ancestor traversal."""


_GroupManagerBase = AngeeManager.from_queryset(GroupQuerySet)


class GroupManager[GroupT: Group](_GroupManagerBase[GroupT]):
    """Keep a stable import identity for shared group tree managers."""


class _MembershipCanRead(TypedDict):
    can_read: bool


class MembershipQuerySet[MembershipT: Membership](AngeeHistoryQuerySet[MembershipT]):
    """Canonical roster selections shared by admission and the team audience."""

    def confirmed(self) -> Self:
        """Return accepted roster rows that have not been dismissed."""

        return self.filter(is_confirmed=True, is_dismissed=False)

    def with_can_read(self) -> MembershipQuerySet[WithAnnotations[MembershipT, _MembershipCanRead]]:
        """Annotate ``can_read``: whether the ambient actor may read each membership.

        A record that displays another group's roster admits its seats' labels
        only; a seat's id and its holder's subject stay with ``can_read`` rows.
        """

        actor = current_actor()
        if actor is None:
            return self.annotate(can_read=models.Value(False))
        return self.annotate(can_read=permission_expression(self.model, "read", actor=actor))


_MembershipManagerBase = AngeeHistoryManager.from_queryset(MembershipQuerySet)


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
        """Provision one confirmed seat from a trusted system context, attributed to ``provisioned_by``."""

        if not is_sudo():
            raise PermissionDenied("System context is required to provision a roster seat.")
        return self._confirm(group=group, party=party, role=role, provisioned_by=provisioned_by)

    def _confirm(self, *, group: Group, party: Any, role: Any, provisioned_by: Any = None) -> MembershipT:
        """Persist one confirmed seat for authorized admission or system provisioning."""

        role_member = enum_member_for(self.model.MembershipRole, role)
        if role_member is None:
            raise ValidationError({"role": ["Select a valid membership role."]})

        with transaction.atomic():
            memberships = self.get_queryset().lock_if_supported().filter(group=group, party=party)
            membership = memberships.first()
            if membership is not None:
                if provisioned_by is None and not membership.has_access("write"):
                    raise PermissionDenied("write access to this membership is required")
                confirmed = {"confidence": 1.0, "is_confirmed": True, "is_dismissed": False}
                if (
                    provisioned_by is not None
                    and membership.role == role_member
                    and all(getattr(membership, name) == value for name, value in confirmed.items())
                ):
                    # Provisioning is idempotent; a manual confirmation always records its write.
                    return membership
                update_fields = [*confirmed, "updated_at"]
                if membership.role != role_member:
                    membership.role = role_member
                    update_fields.append("role")
                for name, value in confirmed.items():
                    setattr(membership, name, value)
                membership.save(update_fields=update_fields)
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
            if provisioned_by is not None:
                membership.created_by_id = provisioned_by.pk
                membership.updated_by_id = provisioned_by.pk
            membership.full_clean(validate_unique=False, validate_constraints=False)
            if actor is not None:
                membership.sudo(reason="spaces.membership.add_confirmed")
            membership.save()
            if actor is not None:
                membership.with_actor(actor)
            return membership


class ThreadSpaceQuerySet[ThreadT: Thread](ThreadQuerySet[ThreadT]):
    """Thread selections Spaces contributes through its group audience."""

    def bound_to_groups(self) -> Self:
        """Select group conversations bound to at least one governed group audience."""

        return self.filter(modality=self.model.Modality.GROUP, groups__isnull=False).distinct()


_ThreadSpaceManagerBase = ThreadManager.from_queryset(ThreadSpaceQuerySet)


class ThreadSpaceManager[ThreadT: Thread](_ThreadSpaceManagerBase[ThreadT]):
    """Spaces' thread manager, composed onto ``messaging.Thread`` as ``Thread.spaces``."""
