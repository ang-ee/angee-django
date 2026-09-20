"""Country-code field contracts."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.test import override_settings
from django_countries import Countries

import angee.parties.fields as country_fields
from angee.parties.fields import CountryCodeField, normalize_country_code


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
        ("United States", "US"),
        ("Cyprus", "CY"),
        ("US", "US"),
        ("USA", "US"),
        ("CYP", "CY"),
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


@pytest.mark.parametrize("value", ["Atlantis", "The Netherlands"])
def test_country_code_field_rejects_unknown_or_colloquial_names(value: str) -> None:
    field = CountryRecord._meta.get_field("country")

    with pytest.raises(ValidationError, match="not a recognized country"):
        field.get_prep_value(value)


@override_settings(COUNTRIES_OVERRIDE={"XK": "Kosovo"})
def test_country_code_field_preserves_configured_django_country_overrides(monkeypatch) -> None:
    monkeypatch.setattr(country_fields, "countries", Countries())

    assert normalize_country_code("XK") == "XK"
    assert normalize_country_code("Kosovo") == "XK"


def test_country_code_field_has_stable_migration_shape() -> None:
    field = CountryRecord._meta.get_field("country")

    _, path, args, kwargs = field.deconstruct()
    assert path == "angee.parties.fields.CountryCodeField"
    assert args == []
    assert "choices" not in kwargs
    assert "max_length" not in kwargs
