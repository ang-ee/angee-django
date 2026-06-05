"""Default URL routes for Angee runtime hosts."""

from __future__ import annotations

from django.urls import path

from angee.graphql.views import csrf_token, graphql_endpoint

urlpatterns = [
    path("auth/csrf/", csrf_token),
    path("graphql/<str:schema_name>/", graphql_endpoint),
]
"""HTTP GraphQL endpoints keyed by schema name, plus the CSRF endpoint."""
