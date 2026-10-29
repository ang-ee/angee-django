"""Abstract public identity and native model-owned identity lookup."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any, ClassVar, TypeVar, cast

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models

from angee.base.fields.identity import SqidField

_ModelT = TypeVar("_ModelT", bound=models.Model)


class PublicIdentity(models.Model):
    """Model-owned public identity, shared without importing composed model bases."""

    class Meta:
        abstract = True

    @property
    def public_id(self) -> str:
        """Return the stable public identifier for this model instance."""

        value = self.public_id_value()
        return "" if value in (None, "") else str(value)

    def public_id_value(self) -> Any:
        """Return the raw public identifier value owned by this instance."""

        return self.pk

    @classmethod
    def public_id_from_pk(cls, value: Any) -> str:
        """Return the public id encoded from this model's primary-key value."""

        return "" if value in (None, "") else str(value)

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the primary-key lookup; public-id fields override this contract."""
        return {cls._meta.pk.name: value}


class SqidMixin(PublicIdentity, models.Model):
    """Add an opaque public identifier backed by the model primary key.

    A model sets only the varying fact — its prefix — as ``sqid_prefix``
    (e.g. ``sqid_prefix = "nte_"``); the shared ``sqid`` column reads it (see
    ``SqidField.contribute_to_class``), so no model re-declares the field.
    """

    sqid_prefix: ClassVar[str] = ""
    """Public-id prefix for ``sqid`` (e.g. ``"nte_"``); empty means no prefix."""

    sqid = SqidField(real_field_name="id", min_length=8)
    """Opaque public identifier encoded from the integer primary key."""

    class Meta:
        """Django model options for sqid-only abstract inheritance."""

        abstract = True

    def public_id_value(self) -> Any:
        """Return the raw public identifier value for this instance."""

        return self.sqid

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the Django lookup for this model's public identifier."""

        return {"sqid": value}

    @classmethod
    def public_id_from_pk(cls, value: Any) -> str:
        """Return the public id encoded from this model's primary-key value."""

        # SqidMixin declares ``sqid = SqidField(...)`` unconditionally, so the column
        # is always a SqidField on any subclass.
        field = cast(SqidField, cls._meta.get_field("sqid"))
        return field.public_id_from_value(value)


def public_id_lookup(
    model: type[models.Model],
    value: str,
) -> dict[str, Any]:
    """Return the model-owned lookup for a generic or third-party public identity."""

    if issubclass(model, PublicIdentity):
        return model.public_id_lookup(value)
    pk = model._meta.pk
    return {pk.name: value} if pk is not None else {}


def instance_from_public_id(
    model: type[_ModelT],
    value: str,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
) -> _ModelT | None:
    """Resolve one public identity through the shared batch owner."""

    return instances_from_public_ids(
        model,
        (value,),
        queryset=queryset,
    ).get(value)


def instances_from_public_ids(
    model: type[_ModelT],
    values: Iterable[str],
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
) -> dict[str, _ModelT]:
    """Resolve public ids with field-owned decoding and native batched retrieval.

    Preserve the supplied ids as keys; missing or malformed values are omitted.
    Single unique-field lookups use ``in_bulk``; other lookups retain native
    per-id filtering. Sqid fields own decoding and declare their backing field;
    ordinary native fields own their PK conversion. The optional queryset
    retains its database and permission scope.
    """

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    resolved: dict[str, _ModelT] = {}
    lookups: dict[str, tuple[str, Any]] = {}
    values_by_field: dict[str, set[Any]] = defaultdict(set)
    for public_id in values:
        if not public_id:
            continue
        try:
            lookup = public_id_lookup(model, public_id)
            field = None
            value: Any = None
            if len(lookup) == 1:
                field_name = next(iter(lookup))
                value = lookup[field_name]
                try:
                    field = model._meta.pk if field_name == "pk" else model._meta.get_field(field_name)
                except FieldDoesNotExist:
                    pass
                if isinstance(field, SqidField):
                    value = field.public_id_to_value(value)
                    field = field.real_col
            if not isinstance(field, models.Field) or not field.unique or field.is_relation:
                instance = active_queryset.filter(**lookup).first()
                if instance is not None:
                    resolved[public_id] = instance
                continue
            value = field.to_python(value)
            if value is not None:
                lookups[public_id] = (field.name, value)
                values_by_field[field.name].add(value)
        except TypeError, ValueError, ValidationError:
            continue
    instances = {
        (field_name, value): instance
        for field_name, field_values in values_by_field.items()
        for value, instance in active_queryset.in_bulk(field_values, field_name=field_name).items()
    }
    resolved.update((public_id, instances[lookup]) for public_id, lookup in lookups.items() if lookup in instances)
    return resolved


def public_id_of(instance: models.Model) -> str:
    """Return the public id for a generic model instance."""

    if isinstance(instance, PublicIdentity):
        return instance.public_id
    pk = instance.pk
    if pk in (None, ""):
        return ""
    return str(pk)


def public_id_for(
    model: type[models.Model],
    pk: Any,
) -> str:
    """Return a generic model's public id when only its primary key is known."""

    if pk in (None, ""):
        return ""
    if issubclass(model, PublicIdentity):
        return model.public_id_from_pk(pk)
    return str(pk)
