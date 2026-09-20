"""Focused contracts for native platform model, field, and implementation projections."""

from __future__ import annotations

from typing import Any, NoReturn

import pytest
from django.apps import apps

from angee.platform import composed


def _unexpected(*args: Any, **kwargs: Any) -> NoReturn:
    """Fail when a projection asks for an unrelated representation."""

    del args, kwargs
    raise AssertionError("unexpected projection")


def test_model_row_retains_exact_native_field_facts_without_eager_field_rows(
    monkeypatch: Any,
) -> None:
    """A model list computes counts from Django fields and defers nested row binding."""

    user = apps.get_model("iam", "User")
    config = apps.get_app_config(user._meta.app_label)
    with monkeypatch.context() as patch:
        patch.setattr(composed.PlatformFieldRow, "from_field", classmethod(_unexpected))
        row = composed.PlatformModelRow.from_model(config, user)

    assert row.id == row.label == user._meta.label_lower
    assert row.field_count == len(composed.own_fields(user))
    assert row.relation_count == sum(field.is_relation for field in composed.own_fields(user))
    assert {field.name for field in row.fields()} == {field.name for field in composed.own_fields(user)}


def test_field_rows_project_directly_without_model_rows(monkeypatch: Any) -> None:
    """The flat field collection never materializes the model-row collection."""

    monkeypatch.setattr(composed.PlatformModelRow, "from_model", classmethod(_unexpected))

    rows = composed.field_rows()

    assert rows
    assert all(row.id == f"{row.model}.{row.name}" for row in rows)


def test_native_relation_cardinality_and_target_survive_projection() -> None:
    """Many-to-many relation fields preserve exact graph semantics and IDs."""

    line = apps.get_model("linesdemo", "SaleLine")
    tags = line._meta.get_field("tags")

    row = composed.PlatformFieldRow.from_field(line, tags)

    assert row.id == f"{line._meta.label_lower}.tags"
    assert row.model == line._meta.label_lower
    assert row.addon == line._meta.app_label
    assert row.relation_target == tags.related_model._meta.label_lower
    assert row.relation_kind() == "many_to_many"


def test_nested_inherited_fields_keep_the_selected_child_identity() -> None:
    """Inherited native fields remain nested under the selected MTI model row."""

    parent = apps.get_model("mtidemo", "MtiParent")
    child = apps.get_model("mtidemo", "MtiChild")
    config = apps.get_app_config("mtidemo")
    inherited = next(field for field in composed.own_fields(child) if field.model is parent)

    row = composed.PlatformModelRow.from_model(config, child)
    projected = next(field for field in row.fields() if field.name == inherited.name)

    assert projected.id == f"{child._meta.label_lower}.{inherited.name}"
    assert projected.model == child._meta.label_lower
    assert projected.addon == child._meta.app_label


def test_implementation_rows_use_registered_field_owners_without_reading_source(
    monkeypatch: Any,
) -> None:
    """List rows resolve canonical registry metadata but never inspect Python source."""

    monkeypatch.setattr(composed.inspect, "getsourcelines", _unexpected)
    monkeypatch.setattr(composed.inspect, "getsourcefile", _unexpected)

    rows = composed.implementation_rows()

    assert rows
    assert rows == sorted(rows, key=lambda row: row.id)
    assert all(row.id == f"{row.model}.{row.field}:{row.key}" for row in rows)
    assert all(row.registry_setting and row.class_path and row.base_class_path for row in rows)


@pytest.mark.parametrize("installed", [True, False])
def test_implementation_row_factory_retains_field_choice_and_optional_addon(
    monkeypatch: Any,
    installed: bool,
) -> None:
    """The factory preserves registered identity and metadata for later detail reads."""

    model = apps.get_model("storage", "Backend")
    field = model._meta.get_field("backend_class")
    choice = next(choice for choice in field.impl_choices() if choice.key == "local")
    configs = [apps.get_app_config("storage")] if installed else []
    with monkeypatch.context() as patch:
        patch.setattr(composed.inspect, "getsourcelines", _unexpected)
        patch.setattr(composed.inspect, "getsourcefile", _unexpected)
        row = composed.PlatformImplementationRow.from_field(model, field, "local", choice, configs)

    assert row.id == "storage.Backend.backend_class:local"
    assert row.model == "storage.Backend"
    assert row.field == "backend_class"
    assert row.key == "local"
    assert (row.label, row.category, row.icon) == (choice.label, choice.category, choice.icon)
    assert row.registry_setting == "ANGEE_STORAGE_BACKEND_CLASSES"
    assert row.class_path == "angee.storage.backends.LocalBackend"
    assert row.base_class_path == "angee.storage.backends.StorageBackend"
    assert row.addon_id == ("angee.storage" if installed else "")
    assert row.addon_label == ("storage" if installed else "")
    detail = row.detail()
    assert detail.defaults == choice.defaults
    assert detail.config_schema == choice.config_schema
    assert detail.source is not None
    assert "class LocalBackend(" in detail.source


def test_implementation_detail_only_resolves_registered_ids() -> None:
    """Detail source lookup accepts canonical registered identities and rejects arbitrary paths."""

    row = composed.implementation_rows()[0]

    assert composed.implementation_detail("/tmp/caller-controlled.py") is None
    detail = composed.implementation_detail(row.id)

    assert detail is not None
    assert detail.id == row.id
    assert detail.defaults is not None
    assert (detail.source is not None) != (detail.source_unavailable_reason is not None)
    if detail.source is not None:
        assert detail.source_file
        assert detail.source_start_line is not None
