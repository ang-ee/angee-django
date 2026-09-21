"""Platform GraphQL contracts over canonical native read projections."""

from __future__ import annotations

import importlib
from typing import Any, NoReturn

import pytest
import strawberry
from django.apps import apps
from rebac import system_context
from strawberry.schema.config import StrawberryConfig

from angee.platform import composed
from tests.conftest import addon_schema, execute_schema
from tests.conftest import create_platform_admin as _platform_admin
from tests.conftest import result_data as _data
from tests.test_platform_install import platform_tables as platform_tables

platform_schema = importlib.import_module("angee.platform.schema")


def _unexpected(*args: Any, **kwargs: Any) -> NoReturn:
    """Fail when a selected field asks for an unrelated platform view."""

    del args, kwargs
    raise AssertionError("unexpected platform view")


def _schema() -> Any:
    """Build the platform addon's console schema bucket."""

    return addon_schema(platform_schema.schemas, "console")


@pytest.mark.parametrize("config_schema", [None, {"type": "object", "properties": {"mode": {"type": "string"}}}])
def test_implementation_detail_projects_owner_fields_and_json(monkeypatch: Any, config_schema: Any) -> None:
    """Native projection retains inherited fields, JSON values and nullable source metadata."""

    detail = composed.PlatformImplementationDetail(
        id="tests.Model:backend:sample",
        model="tests.Model",
        field="backend",
        key="sample",
        label="Sample",
        category="Tests",
        icon="test",
        registry_setting="TEST_IMPLEMENTATIONS",
        class_path="tests.Sample",
        base_class_path="tests.Base",
        addon_id="tests",
        addon_label="Tests",
        defaults={"nested": {"enabled": True}, "items": [1, None]},
        config_schema=config_schema,
        description="Sample implementation",
        source=None,
        source_file=None,
        source_start_line=None,
        source_unavailable_reason="No source file",
    )
    monkeypatch.setattr(composed, "implementation_detail", lambda _id: detail)

    @strawberry.type
    class Query:
        @strawberry.field
        def implementation(self) -> platform_schema.PlatformImplementationDetail | None:
            return platform_schema.PlatformQuery().platform_implementation(detail.id)

    schema = strawberry.Schema(query=Query, config=StrawberryConfig(auto_camel_case=False))
    result = schema.execute_sync(
        """{
        implementation {
            id model field key label category icon registry_setting class_path base_class_path addon_id addon_label
            defaults config_schema description source source_file source_start_line source_unavailable_reason
        }
    }"""
    )

    assert result.errors is None
    assert result.data == {"implementation": detail.model_dump()}


def test_denied_explorer_is_null_while_computed_collections_are_empty(monkeypatch: Any) -> None:
    """Platform's authored and Hasura read surfaces keep their distinct denial shapes."""

    monkeypatch.setattr(platform_schema, "platform_can_read", lambda: False)

    data = _data(
        execute_schema(
            _schema(),
            """
            query {
              platform_explorer { models { label } }
              platform_models(limit: 10) { id }
              platform_fields(limit: 10) { id }
              platform_implementations(limit: 10) { id }
            }
            """,
        )
    )

    assert data == {
        "platform_explorer": None,
        "platform_models": [],
        "platform_fields": [],
        "platform_implementations": [],
    }


