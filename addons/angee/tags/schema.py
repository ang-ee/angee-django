"""GraphQL schema contributions for Angee tags.

Exposed on the admin console. :class:`Tag` gets ordinary CRUD; the polymorphic
:class:`TagAssignment` edge is **not** an ordinary resource insert, so it is
written through the authored ``tag`` / ``untag`` mutations and read through the
authored ``tag_assignments`` query — all thin dispatchers into
:class:`~angee.tags.models.TagAssignmentManager`, which owns the edge protocol:
target and tags resolve under the calling actor (nobody tags a row they cannot
read), and only the gate-less edge insert/delete elevates. The mutations are
additionally gated on the ``tags_admin`` role.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from rebac import ObjectRef
from strawberry import auto
from strawberry.permission import BasePermission

from angee.graphql.data import AngeeHasuraWriteBackend, declared_hasura_resource_fields, hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.iam.permissions import RolePermission

Tag = apps.get_model("tags", "Tag")
TagAssignment = apps.get_model("tags", "TagAssignment")

_TAGS_ADMIN_ROLE = ObjectRef("tags/role", "tags_admin")
"""Role whose effective members may curate the vocabulary and (un)tag rows."""


class TagsAdminPermission(RolePermission):
    """Allow actors who reach the ``tags_admin`` role.

    Platform admins (``angee/role:admin``) are implicit members through the
    role's ``member`` union in ``permissions.zed``.
    """

    role_ref = _TAGS_ADMIN_ROLE
    message = "Tags admin permission required."


_TAGS_ADMIN_CLASSES: list[type[BasePermission]] = [TagsAdminPermission]


@strawberry_django.type(Tag)
class TagType(AngeeNode):
    """Admin projection of one tag in the vocabulary."""

    name: auto
    color: auto
    is_archived: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(TagAssignment)
class TagAssignmentType(AngeeNode):
    """Admin projection of one polymorphic tag edge, with its target addressed publicly."""

    tag: TagType
    created_at: auto

    @strawberry_django.field(only=["content_type_id", "object_id"])
    def target_type(self) -> str:
        """Return the target row's REBAC resource type (e.g. ``parties/party``)."""

        return cast(Any, self).record_ref.resource_type

    @strawberry_django.field(only=["content_type_id", "object_id"])
    def target_id(self) -> PublicID:
        """Return the target row's public id."""

        return PublicID(cast(Any, self).record_public_id)


_TAG_EXTENSION_READ_FIELDS = declared_hasura_resource_fields(Tag, "hasura_readable_fields")
_TAG_EXTENSION_INSERT_FIELDS = declared_hasura_resource_fields(Tag, "hasura_insertable_fields")
_TAG_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(Tag, "hasura_updatable_fields")
_TAG_EXTENSION_WRITE_FIELDS = tuple(
    dict.fromkeys((*_TAG_EXTENSION_INSERT_FIELDS, *_TAG_EXTENSION_UPDATE_FIELDS))
)
_TAG_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name
    for name in _TAG_EXTENSION_WRITE_FIELDS
    if Tag._meta.get_field(name).is_relation
)

_TAG_FILTERABLE_FIELDS = (
    "id",
    "name",
    "is_archived",
    *_TAG_EXTENSION_READ_FIELDS,
)
_TAG_GROUPABLE_FIELDS = (
    "is_archived",
    *_TAG_EXTENSION_READ_FIELDS,
)
_TAG_BASE_WRITABLE_FIELDS = (
    "name",
    "color",
    "is_archived",
)

_TAG_RESOURCE = hasura_model_resource(
    TagType,
    model=Tag,
    name="tags",
    filterable=list(_TAG_FILTERABLE_FIELDS),
    sortable=["name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=list(_TAG_GROUPABLE_FIELDS),
    insertable=[*_TAG_BASE_WRITABLE_FIELDS, *_TAG_EXTENSION_INSERT_FIELDS],
    updatable=[*_TAG_BASE_WRITABLE_FIELDS, *_TAG_EXTENSION_UPDATE_FIELDS],
    declared_fields=_TAG_EXTENSION_READ_FIELDS,
    write_backend=AngeeHasuraWriteBackend(
        Tag,
        public_id_fields=_TAG_EXTENSION_PUBLIC_ID_FIELDS,
    ),
    id_column="sqid",
)


@strawberry.type
class TagQuery:
    """Reads for a target row's tag assignments."""

    @strawberry.field(name="tag_assignments")
    def tag_assignments(self, target_type: str, target_id: PublicID) -> list[TagAssignmentType]:
        """Return the tag assignments on one target row, REBAC-scoped by tag reach."""

        rows = TagAssignment.objects.for_target(target_type, str(target_id)).rebac_select_related("tag")
        return cast(list[TagAssignmentType], list(rows))


@strawberry.type
class TagMutation:
    """Authored writes for the polymorphic tag edge (not an ordinary resource insert)."""

    @strawberry.mutation(name="tag", permission_classes=_TAGS_ADMIN_CLASSES)
    def tag(self, target_type: str, target_id: PublicID, tag_ids: list[PublicID]) -> list[TagAssignmentType]:
        """Attach each tag in ``tag_ids`` to the target row (idempotent per edge)."""

        assignments = TagAssignment.objects.attach(target_type, str(target_id), [str(tag_id) for tag_id in tag_ids])
        return cast(list[TagAssignmentType], assignments)

    @strawberry.mutation(name="untag", permission_classes=_TAGS_ADMIN_CLASSES)
    def untag(self, target_type: str, target_id: PublicID, tag_ids: list[PublicID]) -> bool:
        """Detach each tag in ``tag_ids`` from the target row."""

        TagAssignment.objects.detach(target_type, str(target_id), [str(tag_id) for tag_id in tag_ids])
        return True


schemas = {
    "console": {
        "query": [TagQuery, _TAG_RESOURCE.query],
        "mutation": [TagMutation, _TAG_RESOURCE.mutation],
        "types": [TagType, TagAssignmentType, *_TAG_RESOURCE.types],
    },
}
