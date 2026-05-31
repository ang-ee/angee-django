"""Default URL routes for Angee runtime hosts."""

from __future__ import annotations

from django.urls import path

from angee.base.views import graphql_endpoint

urlpatterns = [
    path("graphql/<str:schema_name>/", graphql_endpoint),
]
"""HTTP GraphQL endpoints keyed by schema name."""
