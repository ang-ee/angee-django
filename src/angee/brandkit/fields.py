"""The brand kit's CSS font stack column."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from angee.graphql.field_types import register_field_type

GENERIC_FONT_FAMILIES = frozenset({
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif",
    "ui-monospace", "ui-rounded", "math", "emoji", "fangsong",
})
"""The CSS Fonts 4 generic families; a font stack ends in one, so text always finds a face."""

CSS_WIDE_KEYWORDS = frozenset({"inherit", "initial", "unset", "default", "revert", "revert-layer"})
"""Keywords that void a whole ``font-family`` declaration when they stand unquoted in one of its families."""

# Possessive quantifiers: a run of spaces or hyphens is matched once and never
# re-tried, so a family is judged in time linear in its length.
_FAMILY_NAME = r"[ -]*+[^\W_](?:[^\W_]|[ -])*+"
_FONT_FAMILY = re.compile(rf" *+(?:(?P<bare>{_FAMILY_NAME})|'{_FAMILY_NAME}') *+")
_NAMES = "Name each font family with letters, digits, spaces and hyphens, separated by commas."
_QUOTE = (
    "Quote a family name with a word that starts with a digit, a lone hyphen or a hyphen and a digit, "
    "or a CSS keyword, such as 'Font Awesome 5 Free'."
)
_GENERIC = "End the font stack with a generic family, such as sans-serif."


def normalize_font_stack(value: str) -> str:
    """Return ``value`` with its double quotes as single quotes, so it never ends a double-quoted HTML attribute."""

    return value.replace('"', "'")


def _identifier(word: str) -> bool:
    """Whether a word of an unquoted family is a CSS identifier that leaves the declaration valid."""

    starts_badly = word[0].isdigit() or word == "-" or (word[0] == "-" and word[1].isdigit())
    return not starts_badly and word.lower() not in CSS_WIDE_KEYWORDS


def validate_font_stack(value: str) -> None:
    """Admit a normalised CSS font stack that is safe to write into a style.

    Each comma-separated family is letters, digits, spaces and hyphens, bare or
    wholly inside one pair of single quotes (:func:`normalize_font_stack` turns
    double quotes into single ones first). An unquoted family is a sequence of CSS
    identifiers, and one invalid word voids the whole declaration, so no word of a
    bare family may start with a digit, be a lone hyphen or start with a hyphen and
    a digit, or be a :data:`CSS_WIDE_KEYWORDS` keyword. The last family is an
    unquoted :data:`GENERIC_FONT_FAMILIES` family. Nothing else can reach the CSS,
    and the check takes time linear in the value's length.
    """

    families: Sequence[str] = value.split(",")
    bare_names: list[str | None] = []
    for family in families:
        match = _FONT_FAMILY.fullmatch(family)
        if match is None:
            raise ValidationError(_NAMES, code="font_stack")
        bare = match["bare"].strip() if match["bare"] is not None else None
        if bare is not None and not all(_identifier(word) for word in bare.split()):
            raise ValidationError(_QUOTE, code="font_stack")
        bare_names.append(bare)
    if bare_names[-1] is None or bare_names[-1].lower() not in GENERIC_FONT_FAMILIES:
        raise ValidationError(_GENERIC, code="font_stack")


class FontStackField(models.CharField):
    """A CSS font stack: named families closed by a generic family, safe to write into a style.

    Declared natively, e.g. ``FontStackField()``; ``max_length`` defaults to 255.
    Double quotes are stored as single quotes (:func:`normalize_font_stack`).
    ``full_clean`` refuses a stack :func:`validate_font_stack` does not admit, and
    so does every write that reaches the database, through
    :meth:`get_db_prep_save`: ``save``, ``bulk_create``, ``bulk_update`` and
    ``QuerySet.update``. Lookups take their operand as given.
    """

    description = "CSS font stack"
    default_validators = [validate_font_stack]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 255)
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        if kwargs.get("max_length") == 255:
            del kwargs["max_length"]
        return name, path, args, kwargs

    def to_python(self, value: Any) -> Any:
        """Return the stack with its double quotes as single quotes."""

        value = super().to_python(value)
        return normalize_font_stack(value) if isinstance(value, str) else value

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        """Store the normalised stack on the instance it saves."""

        value = self.to_python(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value

    def get_db_prep_save(self, value: Any, connection: Any) -> Any:
        """Refuse a stack :func:`validate_font_stack` does not admit, whichever write reaches the database."""

        if isinstance(value, str):
            value = normalize_font_stack(value)
            try:
                validate_font_stack(value)
            except ValidationError as error:
                raise ValidationError({self.name: error.messages}) from None
        return super().get_db_prep_save(value, connection)


register_field_type(FontStackField, str)
