"""Public identity field metadata and REBAC transport subject adapters."""

from __future__ import annotations

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from rebac import SubjectRef
from rebac.resources import model_for_resource_type

from angee.base.fields.identity import SqidField


def public_data_id_field(model: type[models.Model]) -> SqidField | None:
    """Return the sqid field that makes ``model`` safe for public data surfaces."""

    try:
        field = model._meta.get_field("sqid")
    except FieldDoesNotExist:
        return None
    return field if isinstance(field, SqidField) else None


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
    if pk is None:
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
