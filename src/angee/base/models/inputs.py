"""Cooperative consumption of model write-input extensions."""

from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models


class InputExtensionsMixin(models.Model):
    """Fieldless terminal shared by Angee models and input-owning model mixins."""

    class Meta:
        abstract = True

    @classmethod
    def pop_input_extensions(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Remove and return the write-input values no field of this model owns.

        A name this model's ``_meta.get_field`` resolves (a field, its attname
        or a relation) stays in ``values`` for the writer to persist with the
        row; every other name is an input extension, which the writer hands to
        the written row's :meth:`apply_input_extensions` in the same transaction.
        """

        def owned(name: str) -> bool:
            try:
                cls._meta.get_field(name)
            except FieldDoesNotExist:
                return False
            return True

        return {name: values.pop(name) for name in [name for name in values if not owned(name)]}

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
