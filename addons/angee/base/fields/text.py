"""Text, model-label and colour field value contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from angee.base.validators import HEX_COLOR_VALIDATOR
from angee.serialization import strip_null_bytes


class DiagnosticTextField(models.TextField):
    """Store bounded diagnostic text without database-invalid NUL characters."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        max_length = kwargs.setdefault("max_length", 8192)
        if not isinstance(max_length, int) or max_length < 1:
            raise ValueError("DiagnosticTextField requires a positive max_length.")
        super().__init__(*args, **kwargs)

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        return strip_null_bytes(value)[: self.max_length] if isinstance(value, str) else value

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        value = self.get_prep_value(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value


class ModelLabelField(models.CharField):
    """Store Django's canonical ``app_label.ModelName`` for a configured model."""

    @staticmethod
    def get_model(value: str) -> type[models.Model]:
        """Return the configured model a label names; ``ValueError`` when none does."""

        try:
            return apps.get_model(value)
        except (LookupError, ValueError) as error:
            raise ValueError(f"Unknown model label {value!r}.") from error

    @classmethod
    def normalize(cls, value: str) -> str:
        if not value:
            return ""
        try:
            return cls.get_model(value)._meta.label
        except ValueError as error:
            raise ValidationError(str(error)) from error

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        if isinstance(value, str):
            try:
                return self.normalize(value)
            except ValidationError:
                # Django also passes substring operands through field prep.
                return value
        return value

    def get_db_prep_save(self, value: Any, connection: Any) -> Any:
        if isinstance(value, str):
            value = self.normalize(value)
        return super().get_db_prep_save(value, connection)

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        value = self.normalize(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value


class ColorField(models.CharField):
    """A ``#rrggbb`` colour column, chosen with the shared colour widget.

    Declared natively, e.g. ``ColorField(blank=True, default="")``; ``max_length``
    defaults to the seven characters of a hex colour. Blank means "no colour".
    Resource metadata classifies the column to the ``color`` widget, so owners
    place the field and never name the widget.
    """

    angee_widget = "color"
    default_validators = [HEX_COLOR_VALIDATOR]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_length", 7)
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        if kwargs.get("max_length") == 7:
            del kwargs["max_length"]
        return name, path, args, kwargs
