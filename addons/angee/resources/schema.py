"""GraphQL contribution: the resource import ledger, surfaced in the platform console.

``resources`` owns the import ledger, so it owns the query that lists it
(``resourceLedger``) and contributes a "Resources" section into the platform
console (see ``addons/angee/resources/web``). The listing is gated on ``read`` over
the platform's ``platform/explorer`` anchor — resources can't own that gate
itself (``iam`` depends on ``resources``, so referencing ``angee/role`` here would
cycle), and the section only exists inside the platform console, so one
platform-admin role resolves the whole surface. The reference is by name, not a
Python import, so no dependency edge is added.
"""

from __future__ import annotations

import strawberry
from django.apps import apps
from rebac import ObjectRef, current_actor
from rebac.backends import backend
from rebac.field_visibility import check_field_access

_EXPLORER = ObjectRef("platform/explorer", "default")

# Hard cap on a single ledger fetch: the ledger grows one row per imported
# resource per addon, so an unbounded read would be a latency/payload surface.
_LEDGER_LIMIT = 500


@strawberry.type
class ResourceLedgerRow:
    """One row of the resource import ledger."""

    id: str
    source_addon: str
    source_path: str
    tier: str
    content_hash: str
    target_model: str
    target_id: str
    loaded_at: str


@strawberry.type
class ResourceQuery:
    """Read-only resource ledger query for the platform console."""

    @strawberry.field
    def resource_ledger(self) -> list[ResourceLedgerRow]:
        """Return the import ledger for platform readers, else empty."""

        actor = current_actor()
        if actor is None:
            return []
        if not check_field_access(
            backend(),
            subject=actor,
            action="read",
            resource=_EXPLORER,
        ).allowed:
            return []
        resource = apps.get_model("resources", "Resource")
        rows = resource.objects.ledger_page(limit=_LEDGER_LIMIT)
        return [
            ResourceLedgerRow(
                id=str(row.pk),
                source_addon=row.source_addon,
                source_path=row.source_path,
                tier=str(row.tier),
                content_hash=row.content_hash,
                target_model=row.target_model,
                target_id=row.target_id,
                loaded_at=row.loaded_at.isoformat() if row.loaded_at else "",
            )
            for row in rows
        ]


schemas = {
    "console": {
        "query": [ResourceQuery],
        "types": [ResourceLedgerRow],
    },
}
"""GraphQL contributions installed by the resources addon (console surface)."""
