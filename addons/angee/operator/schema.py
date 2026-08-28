"""GraphQL bridge field for the local Angee operator daemon."""

from __future__ import annotations

import strawberry
from rebac import ObjectRef, current_actor
from rebac.backends import backend
from rebac.field_visibility import check_field_access

from angee.operator.daemon import OperatorDaemon

_CONNECTION = ObjectRef("operator/connection", "default")


@strawberry.type
class OperatorConnectionInfo:
    """Browser-visible endpoint and bearer for the operator daemon."""

    endpoint: str
    token: str


@strawberry.type
class OperatorQuery:
    """Operator daemon bridge queries."""

    @strawberry.field
    def operator_connection(self) -> OperatorConnectionInfo | None:
        """Return the daemon connection for operator admins, else ``None``.

        ``None`` is the single "no access" shape: the actor lacks ``read`` on the
        operator connection (no operator-admin role / not a platform admin), or no
        token could be minted (the daemon is unconfigured or unreachable). The
        admin bearer stays server-side; the browser receives a short-lived, scoped
        token minted per actor, so a leaked browser token expires and never
        carries root access.
        """

        actor = current_actor()
        if actor is None:
            return None
        allowed = check_field_access(
            backend(),
            subject=actor,
            action="read",
            resource=_CONNECTION,
        )
        if not allowed.allowed:
            return None
        daemon = OperatorDaemon.from_settings()
        token = daemon.mint_token(str(actor.object))
        if token is None:
            return None
        return OperatorConnectionInfo(endpoint=daemon.endpoint, token=token)


schemas = {
    "console": {
        "query": [OperatorQuery],
        "types": [OperatorConnectionInfo],
    },
}
"""GraphQL contributions installed by the operator addon (console surface)."""
