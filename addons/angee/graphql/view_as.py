"""Request-scoped, read-only GraphQL previews authorized on the target user."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from rebac import actor_context, system_context, to_subject_ref
from rebac.actors import is_sudo
from strawberry.extensions import SchemaExtension
from strawberry.types.graphql import OperationType

from angee.base.identity import instance_from_public_id
from angee.base.scoping import system_queryset
from graphql import GraphQLError

logger = logging.getLogger(__name__)
_DENIED = "View-as is not permitted."


@dataclass(slots=True)
class ViewAs:
    """Typed ``request.view_as`` carrier shared with identity resolvers.

    IAM owns the target's person/role bound through ``preview_candidates`` and
    ``is_previewable``. This adapter owns admission, binding and rollback only.
    """

    real_user: AbstractBaseUser
    target: AbstractBaseUser
    operations: list[str] = field(default_factory=list)

    @classmethod
    def from_request(cls, request: HttpRequest) -> ViewAs | None:
        target_id = request.headers.get("X-Angee-View-As")
        if target_id is None:
            return None
        real_user = getattr(request, "user", None)
        if real_user is None or not real_user.is_authenticated or is_sudo():
            raise PermissionDenied(_DENIED)
        model = get_user_model()
        with system_context(reason="graphql.view_as.target"):
            target = instance_from_public_id(
                model,
                target_id,
                queryset=system_queryset(model).preview_candidates(),
            )
        if target is None:
            raise PermissionDenied(_DENIED)
        target.with_actor(to_subject_ref(real_user))
        if not target.has_access("view_as") or not target.is_previewable():
            raise PermissionDenied(_DENIED)
        return cls(real_user=real_user, target=target)

    def dispatch(
        self,
        request: HttpRequest,
        view: Callable[[HttpRequest], HttpResponse],
        *,
        schema_name: str,
    ) -> HttpResponse:
        """Bind both identity readers, roll back query writes, then audit once."""

        request.view_as = self
        request.user = self.target
        try:
            with actor_context(self.target), transaction.atomic():
                try:
                    return view(request)
                finally:
                    transaction.set_rollback(True)
        finally:
            request.user = self.real_user
            del request.view_as
            logger.info(
                "GraphQL view-as request",
                extra={
                    "real_actor": str(to_subject_ref(self.real_user)),
                    "target": str(to_subject_ref(self.target)),
                    "schema": schema_name,
                    "operation": tuple(self.operations),
                },
            )


class ViewAsReadOnlyExtension(SchemaExtension):
    """Reject non-query documents before execution, including HTTP subscriptions."""

    def on_parse(self) -> Iterator[None]:
        yield
        context = self.execution_context.context
        request = context.get("request") if isinstance(context, Mapping) else getattr(context, "request", None)
        preview = getattr(request, "view_as", None)
        if preview is None or self.execution_context.graphql_document is None:
            return
        operation = self.execution_context.operation_type
        preview.operations.append(operation.value)
        if operation is not OperationType.QUERY:
            raise GraphQLError("View-as is read-only.", extensions={"code": "VIEW_AS_READ_ONLY"})
