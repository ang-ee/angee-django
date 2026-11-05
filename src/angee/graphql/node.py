"""GraphQL identity primitive for Angee types."""

from __future__ import annotations

from functools import cache
from typing import Self, cast

import strawberry
import strawberry_django
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Prefetch
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.optimizer import OptimizerStore

from angee.base.models.base import AngeeModel, record_display_label
from angee.base.models.identity import public_id_of
from angee.graphql.ids import PublicID

NODE_DISPLAY_NAME_DESCRIPTION = "Human-readable label for this object."
_MISSING = object()


@cache
def _display_name_paths(model: type[AngeeModel]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve one model's direct label columns and related rows once."""

    paths = model.record_display_paths
    if paths is None:
        if model.__str__ is models.Model.__str__:
            return (model._meta.pk.attname,), ()
        return tuple(field.attname for field in model._meta.concrete_fields), ()

    only: list[str] = []
    related: list[str] = []
    for path in paths:
        if "__" in path:
            raise ValueError(f"{model._meta.label}.record_display_paths accepts direct fields only: {path}")
        field = model._meta.get_field(path)
        if isinstance(field, (models.ForeignKey, models.OneToOneField)) and path == field.name:
            only.append(field.attname)
            related.append(path)
        else:
            only.append(path)
    return tuple(only), tuple(related)


@cache
def _display_name_store(model: type[AngeeModel]) -> OptimizerStore:
    """Cache the native label hints without storing a request's actor or scope."""

    only, related = _display_name_paths(model)
    return OptimizerStore.with_hints(
        only=only,
        prefetch_related=[Prefetch(path, to_attr=f"_angee_display_{path}") for path in related],
    )


class _DisplayNameField(StrawberryDjangoField):
    """Apply the model's label dependencies to each concrete node projection."""

    @property
    def store(self) -> OptimizerStore:
        definition = getattr(self, "origin_django_type", None)
        model = definition.model if definition is not None else None
        if model is None or not issubclass(model, AngeeModel):
            return self._base_store

        return self._base_store | _display_name_store(model)

    @store.setter
    def store(self, value: OptimizerStore) -> None:
        self._base_store = value

    def __copy__(self) -> Self:
        copied = super().__copy__()
        copied._base_store = self._base_store.copy()
        return copied


@strawberry.interface(name="Node")
class AngeeNode:
    """GraphQL object whose id field is the model's public id."""

    @strawberry.field(description="The public ID of this object.")
    def id(self) -> PublicID:
        """Return this row's public id."""

        return PublicID(public_id_of(cast(models.Model, self)))

    @strawberry_django.field(field_cls=_DisplayNameField, description=NODE_DISPLAY_NAME_DESCRIPTION)
    def display_name(self) -> str:
        """Return the record's human label from its model-owned representation.

        Every node carries a label without each type re-declaring one. A model
        that stores an editable, searchable label declares its own
        ``display_name`` field, which overrides this resolver on that type.
        Models may narrow the default concrete-column load or declare related
        label sources with ``record_display_paths``.
        """

        row = cast(models.Model, self)
        hidden_related = False
        if isinstance(row, AngeeModel):
            for path in _display_name_paths(type(row))[1]:
                related = getattr(row, f"_angee_display_{path}", _MISSING)
                if related is _MISSING:
                    continue  # Instance-returning resolvers have no optimizer prefetch.
                field = cast(models.ForeignKey, row._meta.get_field(path))
                if related is None:
                    if getattr(row, field.attname) is not None:
                        hidden_related = True
                    field.set_cached_value(row, None)
                else:
                    field.set_cached_value(row, cast(models.Model, related))
        try:
            return record_display_label(row)
        except ObjectDoesNotExist:
            if not hidden_related:
                raise
            return str(row._meta.verbose_name)
