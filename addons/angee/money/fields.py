"""Money-owned model fields."""

from __future__ import annotations

import decimal
from typing import Any

from django.apps import apps
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from angee.graphql.field_types import register_field_type


class MoneyField(models.DecimalField):
    """A decimal amount paired with the currency its row is denominated in.

    ``docs/stack.md`` keeps money native: a ``DecimalField`` (default
    ``max_digits=18, decimal_places=6``), never a money library. The single fact a
    money column adds over a plain decimal is *which currency the amount is in*:
    ``currency_field`` names the path to the money addon's ``Currency`` foreign
    key that owns the row's currency, either a sibling FK on the same model
    (``"currency"``, the default) or a one-hop related path
    (``"order.currency"``) when the currency lives on a parent document.

    ``currency_field`` is a semantic declaration, not a database fact: Django's
    ``Field.deconstruct`` serializes only the tracked column kwargs (the field's
    class path plus ``max_digits`` / ``decimal_places``) and never a custom
    constructor attribute, so ``currency_field`` stays out of migration state on
    its own — no ``deconstruct`` override — while the currency path rides through
    ``deepcopy`` inheritance onto the live field. Changing ``currency_field``
    therefore never writes a migration. Rendering the amount with its currency
    (resolved through the metadata's currency path) is the ``"money"`` widget's
    job, registered by the currency addon's web package; the field only owns the
    backend vocabulary.
    """

    angee_widget = "money"
    angee_scalar_hint = "Decimal"

    def __init__(self, *args: Any, currency_field: str = "currency", **kwargs: Any) -> None:
        """Record the currency path and default the money decimal precision."""

        self.currency_field = currency_field
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 6)
        super().__init__(*args, **kwargs)

    @property
    def angee_currency_field(self) -> str:
        """Return the currency path this field declares for resource metadata."""

        return self.currency_field

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate that ``currency_field`` resolves to the money addon's ``Currency``."""

        errors = super().check(**kwargs)
        errors.extend(self._check_currency_field())
        return errors

    def _check_currency_field(self) -> list[checks.CheckMessage]:
        """Return check errors for the declared ``currency_field`` path."""

        segments = self.currency_field.split(".") if self.currency_field else []
        if not 1 <= len(segments) <= 2:
            return [
                checks.Error(
                    f"MoneyField currency_field={self.currency_field!r} must name a sibling "
                    "foreign key ('currency') or a one-hop related path ('order.currency').",
                    obj=self,
                    id="angee.E010",
                )
            ]
        model = self.model
        for hop in segments[:-1]:
            resolved = self._foreign_key_target(model, hop)
            if isinstance(resolved, checks.Error):
                return [resolved]
            if resolved is None:  # unresolved relation — Django's fields.E300 owns the report
                return []
            model = resolved
        target = self._foreign_key_target(model, segments[-1])
        if isinstance(target, checks.Error):
            return [target]
        if target is None:
            return []
        currency_model = apps.get_model("money", "Currency")
        if target is not currency_model:
            currency_label = currency_model._meta.label
            return [
                checks.Error(
                    f"MoneyField currency_field={self.currency_field!r} resolves to "
                    f"{target._meta.label}, not {currency_label}.",
                    hint=f"Point currency_field at a foreign key to {currency_label}.",
                    obj=self,
                    id="angee.E013",
                )
            ]
        return []

    def _foreign_key_target(
        self, model: type[models.Model], field_name: str
    ) -> type[models.Model] | checks.Error | None:
        """Resolve one path segment to its related model, an error, or ``None``."""

        try:
            field = model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return checks.Error(
                f"MoneyField currency_field={self.currency_field!r}: "
                f"{model._meta.label} has no field {field_name!r}.",
                obj=self,
                id="angee.E011",
            )
        if not (getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False)):
            return checks.Error(
                f"MoneyField currency_field={self.currency_field!r}: "
                f"{model._meta.label}.{field_name} is not a foreign key.",
                obj=self,
                id="angee.E012",
            )
        related = field.related_model
        if related is None or isinstance(related, str):
            return None
        return related


register_field_type(MoneyField, decimal.Decimal)
