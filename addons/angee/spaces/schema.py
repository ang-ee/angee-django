"""GraphQL resources for shared groups, rosters, and their group threads."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.actions import authorized_action_target, authorized_permission_target
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.ids import require_instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_many, actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.schema import UserType
from angee.messaging.models import NotificationPolicy
from angee.messaging.schema import FragmentType
from angee.parties.schema import PartyType

Group = apps.get_model("spaces", "Group")
Membership = apps.get_model("spaces", "Membership")
Party = apps.get_model("parties", "Party")
Thread = apps.get_model("messaging", "Thread")
Channel = apps.get_model("messaging", "Channel")
Vault = apps.get_model("knowledge", "Vault")

MembershipRole = Membership._meta.get_field("role").choices_enum
strawberry.enum(cast(Any, MembershipRole))


@strawberry_django.type(Group)
class SpaceGroupType(AngeeNode):
    """GraphQL projection of a shared group."""

    name: auto
    slug: auto
    description: auto
    visibility: auto
    created_at: auto
    updated_at: auto
    permissions = permissions_field(("write", "manage_roster"))

    parent: SpaceGroupType | None = actor_scoped_to_one("parent")
    owner: UserType | None = actor_scoped_to_one("owner")

    @strawberry_django.field
    def membership_roles(self) -> list[MembershipRole]:  # type: ignore[valid-type]
        """Project the roster manager's authorized role choices."""

        return Membership.objects.available_roles(group=self)


@strawberry_django.type(Membership)
class SpaceMembershipType(AngeeNode):
    """GraphQL projection of one role-bearing group roster row."""

    permissions = permissions_field(("write", "write__role", "delete", "set_notifications"))
    group: SpaceGroupType | None = actor_scoped_to_one("group")
    role: auto
    confidence: auto
    source: auto
    is_confirmed: auto
    is_dismissed: auto
    notification_policy: auto
    subtype_keys: list[str]
    created_at: auto
    updated_at: auto

    party: PartyType | None = actor_scoped_to_one("party")


@strawberry_django.type(Channel, name="ChannelType", extend=True)
class ChannelSpaceExtension:
    """Project the channel's team with the shared relation read guard."""

    team: SpaceGroupType | None = actor_scoped_to_one("team")


@strawberry_django.type(Vault, name="VaultType", extend=True)
class VaultSpaceExtension:
    """Project the vault's team with the shared relation read guard."""

    team: SpaceGroupType | None = actor_scoped_to_one("team")


@strawberry_django.type(Thread)
class SpaceThreadType(AngeeNode):
    """Read-only projection of a messaging group thread bound to a space."""

    title: FragmentType | None
    modality: auto
    message_count: auto
    last_message_at: auto
    created_at: auto
    updated_at: auto

    groups: list[SpaceGroupType] = actor_scoped_to_many("groups")


@strawberry.type
class SpacesMembershipMutation:
    """Human decisions on suggested roster rows."""

    @strawberry.mutation
    def add_space_membership(
        self,
        info: strawberry.Info,
        group_id: strawberry.ID,
        party_id: strawberry.ID,
        role: MembershipRole,  # type: ignore[valid-type]
    ) -> SpaceMembershipType:
        """Resolve the selected group and let its roster authorize the selected role."""

        group = authorized_permission_target(info, Group, group_id, "read")
        party = require_instance_for_id(
            Party,
            party_id,
            queryset=Party.objects.scoped(),
            not_found="party not found",
        )
        membership = Membership.objects.add_confirmed(
            group=group,
            party=party,
            role=role,
        )
        return cast(SpaceMembershipType, membership)

    @strawberry.mutation
    def confirm_membership(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
    ) -> SpaceMembershipType:
        """Confirm an authorized roster row; access follows its persisted flags."""

        membership = authorized_action_target(info, Membership, id, "write")
        membership.confirm()
        return cast(SpaceMembershipType, membership)

    @strawberry.mutation
    def dismiss_membership(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
    ) -> SpaceMembershipType:
        """Dismiss an authorized roster row; access follows its persisted flags."""

        membership = authorized_action_target(info, Membership, id, "write")
        membership.dismiss()
        return cast(SpaceMembershipType, membership)

    @strawberry.mutation
    def set_membership_notifications(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
        policy: NotificationPolicy,
        subtype_keys: list[str],
    ) -> SpaceMembershipType:
        """Let the roster holder choose its own team notification preference."""

        membership = authorized_permission_target(info, Membership, id, "set_notifications")
        membership.set_notifications(policy, subtype_keys)
        return cast(SpaceMembershipType, membership)


