"""Transport-neutral public-identity adapters for Django models."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from rebac import SubjectRef
from rebac.resources import model_for_resource_type

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


def public_id_lookup(
    model: type[models.Model],
    value: str,
    *,
    public_identity: SqidPublicIdentity | None = None,
) -> dict[str, Any]:
    """Return the model-owned lookup for a generic or third-party public identity."""

    if public_identity is not None:
        return public_identity.public_id_lookup(model, value)
    if issubclass(model, AngeeModel):
        return model.public_id_lookup(value)
    pk = model._meta.pk
    return {pk.name: value} if pk is not None else {}


def instance_from_public_id(
    model: type[_ModelT],
    value: str,
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
    public_identity: SqidPublicIdentity | None = None,
) -> _ModelT | None:
    """Resolve one public identity through the shared batch owner."""

    return instances_from_public_ids(
        model, (value,), queryset=queryset, public_identity=public_identity,
    ).get(value)


def instances_from_public_ids(
    model: type[_ModelT],
    values: Iterable[str],
    *,
    queryset: models.QuerySet[_ModelT] | None = None,
    public_identity: SqidPublicIdentity | None = None,
) -> dict[str, _ModelT]:
    """Resolve public ids with field-owned decoding and native batched retrieval.

    Preserve the supplied ids as keys; missing or malformed values are omitted.
    Single unique-field lookups use ``in_bulk``; other lookups retain native
    per-id filtering. Sqid fields own decoding and declare their backing field;
    third-party adapters own their pk conversion. The optional queryset retains
    its database and permission scope.
    """

    active_queryset = queryset if queryset is not None else model._default_manager.all()
    resolved: dict[str, _ModelT] = {}
    lookups: dict[str, tuple[str, Any]] = {}
    values_by_field: dict[str, set[Any]] = defaultdict(set)
    for public_id in values:
        if not public_id:
            continue
        try:
            lookup = public_id_lookup(model, public_id, public_identity=public_identity)
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
        except (TypeError, ValueError, ValidationError):
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


def canonical_subject_ref(value: str) -> SubjectRef:
    """Decode a transport subject into its canonical REBAC identity."""

    subject = SubjectRef.parse(value)
    if subject.subject_id in {"", "*"}:
        return subject
    model = model_for_resource_type(subject.subject_type)
    if model is None or not model._meta.managed:
        return subject
    field = public_data_id_field(model)
    if field is None:
        return subject
    pk = field.public_id_to_value(subject.subject_id)
    is_public_id = pk is not None and field.public_id_from_value(pk) == subject.subject_id
    if not is_public_id:
        if not field.prefix:
            raise ValueError(f"Subject {value!r} has an invalid public id.")
        model_pk = model._meta.pk
        if model_pk is None:
            raise ValueError(f"Subject {value!r} has no model identity.")
        try:
            pk = model_pk.to_python(subject.subject_id)
        except (TypeError, ValueError, ValidationError) as error:
            raise ValueError(f"Subject {value!r} has an invalid canonical id.") from error
    return SubjectRef.of(subject.subject_type, str(pk), subject.optional_relation)


def public_subject_ref(subject: SubjectRef) -> SubjectRef:
    """Encode a canonical model-backed subject for a transport boundary."""

    if subject.subject_id in {"", "*"}:
        return subject
    model = model_for_resource_type(subject.subject_type)
    if model is None or not model._meta.managed:
        return subject
    field = public_data_id_field(model)
    if field is None:
        return subject
    return SubjectRef.of(
        subject.subject_type,
        field.public_id_from_value(subject.subject_id),
        subject.optional_relation,
    )
