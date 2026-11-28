"""GraphQL schema contributions for impact probe."""

from __future__ import annotations

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode

ImpactProbe = apps.get_model("impact_probe", "ImpactProbe")


@strawberry_django.type(ImpactProbe)
class ImpactProbeType(AngeeNode):
    """GraphQL projection of one impactprobe."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["title"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    title: auto
    body: auto
    created_at: auto
    updated_at: auto


_IMPACTPROBE_RESOURCE = hasura_model_resource(
    ImpactProbeType,
    model=ImpactProbe,
    name="impact_probe",
    filterable=["id", "title", "updated_at"],
    sortable=["title", "updated_at", "created_at"],
    aggregatable=["id"],
    groupable=["updated_at"],
    writable=["title", "body"],
    write_backend=AngeeHasuraWriteBackend(ImpactProbe),
    id_column="sqid",
)


schemas = {
    "console": {
        "query": [_IMPACTPROBE_RESOURCE.query],
        "mutation": [_IMPACTPROBE_RESOURCE.mutation],
        "types": [ImpactProbeType, *_IMPACTPROBE_RESOURCE.types],
    },
}
