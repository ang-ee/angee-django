"""GraphQL schema contributions for Angee tags.

Exposed on the admin console. :class:`Tag` gets ordinary CRUD; the polymorphic
:class:`TagAssignment` edge is **not** an ordinary resource insert, so it is
written through the authored ``tag`` / ``untag`` mutations and read through the
authored ``tag_assignments`` query — all thin dispatchers into
:class:`~angee.tags.models.TagAssignmentManager`, which writes under the calling
actor: the edge's own gates require write on the tagged record, whoever curates
the vocabulary.

Owners of taggable models compose :class:`TaggedNode` onto their console node
type to read a row's tags as a batched, actor-scoped relation list, and
:func:`tags_input_extensions` onto that resource's insert and set inputs, so a
record form edits ``tags`` like any field and the row's save writes them
(:class:`~angee.tags.models.TaggedModel`).
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from rebac import current_actor
from rebac.graphql.strawberry_django import optimize
from strawberry import auto
from strawberry.types import get_object_definition
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.queryset import run_type_get_queryset

from angee.base.refs import canonical_link_path
from angee.base.scoping import read_scoped_queryset
from angee.graphql.data import AngeeHasuraWriteBackend, declared_hasura_resource_fields, hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode

Tag = apps.get_model("tags", "Tag")
TagAssignment = apps.get_model("tags", "TagAssignment")

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


_TAG_EDGES_ATTR = "_angee_tag_edges"
"""Where the batched prefetch parks each row's readable tag edges."""


def _tag_edges_prefetch(info: strawberry.Info) -> models.Prefetch:
    """Prefetch each selected row's readable tag edges with their tags, once per page.

    Both querysets are actor-scoped, so an edge whose tag or record the actor
    cannot read never reaches the row, and the inner tag queryset carries the
    selected ``TagType`` hints. The REBAC optimizer stamps and rescopes the outer lookup.
    A multi-table child's edges key on its canonical ancestor, which declares
    ``tag_assignments``, so the lookup climbs the child's parent links to it.
    """

    actor = current_actor()
    field = cast(StrawberryDjangoField, info._field)
    tags = optimize(run_type_get_queryset(read_scoped_queryset(Tag, actor), field.django_type, info), info)
    edges = read_scoped_queryset(TagAssignment, actor).by_tag().prefetch_related(
        models.Prefetch("tag", queryset=tags),
    )
    links = canonical_link_path(field.origin_django_type.model) if field.origin_django_type else ()
    return models.Prefetch("__".join((*links, "tag_assignments")), queryset=edges, to_attr=_TAG_EDGES_ATTR)


def _prefetched_tag_edges(row: models.Model) -> Any:
    """Return the edges the batched prefetch parked on ``row``'s canonical ancestor, if it ran."""

    owner: models.Model | None = row
    for link in canonical_link_path(type(row)):
        owner = owner._state.fields_cache.get(link) if owner is not None else None
    return getattr(owner, _TAG_EDGES_ATTR, None) if owner is not None else None


@strawberry.type
class TaggedNode:
    """Project a model's tags as a relation list on its node type.

    Compose alongside the node base, e.g. ``class FileType(TaggedNode, AngeeNode)``,
    or through a console ``type_extensions`` donor when the node type is shared
    with another schema; the model composes :class:`~angee.tags.models.TaggedModel`.
    With :func:`tags_input_extensions` on the resource's inputs, resource metadata
    projects ``tags`` as a writable ``list`` relation to ``tags.Tag``, which a
    form edits as its standard to-many chips field and saves with the row.
    """

    @strawberry_django.field(prefetch_related=[_tag_edges_prefetch])
    def tags(self) -> list[TagType]:
        """Return the actor-readable tags on this row, in the tag vocabulary's order."""

        row = cast(Any, self)
        edges = _prefetched_tag_edges(row)
        if edges is None:
            # An unoptimized root (a mutation payload, a hand-resolved row): one
            # actor-scoped read for this row alone.
            edges = TagAssignment.objects.for_record(row).by_tag().rebac_select_related("tag")
        return cast(list[TagType], [edge.tag for edge in edges])


@strawberry.input
class TagsInput:
    """The wanted tags of a written row, by public id; omitted, its tags stay as they are."""

    tags: list[PublicID] | None = strawberry.UNSET


def tags_input_extensions(resource: Any) -> list[type]:
    """Return ``tags`` donors for ``resource``'s insert and set inputs, for an owner's ``input_extensions``.

    The write backend hands the value to the written row's
    :meth:`~angee.tags.models.TaggedModel.apply_input_extensions` in the row's
    transaction, and resource metadata marks the field writable.
    """

    donors: list[type] = []
    for surface, accepted in (
        (resource.insert_input_type, resource.insertable_fields),
        (resource.set_input_type, resource.updatable_fields),
    ):
        # A resource that takes no inserts (or no updates) has no such input to extend.
        if surface is None or not accepted:
            continue
        name = get_object_definition(surface, strict=True).name
        donors.append(strawberry.input(name=name, extend=True)(type(f"{name}_tags", (TagsInput,), {
            "__doc__": f"The ``tags`` a ``{name}`` write saves with its row.",
        })))
    return donors


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
        """Return the tag assignments on one target row the actor reads, with their tags."""

        rows = TagAssignment.objects.for_target(target_type, str(target_id)).rebac_select_related("tag")
        return cast(list[TagAssignmentType], list(rows))


@strawberry.type
class TagMutation:
    """Authored writes for the polymorphic tag edge (not an ordinary resource insert)."""

    @strawberry.mutation(name="tag")
    def tag(self, target_type: str, target_id: PublicID, tag_ids: list[PublicID]) -> list[TagAssignmentType]:
        """Attach each tag in ``tag_ids`` to a target row the actor writes (idempotent per edge)."""

        assignments = TagAssignment.objects.attach(target_type, str(target_id), [str(tag_id) for tag_id in tag_ids])
        return cast(list[TagAssignmentType], assignments)

    @strawberry.mutation(name="untag")
    def untag(self, target_type: str, target_id: PublicID, tag_ids: list[PublicID]) -> bool:
        """Detach each tag in ``tag_ids`` from a target row the actor writes."""

        TagAssignment.objects.detach(target_type, str(target_id), [str(tag_id) for tag_id in tag_ids])
        return True


schemas = {
    "console": {
        "query": [TagQuery, _TAG_RESOURCE.query],
        "mutation": [TagMutation, _TAG_RESOURCE.mutation],
        "types": [TagType, TagAssignmentType, *_TAG_RESOURCE.types],
    },
}