def test_explorer_reads_persisted_addons_and_shared_computed_rows(platform_tables: None, monkeypatch: Any) -> None:
    """Addon facts come from the catalogue while model and field bindings share rows."""

    del platform_tables
    admin = _platform_admin("explorer-admin")
    config = apps.get_app_config("linesdemo")
    line = apps.get_model("linesdemo", "SaleLine")
    tag = apps.get_model("linesdemo", "Tag")
    line_row = composed.PlatformModelRow.from_model(config, line)
    tag_row = composed.PlatformModelRow.from_model(config, tag)
    tag_field = next(field for field in line_row.fields() if field.name == "tags")
    addon = apps.get_model("platform", "Addon")
    with system_context(reason="test.platform.explorer.seed"):
        addon.objects.all().delete()
        addon.objects.create(
            name=config.name,
            label=config.label,
            namespace="tests",
            kind=addon.Kind.CONSUMER,
            state=addon.State.ENABLED,
            model_count=2,
            field_count=line_row.field_count + tag_row.field_count,
            resource_count=7,
            depends_on=["example.dependency"],
            model_labels=[line_row.label, tag_row.label],
        )
        addon.objects.create(name="example.remote", source=addon.Source.REMOTE)
    with monkeypatch.context() as patch:
        patch.setattr(platform_schema, "platform_can_read", lambda: True)
        patch.setattr(composed, "model_rows", lambda: [line_row, tag_row])
        patch.setattr(composed, "field_rows", lambda: [tag_field])
        patch.setattr(composed, "resource_counts", _unexpected)

        data = _data(
            execute_schema(
                _schema(),
                """
                query {
                  platform_explorer {
                    addons { id label model_labels resource_count depends_on }
                    models { label fields { name relation_target } }
                    edges { id source target kind field_name }
                  }
                  platform_models_by_pk(id: "linesdemo.saleline") {
                    id
                    label
                    field_count
                    relation_count
                  }
                  platform_fields_by_pk(id: "linesdemo.saleline.tags") {
                    id
                    name
                    model
                    addon
                    relation_target
                  }
                }
                """,
                user=admin,
            )
        )

    explorer = data["platform_explorer"]
    assert explorer["addons"] == [
        {
            "id": "example.remote",
            "label": "",
            "model_labels": [],
            "resource_count": 0,
            "depends_on": [],
        },
        {
            "id": config.name,
            "label": config.label,
            "model_labels": [line_row.label, tag_row.label],
            "resource_count": 7,
            "depends_on": ["example.dependency"],
        },
    ]
    nested_line = next(model for model in explorer["models"] if model["label"] == line_row.label)
    assert {field["name"] for field in nested_line["fields"]} == {
        field.name for field in line_row.fields()
    }
    assert data["platform_models_by_pk"] == {
        "id": line_row.id,
        "label": line_row.label,
        "field_count": line_row.field_count,
        "relation_count": line_row.relation_count,
    }
    assert data["platform_fields_by_pk"] == {
        "id": tag_field.id,
        "name": "tags",
        "model": line_row.label,
        "addon": line._meta.app_label,
        "relation_target": tag_row.label,
    }
    assert {
        (edge["id"], edge["source"], edge["target"], edge["kind"], edge["field_name"])
        for edge in explorer["edges"]
    } >= {
        (tag_field.id, line_row.label, tag_row.label, "many_to_many", "tags"),
    }


def test_addon_names_support_text_search_and_sort_with_unknown_labels(platform_tables: None) -> None:
    """Canonical names remain searchable and ordered when Django identity is unknown."""

    del platform_tables
    admin = _platform_admin("catalogue-search-admin")
    addon = apps.get_model("platform", "Addon")
    with system_context(reason="test.platform.catalogue.search"):
        for name, label in (("example.zebra", ""), ("example.alpha", "Zulu"), ("other.hidden", "")):
            addon.objects.create(name=name, label=label, source=addon.Source.REMOTE)

    data = _data(
        execute_schema(
            _schema(),
            """
            query {
              platform_addons(where: {name: {_ilike: "example.%"}}, order_by: [{name: asc}]) {
                id name label
              }
            }
            """,
            user=admin,
        )
    )

    assert data["platform_addons"] == [
        {"id": "example.alpha", "name": "example.alpha", "label": "Zulu"},
        {"id": "example.zebra", "name": "example.zebra", "label": ""},
    ]


def test_model_only_explorer_selection_skips_catalogue_and_edges(monkeypatch: Any) -> None:
    """A model-only explorer request reads neither the catalogue nor graph edges."""

    config = apps.get_app_config("linesdemo")
    model = apps.get_model("linesdemo", "Tag")
    row = composed.PlatformModelRow.from_model(config, model)
    monkeypatch.setattr(platform_schema, "platform_can_read", lambda: True)
    monkeypatch.setattr(composed, "model_rows", lambda: [row])
    monkeypatch.setattr(platform_schema._Addon.objects, "all", _unexpected)
    monkeypatch.setattr(composed, "resource_counts", _unexpected)
    monkeypatch.setattr(platform_schema, "_edge_rows", _unexpected)

    data = _data(
        execute_schema(
            _schema(),
            "query { platform_explorer { models { label } } }",
        )
    )

    assert data == {"platform_explorer": {"models": [{"label": row.label}]}}
