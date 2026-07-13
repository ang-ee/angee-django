"""GraphQL resources for shared groups, rosters, and their group threads."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    aggregate_queryset,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.messaging.schema import FragmentType
from angee.parties.schema import PartyType

Group = apps.get_model("spaces", "Group")
Membership = apps.get_model("spaces", "Membership")
Party = apps.get_model("parties", "Party")
Thread = apps.get_model("messaging", "Thread")


@strawberry_django.type(Group)
class SpaceGroupType(AngeeNode):
    """GraphQL projection of a shared group."""

    name: auto
    slug: auto
    description: auto
    visibility: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["parent_id"])
    def parent(self) -> SpaceGroupType | None:
        """Return the parent when the actor may read it, otherwise null."""

        parent_id = cast(Any, self).parent_id
        if parent_id is None:
            return None
        return cast("SpaceGroupType | None", Group.objects.filter(pk=parent_id).first())


@strawberry_django.type(Membership)
class SpaceMembershipType(AngeeNode):
    """GraphQL projection of one role-bearing group roster row."""

    group: SpaceGroupType | None
    role: auto
    confidence: auto
    source: auto
    is_confirmed: auto
    is_dismissed: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["party_id"])
    def party(self) -> PartyType | None:
        """Return the roster party when the actor may read it, otherwise null."""

        party_id = cast(Any, self).party_id
        if party_id is None:
            return None
        return cast("PartyType | None", Party.objects.filter(pk=party_id).first())


@strawberry_django.type(Thread)
class SpaceThreadType(AngeeNode):
    """Read-only projection of a messaging group thread bound to a space."""

    title: FragmentType | None
    modality: auto
    message_count: auto
    last_message_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["group_id"])
    def group(self) -> SpaceGroupType | None:
        """Return the bound group when the actor may read it, otherwise null."""

        group_id = cast(Any, self).group_id
        if group_id is None:
            return None
        return cast("SpaceGroupType | None", Group.objects.filter(pk=group_id).first())


@strawberry.type
class SpacesMembershipMutation:
    """Human decisions on suggested roster rows."""

    @strawberry.mutation
    def confirm_membership(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
    ) -> SpaceMembershipType:
        """Confirm a roster row and reconcile its role relationship."""

        del info
        membership = Membership.objects.all().from_public_id(str(id))
        if membership is None:
            raise ValueError("membership not found")
        membership.confirm()
        return cast(SpaceMembershipType, membership)

    @strawberry.mutation
    def dismiss_membership(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
    ) -> SpaceMembershipType:
        """Dismiss a roster row and revoke its role relationship."""

        del info
        membership = Membership.objects.all().from_public_id(str(id))
        if membership is None:
            raise ValueError("membership not found")
        membership.dismiss()
        return cast(SpaceMembershipType, membership)


def _space_threads(info: strawberry.Info) -> object:
    """Return actor-scoped threads that are explicitly bound to a group."""

    del info
    return Thread.objects.filter(
        modality=Thread.Modality.GROUP,
        group__isnull=False,
    )


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
    # The folded field is not present in messaging's emitted threads_bool_exp,
    # and resources have no downstream filter-extension seam. Keep this honest
    # secondary-view label until the framework gains a validated secondary-view
    # contract (tracked in the architecture backlog).
    model_label="spaces.GroupThread",
    name="space_threads",
    filterable=["id", "group", "last_message_at", "created_at", "updated_at"],
    sortable=["last_message_at", "message_count", "created_at", "updated_at"],
    aggregatable=["id", "message_count"],
    groupable=["group", "last_message_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"group": public_pk_decoder(Group)},
    get_queryset=_space_threads,
    get_aggregate_queryset=lambda info: aggregate_queryset(_space_threads(info)),
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
        "subscription": [
            changes(Group, field="spaceGroupChanged"),
            changes(Membership, field="spaceMembershipChanged"),
        ],
    },
}
