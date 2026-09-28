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
from rebac import actor_context
from rebac.actors import is_sudo
from strawberry.extensions import SchemaExtension
from strawberry.types.graphql import OperationType

from angee.base.errors import DomainError
from angee.base.identity import public_id_of

logger = logging.getLogger(__name__)
_DENIED = "View-as is not permitted."


class ViewAsReadOnly(DomainError):
    """A preview accepts queries only."""

    code = "VIEW_AS_READ_ONLY"


@dataclass(slots=True)
class ViewAs:
    """Typed ``request.view_as`` carrier shared with identity resolvers.

    The composed user manager may implement ``admit_view_as(actor, public_id)``:
    return an authorized target with no pinned actor or elevation, or ``None``.
    IAM owns that admission policy. Without the hook previews are refused.
    This adapter owns request binding and rollback only.
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
        admit = getattr(get_user_model()._default_manager, "admit_view_as", None)
        target = admit(real_user, target_id) if callable(admit) else None
        if target is None:
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
                "GraphQL view-as request: real_actor=%s target=%s schema=%s operations=%s",
                public_id_of(self.real_user), public_id_of(self.target), schema_name, tuple(self.operations),
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
        preview.operations.append(self.execution_context.operation_name or operation.value)
        if operation is not OperationType.QUERY:
            raise ViewAsReadOnly()
