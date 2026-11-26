"""A ``JSON`` input keeps Angee's JSON bounds, refused as bad input before execution."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

import pytest
import strawberry
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connections
from django.test.utils import CaptureQueriesContext
from graphql import build_schema, print_type
from rebac import system_context
from strawberry.scalars import JSON

from angee.base.serialization import MAX_JSON_DEPTH, MAX_JSON_MEMBERS
from angee.graphql.schema import GraphQLSchemas
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
    """Run a document anonymously on the web forms console and count the SQL every database captured."""

    with ExitStack() as stack:
        captures = [stack.enter_context(CaptureQueriesContext(connection)) for connection in connections.all()]
        result = execute_schema(addon_schema(webforms_schema.schemas, "console"), document, variables)
    return result, sum(len(capture) for capture in captures)


@strawberry.type
class EchoQuery:
    @strawberry.field
    def echo(self, value: JSON) -> JSON:
        return value


def echo_schema() -> Any:
    return addon_schema({"public": {"query": [EchoQuery]}}, "public")


@pytest.fixture
def form() -> Any:
    with system_context(reason="json input tests setup"):
        return Form.objects.create(name="Feedback")


def stored_versions(form: Any) -> bool:
    with system_context(reason="json input tests read"):
        return FormVersion._base_manager.filter(form=form).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("nested", [nested_list, nested_object])
def test_a_deep_json_input_is_refused_before_any_permission_check_or_query(form: Any, nested: Any) -> None:
    """2,000 levels fail as bad input with no SQL; at the bound, the actor-scoped read of the form refuses."""

    refused, refused_statements = statements(SAVE_VERSION, {"form": form.sqid, "definition": nested(2000)})
    admitted, admitted_statements = statements(SAVE_VERSION, {"form": form.sqid, "definition": nested(MAX_JSON_DEPTH)})

    assert refused.data is None
    assert refused.errors is not None
    assert [error.extensions for error in refused.errors] == [{"code": "BAD_USER_INPUT"}]
    assert f"nests deeper than {MAX_JSON_DEPTH} levels" in refused.errors[0].message
    assert refused_statements == 0
    assert admitted.errors is not None
    assert admitted.errors[0].extensions["code"] == "VALIDATION"
    assert admitted.errors[0].extensions["validationErrors"].keys() == {"form"}
    assert admitted_statements > 0
    assert not stored_versions(form)


@pytest.mark.django_db
def test_a_variable_repeated_inside_a_literal_counts_each_time_and_is_refused_before_any_query(form: Any) -> None:
    """References to one variable expand the value a later walk meets, so each counts against the member bound."""

    shared = [{"value": f"c{index}", "text": "Choice"} for index in range(20_000)]
    document = SAVE_VERSION.replace(
        "definition: $definition", "definition: [%s]" % ", ".join(["$definition"] * 4_000),
    )

    refused, refused_statements = statements(document, {"form": form.sqid, "definition": shared})

    assert refused.data is None
    assert refused.errors is not None
    assert [error.extensions for error in refused.errors] == [{"code": "BAD_USER_INPUT"}]
    assert f"holds more than {MAX_JSON_MEMBERS} members" in refused.errors[0].message
    assert refused_statements == 0
    assert not stored_versions(form)


@pytest.mark.parametrize("nested", [nested_list, nested_object])
def test_a_json_variable_at_the_depth_bound_reaches_the_resolver_unchanged(nested: Any) -> None:
    document = "query Echo($value: JSON!) { echo(value: $value) }"

    kept = echo_schema().execute_sync(document, variable_values={"value": nested(MAX_JSON_DEPTH)})
    refused = echo_schema().execute_sync(document, variable_values={"value": nested(MAX_JSON_DEPTH + 1)})

    assert kept.errors is None
    assert kept.data == {"echo": nested(MAX_JSON_DEPTH)}
    assert refused.data is None
    assert refused.errors is not None
    assert refused.errors[0].extensions == {"code": "BAD_USER_INPUT"}


def test_a_json_variable_at_the_member_bound_is_kept_and_one_past_it_refused() -> None:
    document = "query Echo($value: JSON!) { echo(value: $value) }"

    kept = echo_schema().execute_sync(document, variable_values={"value": [0] * MAX_JSON_MEMBERS})
    refused = echo_schema().execute_sync(document, variable_values={"value": [0] * (MAX_JSON_MEMBERS + 1)})

    assert kept.errors is None
    assert refused.errors is not None
    assert refused.errors[0].formatted == {
        "message": f"Variable '$value' has invalid value: JSON input holds more than {MAX_JSON_MEMBERS} members.",
        "locations": [{"line": 1, "column": 12}],
        "extensions": {"code": "BAD_USER_INPUT"},
    }


def test_a_json_literal_meets_the_same_bound_and_its_refusal_names_where_it_is() -> None:
    kept = echo_schema().execute_sync("{ echo(value: %s) }" % literal(MAX_JSON_DEPTH))
    refused = echo_schema().execute_sync("{ echo(value: %s) }" % literal(MAX_JSON_DEPTH + 1))

    assert kept.errors is None
    assert kept.data == {"echo": nested_list(MAX_JSON_DEPTH)}
    assert refused.data is None
    assert refused.errors is not None
    assert refused.errors[0].formatted == {
        "message": f"JSON input nests deeper than {MAX_JSON_DEPTH} levels.",
        "locations": [{"line": 1, "column": 15}],
        "extensions": {"code": "BAD_USER_INPUT"},
    }


def test_the_json_scalar_prints_as_strawberrys_own() -> None:
    """The bound replaces how the scalar reads input; its name, description and specification stay Strawberry's."""

    def printed_json(sdl: str) -> str:
        return print_type(build_schema(sdl).get_type("JSON"))

    stock = printed_json(strawberry.Schema(query=EchoQuery).as_str())

    assert printed_json(echo_schema().as_str()) == stock
    assert "scalar JSON @specifiedBy" in stock


def test_resource_conditions_keep_their_own_nesting_bound() -> None:
    """A stored condition is measured by the same walk against its own depth of 12."""

    schemas = GraphQLSchemas([])
    user_model = get_user_model()

    with pytest.raises(ValidationError, match="exceed nesting depth 12"):
        schemas.resource_filter(user_model, nested_object(13))
    with pytest.raises(ValidationError, match="has no filterable resource"):
        schemas.resource_filter(user_model, nested_object(12))
