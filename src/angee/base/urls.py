"""Default backend URL routes for composed hosts."""

from __future__ import annotations

import importlib

from django.conf import settings
from django.urls import path
from django.utils.functional import SimpleLazyObject
from strawberry.django.views import GraphQLView


def get_schema() -> object:
    """Import the generated public GraphQL schema."""

    module = importlib.import_module(f"{settings.ANGEE_RUNTIME_MODULE}.schema")
    return module.schema


urlpatterns = [
    path(
        "graphql/",
        GraphQLView.as_view(
            schema=SimpleLazyObject(get_schema),
            graphql_ide=settings.ANGEE_GRAPHQL_IDE,
        ),
    ),
]
