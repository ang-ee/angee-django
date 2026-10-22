"""Cooperative consumption of model write-input extensions."""

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import models


class InputExtensionsMixin(models.Model):
    """Fieldless terminal shared by Angee models and input-owning model mixins."""

    class Meta:
        abstract = True

    def apply_input_extensions(self, **values: Any) -> None:
        """Terminal cooperative hook for write-input values no model field owns.

        A GraphQL input extension can add a field to this model's generated
        insert/set input that is not a column, such as a relation an edge owns.
        The write backend persists the row, then calls this hook in the same
        transaction with those values. A mixin or ``extends`` donor declares a
        keyword-only parameter for each value it owns, applies it and delegates
        the rest exactly once through ``super()``; a value reaching this
        terminal has no owner, so it fails fast instead of being dropped.
        """

        if values:
            raise ImproperlyConfigured(
                f"{type(self)._meta.label} has no owner for input extension values: {', '.join(sorted(values))}."
            )
