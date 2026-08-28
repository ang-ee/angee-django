"""GraphQL schema contributions for Angee sequence.

Only :class:`Sequence` is exposed, on the admin console: it is numbering
configuration. ``SequenceCounter`` has no GraphQL surface — counters advance
server-side inside the drawing transaction, never through a mutation.
"""

from __future__ import annotations

import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode

Sequence = apps.get_model("sequence", "Sequence")


@strawberry_django.type(Sequence)
class SequenceType(AngeeNode):
    """Admin projection of one sequence's numbering configuration."""

    key: auto
    name: auto
    template: auto
    prefix: auto
    period_reset: auto
    preview_enabled: auto
    created_at: auto
    updated_at: auto


_SEQUENCE_RESOURCE = hasura_model_resource(
    SequenceType,
    model=Sequence,
    name="sequences",
    filterable=["id", "key", "name", "period_reset", "preview_enabled"],
    sortable=["key", "name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["period_reset", "preview_enabled"],
    writable=["key", "name", "template", "prefix", "period_reset", "preview_enabled"],
    id_column="sqid",
)


schemas = {
    "console": {
        "query": [_SEQUENCE_RESOURCE.query],
        "mutation": [_SEQUENCE_RESOURCE.mutation],
        "types": [SequenceType, *_SEQUENCE_RESOURCE.types],
    },
}
