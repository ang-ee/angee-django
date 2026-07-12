"""GraphQL schema contributions for Angee uom.

Both models are exposed on the admin console. Reads are open to any authenticated
actor (units are a shared catalogue); writes are admin-gated by ``permissions.zed``
and the resource write backend. The ``category`` foreign key projects a nested
``{id, …}`` display node (like storage's drive→backend relation) and accepts the
related category's public id on write.
"""

from __future__ import annotations

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode

UomCategory = apps.get_model("uom", "UomCategory")
Uom = apps.get_model("uom", "Uom")


@strawberry_django.type(UomCategory)
class UomCategoryType(AngeeNode):
    """Admin projection of one unit-of-measure category."""

    name: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Uom)
class UomType(AngeeNode):
    """Admin projection of one unit, with its category as a display node."""

    category: UomCategoryType
    name: auto
    ratio: auto
    offset: auto
    rounding: auto
    is_reference: auto
    is_archived: auto
    created_at: auto
    updated_at: auto


_CATEGORY_RESOURCE = hasura_model_resource(
    UomCategoryType,
    model=UomCategory,
    name="uom_categories",
    filterable=["id", "name"],
    sortable=["name", "created_at", "updated_at"],
    aggregatable=["id"],
    writable=["name"],
    id_column="sqid",
)

_UOM_RESOURCE = hasura_model_resource(
    UomType,
    model=Uom,
    name="uoms",
    filterable=["id", "name", "category", "is_reference", "is_archived"],
    sortable=["name", "ratio", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["category", "is_reference", "is_archived"],
    writable=["name", "category", "ratio", "offset", "rounding", "is_reference", "is_archived"],
    field_id_decode={"category": public_pk_decoder(UomCategory)},
    write_backend=AngeeHasuraWriteBackend(Uom, public_id_fields=("category",)),
    id_column="sqid",
)


schemas = {
    "console": {
        "query": [_CATEGORY_RESOURCE.query, _UOM_RESOURCE.query],
        "mutation": [_CATEGORY_RESOURCE.mutation, _UOM_RESOURCE.mutation],
        "types": [
            UomCategoryType,
            UomType,
            *_CATEGORY_RESOURCE.types,
            *_UOM_RESOURCE.types,
        ],
    },
}