def _space_threads(info: strawberry.Info) -> object:
    """Return actor-scoped threads that are explicitly bound to at least one group."""

    del info
    return Thread.objects.filter(
        modality=Thread.Modality.GROUP,
        groups__isnull=False,
    ).distinct()


_GROUP_RESOURCE = hasura_model_resource(
    SpaceGroupType,
    model=Group,
    name="space_groups",
    filterable=["id", "name", "slug", "visibility", "parent", "created_at", "updated_at"],
    sortable=["name", "slug", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["visibility", "parent"],
    writable=["name", "slug", "description", "visibility", "parent"],
    field_id_decode={"parent": public_pk_decoder(Group)},
    write_backend=AngeeHasuraWriteBackend(Group, public_id_fields=("parent",)),
)
_MEMBERSHIP_RESOURCE = hasura_model_resource(
    SpaceMembershipType,
    model=Membership,
    name="space_memberships",
    filterable=[
        "id",
        "group",
        "party",
        "role",
        "source",
        "is_confirmed",
        "is_dismissed",
        "created_at",
        "updated_at",
    ],
    sortable=["group", "party", "role", "confidence", "created_at", "updated_at"],
    aggregatable=["id", "confidence"],
    groupable=["group", "party", "role", "source"],
    insertable=["group", "party", "role"],
    updatable=["role"],
    field_id_decode={
        "group": public_pk_decoder(Group),
        "party": public_pk_decoder(Party),
    },
    write_backend=AngeeHasuraWriteBackend(
        Membership,
        public_id_fields=("group", "party"),
    ),
)
_SPACE_THREAD_RESOURCE = hasura_model_resource(
    SpaceThreadType,
    model=Thread,
    # Spaces owns this secondary view over messaging.Thread because the audience
    # field is contributed by the spaces addon. The emitted SDL carries
    # `space_threads_bool_exp.groups`; group-by-audience remains intentionally
    # unavailable because grouping over M2M audience membership would fan rows out.
    model_label="spaces.GroupThread",
    name="space_threads",
    filterable=["id", "groups", "last_message_at", "created_at", "updated_at"],
    sortable=["last_message_at", "message_count", "created_at", "updated_at"],
    aggregatable=["id", "message_count"],
    groupable=["last_message_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"groups": public_pk_decoder(Group)},
    get_queryset=_space_threads,
)

_RESOURCE_TYPES = [
    *_GROUP_RESOURCE.types,
    *_MEMBERSHIP_RESOURCE.types,
    *_SPACE_THREAD_RESOURCE.types,
]

_SPACES_SCHEMA_BUCKET = {
    "query": [
        _GROUP_RESOURCE.query,
        _MEMBERSHIP_RESOURCE.query,
        _SPACE_THREAD_RESOURCE.query,
    ],
    "mutation": [
        SpacesMembershipMutation,
        _GROUP_RESOURCE.mutation,
        _MEMBERSHIP_RESOURCE.mutation,
        _SPACE_THREAD_RESOURCE.mutation,
    ],
    "types": [
        SpaceGroupType,
        SpaceMembershipType,
        SpaceThreadType,
        *_RESOURCE_TYPES,
    ],
}

schemas = {
    "public": {
        **_SPACES_SCHEMA_BUCKET,
    },
    "console": {
        **_SPACES_SCHEMA_BUCKET,
        # Channel and vault nodes are console types, so their team projection is too.
        "type_extensions": [ChannelSpaceExtension, VaultSpaceExtension],
        "subscription": [
            changes(Group, field="spaceGroupChanged"),
            changes(Membership, field="spaceMembershipChanged"),
        ],
    },
}
