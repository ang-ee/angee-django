"""Framework base for registry-resolved implementation classes.

An :class:`~angee.base.fields.ImplClassField` column selects one of these by key.
Beyond the behaviour its domain needs, an impl carries shared metadata and
*defaults* — the field values the owning row materialises when the impl is
chosen. Defaults merge along the MRO, so refinements inherit their base's
defaults and override only what differs. Abstract bases leave ``key`` blank and
stay out of the registry; concrete leaves register a key and are pickable.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from angee.base.fields import ImplClassField
from angee.base.impl_types import ImplBase, ImplChoice

__all__ = ["ImplBase", "ImplChoice", "ImplDefaultsMixin"]


class ImplDefaultsMixin(models.Model):
    """Materialise impl defaults on create for every ``ImplClassField`` on the model.

    The backend safety net behind the form-level prefill: a row created without a
    form (API, resource seed) still gets the chosen impl's defaults — for the fields
    the caller did not supply. Form-created rows pass their (possibly edited) values,
    so the impl never overrides them, even when a value equals the model default.
    """

    class Meta:
        """Abstract: contributes the create-time default seeding only."""

        abstract = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record the caller-supplied field names so create-time seeding skips them."""

        self._impl_provided_fields = frozenset(kwargs)
        super().__init__(*args, **kwargs)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Seed impl defaults for unsupplied fields on first insert, then persist."""

        if self._state.adding:
            provided: frozenset[str] = getattr(self, "_impl_provided_fields", frozenset())

            for field in self._meta.get_fields():
                if not isinstance(field, ImplClassField):
                    continue
                key = getattr(self, field.attname, None)
                if not key:
                    continue
                impl = field.resolve_class(key)
                if isinstance(impl, type) and issubclass(impl, ImplBase):
                    impl.materialize(self, provided=provided)
        super().save(*args, **kwargs)

    def set_impl_key(self, field_name: str, value: Any, *, default: str | None = None) -> bool:
        """Assign an impl key and return whether the stored key changed."""

        field = type(self)._impl_field(field_name)
        key = type(self).impl_key_for(field_name, value, default=default)
        changed = key != getattr(self, field.attname)
        setattr(self, field.attname, key)
        return changed

    def materialize_impl_defaults(self, field_name: str, *, provided: frozenset[str] = frozenset()) -> None:
        """Apply the selected impl's defaults for one impl field."""

        field = type(self)._impl_field(field_name)
        key = getattr(self, field.attname, None)
        if not key:
            return
        impl = field.resolve_class(key)
        if isinstance(impl, type) and issubclass(impl, ImplBase):
            impl.materialize(self, provided=provided | {field.name, field.attname})
