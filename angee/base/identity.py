"""Transport-neutral public-identity adapters for Django models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar, cast

from django.core.exceptions import FieldDoesNotExist
from django.db import models

from angee.base.fields import SqidField
from angee.base.models import AngeeModel

_ModelT = TypeVar("_ModelT", bound=models.Model)


@dataclass(frozen=True, slots=True)
class SqidPublicIdentity:
    """Sqid adapter for a third-party model that Angee cannot add a field to."""

    prefix: str
    min_length: int | None = None
    alphabet: str | None = None

    def public_id_from_pk(self, value: Any) -> str:
        """Return the public id encoded from a primary-key value."""

        return self.sqid_field.public_id_from_value(value)

    def public_id_to_pk(self, value: str) -> int | None:
        """Decode one public id to the backing primary-key value."""

        return self.sqid_field.public_id_to_value(value)

    def public_id_lookup(self, model: type[models.Model], value: str) -> dict[str, Any]:
        """Return a Django lookup for ``value`` against ``model``."""

        pk = model._meta.pk
        return {pk.name: self.public_id_to_pk(value)} if pk is not None else {}

    @property
    def sqid_field(self) -> SqidField:
        """Return the owner field used to encode and decode this adapter's ids."""

        # Per-call so SqidField stays the codec owner without model attachment.
        return SqidField(
            real_field_name="id",
            prefix=self.prefix,
            min_length=self.min_length,
            alphabet=self.alphabet,
        )


def public_data_id_field(model: type[models.Model]) -> SqidField | None:
    """Return the sqid field that makes ``model`` safe for public data surfaces."""

    for owner in (model, *model._meta.get_parent_list()):
        try:
            field = owner._meta.get_field("sqid")
        except FieldDoesNotExist:
            continue
        if isinstance(field, SqidField):
            return field
    return None


def instance_from_public_id(
    model: type[_ModelT],
    value: str,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
    public_identity: SqidPublicIdentity | None = None,
) -> _ModelT | None:
    """Resolve a generic model or third-party identity; known Angee owners delegate."""

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    if value == "":
        return None
    try:
        if public_identity is not None:
            lookup = public_identity.public_id_lookup(model, value)
        elif issubclass(model, AngeeModel):
            lookup = model.public_id_lookup(value)
        else:
            pk = model._meta.pk
            lookup = {pk.name: value} if pk is not None else {}
        return cast(_ModelT | None, active_queryset.filter(**lookup).first())
    except (TypeError, ValueError):
        return None


def public_id_of(instance: models.Model) -> str:
    """Return the public id for a generic model instance."""

    public_id = getattr(instance, "public_id", None)
    if isinstance(public_id, str):
        return public_id
    pk = instance.pk
    if pk in (None, ""):
        return ""
    return str(pk)


def public_id_for(
    model: type[models.Model],
    pk: Any,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> str:
    """Return a generic model's public id when only its primary key is known."""

    if pk in (None, ""):
        return ""
    if public_identity is not None:
        return public_identity.public_id_from_pk(pk)
    resolver = getattr(model, "public_id_from_pk", None)
    if callable(resolver):
        return str(resolver(pk))
    return str(pk)
