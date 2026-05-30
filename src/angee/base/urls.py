"""Default backend URL routes for composed hosts.

Each contributed schema is served over HTTP at ``graphql/<name>/`` by the view
in ``angee.base.views``. WebSocket transport for the same names is added by
``angee.base.asgi``.
"""

from __future__ import annotations

from django.urls import path

from angee.base.views import graphql_endpoint

urlpatterns = [
    path("graphql/<str:schema_name>/", graphql_endpoint),
]
