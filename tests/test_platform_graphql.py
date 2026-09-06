"""Platform GraphQL contracts over canonical native read projections."""

from __future__ import annotations

import importlib
from typing import Any, NoReturn

from django.apps import apps

from angee.platform import composed
from tests.conftest import addon_schema, execute_schema
from tests.conftest import result_data as _data

platform_schema = importlib.import_module("angee.platform.schema")


def _unexpected(*args: Any, **kwargs: Any) -> NoReturn:
    """Fail when a selected field asks for an unrelated platform view."""

    del args, kwargs
    raise AssertionError("unexpected platform view")


def _schema() -> Any:
    """Build the platform addon's console schema bucket."""

    return addon_schema(platform_schema.schemas, "console")


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
            }
            """,
        )
    )

    assert data == {
        "platform_explorer": None,
        "platform_models": [],
        "platform_fields": [],
    }


def test_legacy_explorer_and_computed_resources_bind_the_same_rows(monkeypatch: Any) -> None:
    """Nested and flat bindings preserve IDs, fields, and graph edges without recopying."""

    config = apps.get_app_config("linesdemo")
    line = apps.get_model("linesdemo", "SaleLine")
    tag = apps.get_model("linesdemo", "Tag")
    line_row = composed.PlatformModelRow.from_model(config, line)
    tag_row = composed.PlatformModelRow.from_model(config, tag)
    tag_field = next(field for field in line_row.fields() if field.name == "tags")
    rollup = composed.AddonRollup(
        name=config.name,
        label=config.label,
        namespace="tests",
        kind="consumer",
        forced=False,
        model_count=2,
        field_count=line_row.field_count + tag_row.field_count,
        resource_count=0,
        depends_on=[],
        model_labels=[line_row.label, tag_row.label],
        description="",
        keywords=[],
        category="",
    )
    monkeypatch.setattr(platform_schema, "platform_can_read", lambda: True)
    monkeypatch.setattr(composed, "model_rows", lambda: [line_row, tag_row])
    monkeypatch.setattr(composed, "field_rows", lambda: [tag_field])
    monkeypatch.setattr(composed, "addon_rollups", lambda: [rollup])

    data = _data(
        execute_schema(
            _schema(),
            """
            query {
              platform_explorer {
                addons { id label model_labels }
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
        )
    )

    explorer = data["platform_explorer"]
    assert explorer["addons"] == [
        {
            "id": config.name,
            "label": config.label,
            "model_labels": [line_row.label, tag_row.label],
        }
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


def test_model_only_explorer_selection_skips_addon_rollups_and_edges(monkeypatch: Any) -> None:
    """A model-only explorer request reads neither resource counts nor graph edges."""

    config = apps.get_app_config("linesdemo")
    model = apps.get_model("linesdemo", "Tag")
    row = composed.PlatformModelRow.from_model(config, model)
    monkeypatch.setattr(platform_schema, "platform_can_read", lambda: True)
    monkeypatch.setattr(composed, "model_rows", lambda: [row])
    monkeypatch.setattr(composed, "addon_rollups", _unexpected)
    monkeypatch.setattr(platform_schema, "_edge_rows", _unexpected)

    data = _data(
        execute_schema(
            _schema(),
            "query { platform_explorer { models { label } } }",
        )
    )

    assert data == {"platform_explorer": {"models": [{"label": row.label}]}}
