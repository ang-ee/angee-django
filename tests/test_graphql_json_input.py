"""A ``JSON`` input nests at most ``MAX_JSON_INPUT_DEPTH`` levels, refused as bad input before execution."""

from __future__ import annotations

import inspect
import sys
from contextlib import ExitStack
from typing import Any

import pytest
import strawberry
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connections
from django.test.utils import CaptureQueriesContext
from rebac import system_context
from strawberry.scalars import JSON

from angee.graphql.schema import MAX_JSON_INPUT_DEPTH, GraphQLSchemas, nests_deeper_than
from angee.graphql.testing.documents import execute_schema
from angee.webforms import schema as webforms_schema
from angee.webforms.testing.models import Form, FormVersion
from tests.conftest import addon_schema

SAVE_VERSION = """
  mutation($form: ID!, $definition: JSON!) {
    insert_webform_versions_one(object: {
      form: $form, definition: $definition, languages: ["en"], default_language: "en"
    }) { id }
  }
"""


def nested_list(levels: int) -> Any:
    """A member inside ``levels`` arrays."""

    value: Any = "member"
    for _ in range(levels):
        value = [value]
    return value


def nested_object(levels: int) -> Any:
    """A member inside ``levels`` objects."""

    value: Any = "member"
    for _ in range(levels):
        value = {"a": value}
    return value


def literal(levels: int) -> str:
    """The GraphQL literal of ``nested_list(levels)``."""

    return "[" * levels + '"member"' + "]" * levels


def statements(document: str, variables: dict[str, Any]) -> tuple[Any, int]:
    """Save a version anonymously and count the SQL every configured database captured."""

    with ExitStack() as stack:
        captures = [stack.enter_context(CaptureQueriesContext(connection)) for connection in connections.all()]
        result = execute_schema(addon_schema(webforms_schema.schemas, "console"), document, variables)
    return result, sum(len(capture) for capture in captures)


@strawberry.type
class EchoQuery:
    @strawberry.field
    def echo(self, value: JSON) -> JSON:
        return value


@pytest.mark.django_db
@pytest.mark.parametrize("nested", [nested_list, nested_object])
def test_a_deep_json_input_is_refused_before_any_permission_check_or_query(nested: Any) -> None:
    """2,000 levels fail as bad input with no SQL; at the bound, the actor-scoped read of the form refuses."""

    with system_context(reason="json input tests setup"):
        form = Form.objects.create(name="Feedback")

    refused, refused_statements = statements(SAVE_VERSION, {"form": form.sqid, "definition": nested(2000)})
    admitted, admitted_statements = statements(
        SAVE_VERSION, {"form": form.sqid, "definition": nested(MAX_JSON_INPUT_DEPTH)},
    )

    assert refused.data is None
    assert refused.errors is not None
    assert [error.extensions for error in refused.errors] == [{"code": "BAD_USER_INPUT"}]
    assert f"nests deeper than {MAX_JSON_INPUT_DEPTH} levels" in refused.errors[0].message
    assert refused_statements == 0
    assert admitted.errors is not None
    assert admitted.errors[0].extensions["code"] == "VALIDATION"
    assert admitted.errors[0].extensions["validationErrors"].keys() == {"form"}
    assert admitted_statements > 0
    with system_context(reason="json input tests read"):
        assert not FormVersion._base_manager.filter(form=form).exists()


@pytest.mark.parametrize("nested", [nested_list, nested_object])
def test_a_json_variable_at_the_bound_reaches_the_resolver_unchanged(nested: Any) -> None:
    schema = addon_schema({"public": {"query": [EchoQuery]}}, "public")
    document = "query Echo($value: JSON!) { echo(value: $value) }"

    kept = schema.execute_sync(document, variable_values={"value": nested(MAX_JSON_INPUT_DEPTH)})
    refused = schema.execute_sync(document, variable_values={"value": nested(MAX_JSON_INPUT_DEPTH + 1)})

    assert kept.errors is None
    assert kept.data == {"echo": nested(MAX_JSON_INPUT_DEPTH)}
    assert refused.data is None
    assert refused.errors is not None
    assert refused.errors[0].extensions == {"code": "BAD_USER_INPUT"}


def test_a_json_literal_meets_the_same_bound() -> None:
    schema = addon_schema({"public": {"query": [EchoQuery]}}, "public")

    kept = schema.execute_sync("{ echo(value: %s) }" % literal(MAX_JSON_INPUT_DEPTH))
    refused = schema.execute_sync("{ echo(value: %s) }" % literal(MAX_JSON_INPUT_DEPTH + 1))

    assert kept.errors is None
    assert kept.data == {"echo": nested_list(MAX_JSON_INPUT_DEPTH)}
    assert refused.data is None
    assert refused.errors is not None
    assert refused.errors[0].extensions == {"code": "BAD_USER_INPUT"}


def test_nesting_is_measured_without_recursion() -> None:
    """With almost no frames left, a value at the bound and one far past it are both measured."""

    values = (nested_list(MAX_JSON_INPUT_DEPTH), nested_object(100_000))
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(len(inspect.stack(0)) + 20)
    try:
        measured = [nests_deeper_than(value, MAX_JSON_INPUT_DEPTH) for value in values]
    finally:
        sys.setrecursionlimit(limit)

    assert measured == [False, True]


def test_resource_conditions_keep_their_own_nesting_bound() -> None:
    """A stored condition is measured by the same walk against its own depth of 12."""

    schemas = GraphQLSchemas([])
    user_model = get_user_model()

    with pytest.raises(ValidationError, match="exceed nesting depth 12"):
        schemas.resource_filter(user_model, nested_object(13))
    with pytest.raises(ValidationError, match="has no filterable resource"):
        schemas.resource_filter(user_model, nested_object(12))
