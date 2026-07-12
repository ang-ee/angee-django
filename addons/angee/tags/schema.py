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

from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, to_public_id
from angee.graphql.node import AngeeNode
from angee.iam.permissions import RolePermission

Company = apps.get_model("iam", "Company")
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

    @strawberry_django.field(only=["company_id"])
    def company(self) -> PublicID | None:
        """Return the owning company's public id, or ``None`` for shared vocabulary."""

        return to_public_id(Company, cast(Any, self).company_id)


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


_TAG_RESOURCE = hasura_model_resource(
    TagType,
    model=Tag,
    name="tags",
    filterable=["id", "name", "is_archived", "company"],
    sortable=["name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["company", "is_archived"],
    writable=["name", "color", "company", "is_archived"],
    field_id_decode={"company": public_pk_decoder(Company)},
    write_backend=AngeeHasuraWriteBackend(Tag, public_id_fields=("company",)),
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
