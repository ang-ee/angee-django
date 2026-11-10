"""Request-scoped, read-only GraphQL previews authorized on the target user."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from rebac import actor_context
from rebac.actors import is_sudo
from strawberry.extensions import SchemaExtension
from strawberry.types.graphql import OperationType

from angee.base.exceptions import DomainError
from angee.base.managers import AngeeManager
from angee.base.models.identity import public_id_of

if TYPE_CHECKING:
    from django.contrib.auth.models import _User

    class _ViewAsRequest(HttpRequest):
        view_as: ViewAs | None = None

logger = logging.getLogger(__name__)
_DENIED = "View-as is not permitted."


class ViewAsReadOnly(DomainError):
    """A preview accepts queries only."""

    code = "VIEW_AS_READ_ONLY"


@dataclass(slots=True)
class ViewAs:
    """Typed ``request.view_as`` carrier shared with identity resolvers.

    The composed user manager's ``admit_view_as(actor, public_id)`` returns an
    authorized target with no pinned actor or elevation, or ``None``. IAM owns
    admission; the base manager's default refuses previews.
    This adapter owns request binding and rollback only.
    """

    real_user: _User
    target: _User
    operations: list[str] = field(default_factory=list)

    @staticmethod
    def bound_to(request: HttpRequest | None) -> ViewAs | None:
        """Read this transport's optional request binding, including outside dispatch."""

        return request.__dict__.get("view_as") if request is not None else None

    @classmethod
    def from_request(cls, request: HttpRequest) -> ViewAs | None:
        target_id = request.headers.get("X-Angee-View-As")
        if target_id is None:
            return None
        # Django AuthenticationMiddleware supplies user; bare HttpRequest does not.
        real_user = getattr(request, "user", None)
        if real_user is None or not real_user.is_authenticated or is_sudo():
            raise PermissionDenied(_DENIED)
        manager = get_user_model()._default_manager
        target = manager.admit_view_as(real_user, target_id) if isinstance(manager, AngeeManager) else None
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

        request = cast("_ViewAsRequest", request)
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
        # Strawberry permits mapping contexts and custom objects carrying request.
        request = context.get("request") if isinstance(context, Mapping) else getattr(context, "request", None)
        preview = ViewAs.bound_to(request)
        if preview is None or self.execution_context.graphql_document is None:
            return
        try:
            operation = self.execution_context.operation_type
        except RuntimeError:
            # Strawberry owns missing/ambiguous operation selection and its 400.
            return
        preview.operations.append(self.execution_context.operation_name or operation.value)
        if operation is not OperationType.QUERY:
            raise ViewAsReadOnly()
