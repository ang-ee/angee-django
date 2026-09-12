"""Canonical country-code fields for party-owned postal identities."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.encoding import force_str
from django_countries import Countries, countries

from angee.graphql.field_types import register_field_type


def country_choices() -> Countries:
    """Return translated ISO 3166-1 choices lazily for Django metadata."""

    return countries


def normalize_country_code(value: Any) -> str:
    """Resolve an ISO code or exact upstream-recognized name to alpha-2.

    Deliberately reject fuzzy or colloquial names so ingestion cannot silently
    attach an address to the wrong jurisdiction.
    """

    candidate = force_str(value or "").strip()
    if not candidate:
        return ""
    code = countries.alpha2(candidate) or countries.by_name(candidate)
    if not isinstance(code, str) or not code:
        raise ValidationError(
            "%(value)s is not a recognized country code or name.",
            code="invalid_country",
            params={"value": candidate},
        )
    return code


class CountryCodeField(models.CharField):
    """A plain-string field that stores canonical ISO 3166-1 alpha-2 codes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["max_length"] = 2
        kwargs["choices"] = country_choices
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("choices", None)
        kwargs.pop("max_length", None)
        return name, path, args, kwargs

    def to_python(self, value: Any) -> str:
        return normalize_country_code(super().to_python(value))

    def get_prep_value(self, value: Any) -> str:
        return super().get_prep_value(self.to_python(value))

    def pre_save(self, model_instance: models.Model, add: bool) -> str:
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


register_field_type(CountryCodeField, str)
