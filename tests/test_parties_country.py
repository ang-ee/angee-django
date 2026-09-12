"""Country-code field and retained-address migration contracts."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.parties.fields import CountryCodeField, normalize_country_code
from angee.parties.runtime_migrations.address_country_code import (
    applies,
    canonicalize_address_countries,
)


class CountryRecord(models.Model):
    country = CountryCodeField(blank=True, default="")

    class Meta:
        app_label = "parties_country_tests"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("de", "DE"),
        ("DEU", "DE"),
        ("Germany", "DE"),
        ("Czech Republic", "CZ"),
        ("Puerto Rico", "PR"),
        ("", ""),
        (None, ""),
    ],
)
def test_country_code_field_normalizes_supported_input_as_plain_strings(value: object, expected: str) -> None:
    row = CountryRecord(country=value)
    field = CountryRecord._meta.get_field("country")

    assert normalize_country_code(value) == expected
    assert field.clean(value, row) == expected
    assert field.pre_save(row, add=True) == expected
    assert row.country == expected
    assert isinstance(row.country, str)


@pytest.mark.parametrize("value", ["Atlantis", "United States", "The Netherlands"])
def test_country_code_field_rejects_unknown_or_colloquial_names(value: str) -> None:
    field = CountryRecord._meta.get_field("country")

    with pytest.raises(ValidationError, match="not a recognized country"):
        field.get_prep_value(value)


def test_country_code_field_has_stable_migration_shape() -> None:
    field = CountryRecord._meta.get_field("country")

    _, path, args, kwargs = field.deconstruct()
    assert path == "angee.parties.fields.CountryCodeField"
    assert args == []
    assert "choices" not in kwargs
    assert "max_length" not in kwargs


def _address_state(field: models.Field[object, object]) -> ProjectState:
    state = ProjectState()
    state.add_model(
        ModelState(
            "parties",
            "Address",
            [("id", models.AutoField(primary_key=True)), ("country", field)],
            options={"db_table": "test_historical_address_country"},
        )
    )
    return state


def test_address_country_migration_guard_recognizes_only_old_and_current_shapes() -> None:
    assert applies(ProjectState()) is False
    assert applies(_address_state(models.TextField(blank=True, default=""))) is True
    assert applies(_address_state(CountryCodeField(blank=True, default=""))) is False


@pytest.mark.django_db(transaction=True)
def test_address_country_migration_canonicalizes_rows_and_rejects_unknowns_before_writing() -> None:
    state = _address_state(models.TextField(blank=True, default=""))
    address_model = state.apps.get_model("parties", "Address")
    with connection.schema_editor() as editor:
        editor.create_model(address_model)
    try:
        rows = address_model.objects.bulk_create(
            [
                address_model(country="Germany"),
                address_model(country="CAN"),
                address_model(country=" pr "),
                address_model(country="Atlantis"),
            ]
        )
        with connection.schema_editor() as editor, pytest.raises(RuntimeError, match="Atlantis"):
            canonicalize_address_countries(state.apps, editor)
        assert address_model.objects.get(pk=rows[0].pk).country == "Germany"

        address_model.objects.filter(pk=rows[3].pk).delete()
        with connection.schema_editor() as editor:
            canonicalize_address_countries(state.apps, editor)
        assert list(address_model.objects.order_by("pk").values_list("country", flat=True)) == [
            "DE",
            "CA",
            "PR",
        ]
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(address_model)
