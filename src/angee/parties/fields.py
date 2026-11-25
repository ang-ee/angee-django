"""Canonical code fields of parties: a postal identity's country, a person's languages, one language."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pycountry
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

    The configured django-countries catalogue resolves first, so a project's
    ``COUNTRIES_OVERRIDE`` names win; the complete pycountry ISO catalogue is an
    exact-match fallback only when that catalogue has no match, keeping the
    field's contract full ISO completeness rather than a validation gate.
    Deliberately reject fuzzy or colloquial names so ingestion cannot silently
    attach an address to the wrong jurisdiction.
    """

    candidate = force_str(value or "").strip()
    if not candidate:
        return ""
    code = countries.alpha2(candidate) or countries.by_name(candidate)
    if not isinstance(code, str) or not code:
        try:
            code = pycountry.countries.lookup(candidate).alpha_2
        except LookupError:
            code = ""
    if not code:
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

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
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


def normalize_language_code(value: Any) -> str:
    """Return one language as stored: its ISO 639 code, or ``""`` for no language.

    The code is two-letter ISO 639-1 or, for a language without one, its
    three-letter ISO 639-3 code, given in any case; a three-letter code of a
    language that has a two-letter one is stored as the two-letter code
    (pycountry's catalogue, as for countries). A region or script subtag
    (``en-GB``) is no language and is refused, as is anything but a string.
    ``None`` and ``""`` are no language.
    """

    return "" if value in (None, "") else _iso_639_code(value)


def normalize_language_codes(value: Any) -> list[str]:
    """Return languages as stored: ISO 639 codes, in the order given, each once.

    Each entry is spelled as :func:`normalize_language_code` stores one language,
    and an empty entry is refused, as is anything but a list of strings.
    """

    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError("Languages are a list of language codes.", code="invalid_languages")
    return list(dict.fromkeys(_iso_639_code(item) for item in value))


def _iso_639_code(value: Any) -> str:
    """Return the stored ISO 639 code of one language, or raise ``ValidationError``."""

    candidate = value.strip().lower() if isinstance(value, str) else ""
    language = (
        pycountry.languages.get(alpha_2=candidate)
        if len(candidate) == 2
        else pycountry.languages.get(alpha_3=candidate) if len(candidate) == 3 else None
    )
    if language is None:
        raise ValidationError(
            "%(value)s is not an ISO 639 language code.", code="invalid_language", params={"value": value}
        )
    return getattr(language, "alpha_2", None) or language.alpha_3


class LanguageCodeField(models.CharField):
    """A plain-string field that stores one canonical ISO 639 code (:func:`normalize_language_code`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["max_length"] = 3
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("max_length", None)
        return name, path, args, kwargs

    def to_python(self, value: Any) -> str:
        return normalize_language_code(super().to_python(value))

    def get_prep_value(self, value: Any) -> str:
        return super().get_prep_value(self.to_python(value))

    def pre_save(self, model_instance: models.Model, add: bool) -> str:
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


register_field_type(LanguageCodeField, str)


class LanguageCodesField(models.JSONField):
    """A JSON list of ISO 639 language codes, most preferred first (:func:`normalize_language_codes`)."""

    def to_python(self, value: Any) -> list[str] | None:
        value = super().to_python(value)
        return None if value is None else normalize_language_codes(value)

    def pre_save(self, model_instance: models.Model, add: bool) -> list[str] | None:
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


register_field_type(LanguageCodesField, list[str])
