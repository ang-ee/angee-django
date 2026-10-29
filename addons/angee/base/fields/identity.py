"""Opaque public identity field over django-sqids."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django_sqids import SqidsField
from sqids import Sqids


def _canonical_sqid_prefix(prefix: str) -> str:
    """Return ``prefix`` carrying Angee's public-id separator (``abc`` -> ``abc_``)."""

    if not prefix:
        return ""
    return prefix if prefix.endswith("_") else f"{prefix}_"


class SqidField(SqidsField):
    """Angee's opaque public id column, declared as ``django-sqids`` glue.

    ``docs/stack.md`` names ``django-sqids`` the owner of opaque external ids;
    this wrapper makes the decoder total and lets a model state only the one
    fact that varies between models — the prefix. A model declares
    ``sqid_prefix = "nte_"`` (``SqidMixin`` exposes the attribute and the shared
    column); the field reads it in ``contribute_to_class`` rather than every
    model re-declaring the whole column. An explicit ``prefix=`` still wins.

    Totality: ``from_db_value`` receives ``None`` when the encoded column
    arrives through a nullable join — e.g. ``values_list("parent__sqid")`` over
    a nullable self-FK, the shape REBAC field-backed arrows query — and upstream
    encodes unconditionally there.

    The field is also usable unbound as a pure public-id codec through
    ``public_id_from_value`` and ``public_id_to_value``, including when only a
    stored public id remains after its original model is retired.
    """

    sqids_instance: Sqids | None

    def __init__(self, *args: Any, prefix: str = "", **kwargs: Any) -> None:
        """Normalize Angee public-id prefixes to the canonical ``abc_`` shape."""

        self._angee_declared_prefix = prefix
        super().__init__(*args, prefix=_canonical_sqid_prefix(prefix), **kwargs)

    def contribute_to_class(self, cls: type[models.Model], name: str) -> None:
        """Resolve the prefix from the model's ``<field>_prefix`` when unset.

        Lets ``SqidMixin``'s one shared column serve every model: each model
        states only ``sqid_prefix = "nte_"`` and the inherited field picks it up
        here. ``sqid`` is private and non-concrete, so Django does not serialize
        it as a schema field. Authored migrations can still import its codec.
        """

        super().contribute_to_class(cls, name)
        if not self._angee_declared_prefix:
            declared = getattr(cls, f"{name}_prefix", "")
            if not isinstance(declared, str):
                raise ImproperlyConfigured(
                    f"{cls.__name__}.{name}_prefix must be a str, got {type(declared).__name__}."
                )
            self.prefix = _canonical_sqid_prefix(declared)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        """Serialize the full public-id contract for generated/runtime models.

        Emits the *resolved* ``prefix`` (not the declared one), so an emitted or
        migration-state model carries the full prefix without needing the
        source's ``sqid_prefix`` class attribute.
        """

        name, path, args, kwargs = super().deconstruct()
        kwargs["real_field_name"] = self.real_field_name
        if self.prefix:
            kwargs["prefix"] = self.prefix
        if self.min_length is not None:
            kwargs["min_length"] = self.min_length
        if self.alphabet is not None:
            kwargs["alphabet"] = self.alphabet
        if self._explicit_sqids_instance is not None:
            kwargs["sqids_instance"] = self._explicit_sqids_instance
        return name, path, args, kwargs

    def from_db_value(self, value: Any, expression: Any, connection: Any, *args: Any) -> Any:
        """Return the encoded public id, passing NULL columns through.

        ``django_sqids`` ``from_db_value`` encodes unconditionally, so a NULL
        arriving through a nullable join crashes it (``sqids.encode([None])``
        raises ``TypeError``); this guard is the workaround. The durable fix is an
        upstream ``django_sqids`` PR, after which this override can be deleted.
        """

        if value is None:
            return None
        return super().from_db_value(value, expression, connection, *args)

    def public_id_from_value(self, value: Any) -> str:
        """Return the encoded public id for one backing integer value."""

        if value in (None, ""):
            return ""
        encoded = self._public_id_codec().encode([int(value)])
        return f"{self.prefix}{encoded}" if encoded is not None else ""

    def public_id_to_value(self, public_id: Any) -> int | None:
        """Return the backing integer decoded from one public id."""

        if public_id in (None, ""):
            return None
        self.sqids_instance = self._public_id_codec()
        decoded = self.get_prep_value(str(public_id))
        return (
            cast(int, decoded) if decoded is not None and self.public_id_from_value(decoded) == str(public_id) else None
        )

    def _public_id_codec(self) -> Sqids:
        """Return the field's Sqids codec, initialising unbound adapter fields."""

        codec = self.sqids_instance
        if codec is None:
            codec = cast(Sqids, self.get_sqid_instance())
            self.sqids_instance = codec
        return codec
