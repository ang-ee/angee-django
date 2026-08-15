"""Shared pytest infrastructure for framework-core tests."""

from __future__ import annotations

import itertools
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

from django.apps import AppConfig
from django.contrib.auth.models import AnonymousUser
from django.db import connection, models
from django.test import RequestFactory
from rebac import actor_context


def _create_missing_tables(
    test_models: tuple[type[models.Model], ...],
) -> list[type[models.Model]]:
    """Create concrete core-test model tables that Django did not synchronize."""

    existing_tables = set(connection.introspection.table_names())
    missing = []
    for model in test_models:
        if model._meta.db_table in existing_tables:
            continue
        missing.append(model)
        existing_tables.add(model._meta.db_table)
    if not missing:
        return []
    with connection.schema_editor() as schema_editor:
        for model in missing:
            schema_editor.create_model(model)
    return missing


def _clear_model_tables(test_models: tuple[type[models.Model], ...]) -> None:
    """Clear schema-editor-created core-test model tables between tests."""

    existing_tables = set(connection.introspection.table_names())
    table_names = []
    for model in test_models:
        table_name = model._meta.db_table
        if table_name not in existing_tables:
            continue
        table_names.append(table_name)
        for field in model._meta.many_to_many:
            through_table_name = field.remote_field.through._meta.db_table
            if through_table_name in existing_tables:
                table_names.append(through_table_name)
    if not table_names:
        return
    with connection.constraint_checks_disabled(), connection.cursor() as cursor:
        for table_name in reversed(tuple(dict.fromkeys(table_names))):
            cursor.execute(f"DELETE FROM {connection.ops.quote_name(table_name)}")


_TEST_ADDON_SEQ = itertools.count()


def make_addon(
    *,
    schemas: dict[str, Any] | None = None,
    depends_on: tuple[str, ...] = (),
    name: str | None = None,
) -> AppConfig:
    """Return a fake AppConfig backed by a temporary addon manifest."""

    name = name or f"tests._addon_{next(_TEST_ADDON_SEQ)}"
    tmp = Path(tempfile.mkdtemp())
    module = ModuleType(name)
    module.__file__ = str(tmp / "apps.py")
    setattr(module, "__path__", [str(tmp)])
    sys.modules[name] = module

    body = ["[addon]", f'name = "{name}"']
    if depends_on:
        body.append("depends_on = [" + ", ".join(f'"{dep}"' for dep in depends_on) + "]")
    if schemas is not None:
        schema_module = ModuleType(f"{name}.schema")
        setattr(schema_module, "schemas", schemas)
        sys.modules[f"{name}.schema"] = schema_module
        body.append('schemas = "schema.schemas"')
    (tmp / "addon.toml").write_text("\n".join(body) + "\n")

    return AppConfig(name, module)


def SchemaAddon(schemas: dict[str, dict[str, tuple[object, ...]]]) -> AppConfig:  # noqa: N802
    """Build an addon stand-in whose manifest exposes GraphQL schemas."""

    return make_addon(schemas=schemas)


def graphql_request(user: Any) -> Any:
    """Return a bare POST request carrying ``user``."""

    request = RequestFactory().post("/graphql/public/")
    request.user = user
    return request


def execute_schema(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a request-shaped context."""

    request = request or graphql_request(user or AnonymousUser())
    actor = getattr(request, "user", AnonymousUser())
    with actor_context(actor):
        return schema.execute_sync(
            query,
            variable_values=variables or {},
            context_value=SimpleNamespace(request=request),
        )


def result_data(result: Any) -> dict[str, Any]:
    """Return result data after asserting the operation succeeded."""

    assert result.errors is None, result.errors
    assert result.data is not None
    return cast(dict[str, Any], result.data)
