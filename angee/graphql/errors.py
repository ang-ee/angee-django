"""GraphQL error normalization for Angee schemas."""

from __future__ import annotations

import strawberry
from rebac import MissingActorError, PermissionDenied
from strawberry.types.execution import ExecutionContext

from graphql import GraphQLError


class AngeeSchema(strawberry.Schema):
    """Strawberry schema that exposes stable REBAC denial codes."""

    def process_errors(
        self,
        errors: list[GraphQLError],
        execution_context: ExecutionContext | None = None,
    ) -> None:
        """Attach GraphQL error codes before Strawberry logs errors."""

        for error in errors:
            self._apply_rebac_code(error)
        super().process_errors(errors, execution_context)

    def _apply_rebac_code(self, error: GraphQLError) -> None:
        """Attach the code owned by a REBAC denial exception."""

        original = error.original_error
        if isinstance(original, MissingActorError):
            code = "UNAUTHENTICATED"
        elif isinstance(original, PermissionDenied):
            code = "PERMISSION_DENIED"
        else:
            return
        error.extensions = {**(error.extensions or {}), "code": code}
