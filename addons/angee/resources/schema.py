"""GraphQL contribution: the resource import ledger, surfaced in the platform console.

``resources`` owns the import ledger, so it owns the resource that lists it (the
read-only Hasura ``resources`` resource) and contributes a "Resources" section
into the platform console (see ``addons/angee/resources/web``). The listing is
gated on ``read`` over the platform's ``platform/explorer`` anchor — resources
can't own that gate itself (``iam`` depends on ``resources``, so referencing
``angee/role`` here would cycle), and the section only exists inside the platform
console, so one platform-admin role resolves the whole surface. The reference is
by name, not a Python import, so no dependency edge is added.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import QuerySet
from rebac import ObjectRef
from strawberry import auto

from angee.graphql.access import actor_can_read
from angee.graphql.data import hasura_model_resource

_EXPLORER = ObjectRef("platform/explorer", "default")

Resource = apps.get_model("resources", "Resource")


@strawberry_django.type(Resource)
class ResourceLedgerType:
    """Read-only projection of one resource import-ledger row."""

    source_addon: auto
    source_path: auto
    tier: auto
    xref: auto
    content_hash: auto
    target_model: auto
    target_id: auto
    loaded_at: auto

    @strawberry_django.field
    def id(self) -> str:
        """Return the ledger row's primary-key identity."""

        return str(cast(Any, self).pk)


def _platform_can_read() -> bool:
    """Return whether the current actor may read the platform console surface.

    Mirrors the platform addon's ``read`` gate on the ``platform/explorer``
    anchor by name (no Python import), so the contributed ledger listing
    resolves for the same platform-admin role that resolves the rest of the
    console.
    """

    return actor_can_read(_EXPLORER)


def _ledger_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the import ledger for platform readers, else the empty set.

    Read-only and admin-scoped: a non-reader actor gets ``.none()`` rather than a
    forbidden error, matching the rest of the admin-only platform console.
    """

    del info
    if not _platform_can_read():
        return cast(QuerySet[Any], Resource.objects.none())
    return cast(QuerySet[Any], Resource.objects.all())


_LEDGER_RESOURCE = hasura_model_resource(
    ResourceLedgerType,
    model=Resource,
    name="resources",
    filterable=["source_addon", "source_path", "tier", "target_model", "xref"],
    sortable=["source_addon", "source_path", "tier", "target_model", "loaded_at"],
    aggregatable=["id"],
    groupable=["source_addon", "source_path", "tier"],
    get_queryset=_ledger_queryset,
    insert=False,
    update=False,
    delete=False,
    id_decode=lambda value: value,
    id_column="id",
    model_label="resources.Resource",
)


schemas = {
    "console": {
        "query": [_LEDGER_RESOURCE.query],
        "types": [*_LEDGER_RESOURCE.types],
    },
}
"""GraphQL contributions installed by the resources addon (console surface)."""
