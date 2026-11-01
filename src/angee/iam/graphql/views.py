"""HTTP views for serving named GraphQL schemas."""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse

from angee.base.models.identity import public_id_of
from angee.graphql.views import AngeeGraphQLView
from angee.iam.graphql.view_as import ViewAs
from graphql import GraphQLError, get_operation_ast, parse

logger = logging.getLogger("angee.iam.graphql.view_as")


class IAMGraphQLView(AngeeGraphQLView):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        view = lambda request: super(IAMGraphQLView, self).dispatch(request, *args, **kwargs)
        try:
            preview = ViewAs.from_request(request)
        except PermissionDenied:
            operation = request.GET.get("operationName")
            document = request.GET.get("query")
            if request.method == "POST":
                try:
                    payload = json.loads(request.body)
                    if isinstance(payload, dict):
                        operation = payload.get("operationName")
                        document = payload.get("query")
                except ValueError, UnicodeDecodeError:
                    pass
            if operation is None and isinstance(document, str):
                try:
                    selected = get_operation_ast(parse(document))
                    operation = selected.name.value if selected is not None and selected.name is not None else None
                except GraphQLError:
                    pass
            user = getattr(request, "user", None)
            logger.warning(
                "GraphQL view-as denied: real_actor=%s target=%s schema=%s operation=%s",
                public_id_of(user) if user is not None and user.is_authenticated else None,
                request.headers.get("X-Angee-View-As"),
                self.schema_name,
                operation if isinstance(operation, str) else None,
            )
            return JsonResponse(
                {"errors": [{"message": "View-as is not permitted.", "extensions": {"code": "FORBIDDEN"}}]}, status=403
            )
        return view(request) if preview is None else preview.dispatch(request, view, schema_name=self.schema_name)
