"""Language-code field contracts: one language, and a person's list as it behaved before the single field."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models

from angee.parties.fields import (
    LanguageCodeField,
    LanguageCodesField,
    normalize_language_code,
    normalize_language_codes,
)


class LanguageRecord(models.Model):
    language = LanguageCodeField(blank=True, default="")
    languages = LanguageCodesField(default=list, blank=True, null=True)

    class Meta:
        app_label = "parties_language_tests"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("en", "en"),
        ("EN", "en"),
        (" eng ", "en"),
        ("spa", "es"),
        ("zh", "zh"),
        ("yue", "yue"),  # a language without a two-letter code keeps its ISO 639-3 code
        ("", ""),
        (None, ""),
    ],
)
def test_one_language_is_stored_as_its_iso_639_code(value: Any, expected: str) -> None:
    row = LanguageRecord(language=value)
    field = LanguageRecord._meta.get_field("language")

    assert normalize_language_code(value) == expected
    assert field.clean(value, row) == expected
    assert field.pre_save(row, add=True) == expected
    assert row.language == expected
    assert isinstance(row.language, str)


@pytest.mark.parametrize("value", ["en-GB", "en-us", "zh-hans", "klingon", "xx", " ", 1, ["en"]])
def test_one_language_refuses_a_subtag_a_name_and_anything_but_a_code(value: Any) -> None:
    field = LanguageRecord._meta.get_field("language")

    with pytest.raises(ValidationError, match="not an ISO 639 language code"):
        field.get_prep_value(value)


def test_the_single_language_field_has_a_stable_migration_shape() -> None:
    _, path, args, kwargs = LanguageRecord._meta.get_field("language").deconstruct()

    assert path == "angee.parties.fields.LanguageCodeField"
    assert args == []
    assert "max_length" not in kwargs


def test_a_persons_languages_normalise_refuse_and_store_as_before() -> None:
    """``LanguageCodesField`` and ``normalize_language_codes`` keep their contract beside the single field."""

    field = LanguageRecord._meta.get_field("languages")
    row = LanguageRecord(languages=["ES", "eng", "spa", " en ", "yue"])

    assert field.pre_save(row, add=True) == ["es", "en", "yue"]
    assert row.languages == ["es", "en", "yue"]
    assert normalize_language_codes(None) == normalize_language_codes("") == []
    assert field.to_python(None) is None
    for refused, code in (
        (["en", ""], "invalid_language"),  # an empty entry is no language
        (["en-GB"], "invalid_language"),
        ([1], "invalid_language"),
        ("en", "invalid_languages"),
        ({"en": 1}, "invalid_languages"),
    ):
        with pytest.raises(ValidationError) as raised:
            normalize_language_codes(refused)
        assert raised.value.code == code
    _, path, _, kwargs = field.deconstruct()
    assert path == "angee.parties.fields.LanguageCodesField"
    assert "max_length" not in kwargs
