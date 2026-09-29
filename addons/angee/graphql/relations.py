"""Guard-aware relation fields for Strawberry-Django schemas."""

from __future__ import annotations

from typing import Any

import strawberry_django
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.expressions import Combinable
from django.db.models.functions import Cast
from rebac import current_actor
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type
from strawberry import Info
from strawberry_django.optimizer import optimize
from strawberry_django.queryset import run_type_get_queryset
from strawberry_django.utils.typing import get_django_definition, unwrap_type

from angee.base.refs import RecordRefMixin
from angee.base.scoping import aggregate_scoped_queryset, read_scoped_queryset
from angee.data.field_classification import is_to_one_relation
from angee.graphql.introspection import FieldPathError, fields_for_path

_UNCACHED = object()


def with_record_reference_access(queryset: models.QuerySet[Any]) -> models.QuerySet[Any]:
    """Annotate ``_angee_record_readable`` from the referenced records' read policy.

    Generic references need a typed membership guard for every represented model.
    Read content types once, then use native scoped subqueries without loading
    target rows. Projection and resource filter axes share this annotation, so
    a hidden reference cannot be recovered through filtering or aggregate counts.
    """
    if not issubclass(queryset.model, RecordRefMixin):
        raise ImproperlyConfigured("Record-reference access requires RecordRefMixin.")
    reference = queryset.model.record_ref_field()
    key_field = queryset.model._meta.get_field(reference.fk_field)
    content_types = ContentType.objects.filter(pk__in=aggregate_scoped_queryset(queryset).order_by().values(
        reference.ct_field,
    )).order_by("app_label", "model")
    actor = relation_actor(queryset)
    readable = models.Q(pk__in=[])
    for content_type in content_types:
        model = content_type.model_class()
        if model is None:
            continue
        targets = read_scoped_queryset(model, actor)
        if targets is not None:
            keys = aggregate_scoped_queryset(targets).order_by().annotate(
                _angee_reference_key=Cast("pk", output_field=key_field),
            ).values("_angee_reference_key")
            readable |= models.Q(**{
                reference.ct_field_attname: content_type.pk,
                f"{reference.fk_field}__in": keys,
            })
    return queryset.annotate(_angee_record_readable=models.ExpressionWrapper(
        readable, output_field=models.BooleanField(),
    ))


def actor_scoped_relation_expression(
    queryset: models.QuerySet[Any],
    field_path: str,
) -> Combinable | None:
    """Return a read-safe scalar expression for one related query axis.

    Every protected target crossed by the selected to-one path contributes an
    uncorrelated membership guard. The related scalar is projected only when
    all guarded rows are readable by the source queryset's actor; otherwise it
    becomes SQL ``NULL`` while the parent row remains visible. Paths with no
    protected target need no override and return ``None``.
    """

    try:
        fields = fields_for_path(queryset.model, field_path)
    except FieldPathError as error:
        raise ImproperlyConfigured(
            f"{queryset.model._meta.label}.{field_path} must traverse "
            "to-one relations to a scalar field"
        ) from error
    terminal = fields[-1]
    relations = fields[:-1]
    if not relations or terminal.is_relation:
        return None
    if not all(is_to_one_relation(field) for field in relations):
        raise ImproperlyConfigured(
            f"{queryset.model._meta.label}.{field_path} must traverse "
            "to-one relations to a scalar field"
        )

    actor = relation_actor(queryset)
    guards: list[models.Q] = []
    traversed: list[str] = []
    parts = field_path.split("__")
    for part, relation in zip(parts[:-1], relations, strict=True):
        related_model = relation.related_model
        if not model_resource_type(related_model):
            traversed.append(part)
            continue
        related_queryset = read_scoped_queryset(related_model, actor)
        if related_queryset is None:
            related_queryset = related_model._default_manager.none()
        else:
            related_queryset = aggregate_scoped_queryset(related_queryset)

        if isinstance(relation, (models.ForeignKey, models.OneToOneField)):
            lookup = "__".join((*traversed, relation.attname))
            target_name = relation.target_field.attname
        else:
            lookup = "__".join((*traversed, part, related_model._meta.pk.attname))
            target_name = related_model._meta.pk.attname
        guards.append(
            models.Q(
                **{
                    f"{lookup}__in": related_queryset.values_list(
                        target_name, flat=True
                    )
                }
            )
        )
        traversed.append(part)

    if not guards:
        return None
    guard = models.Q()
    for item in guards:
        guard &= item
    return models.Case(
        models.When(guard, then=models.F(field_path)),
        default=models.Value(None),
        output_field=terminal,
    )


def actor_scoped_to_one(field_name: str, *, reverse: bool = False) -> Any:
    """Return a nullable forward/reverse to-one field redacting unreadable targets.

    The parent may be actor-scoped or sudo-loaded: a cached related object is used
    only when REBAC stamped it for the current actor; otherwise the stored FK value
    is re-gated through the target model's actor-scoped manager. Missing access
    returns ``None`` rather than raising. Strawberry-Django's native relation
    projection and typed prefetch batch a selected relation once per parent list,
    including the target type's queryset hook and selected annotations.
    ``reverse=True`` selects a reverse one-to-one, which has no local FK column
    to include in the parent's optimized projection.
    """

    def prefetch(info: Info) -> models.Prefetch:
        related_type = unwrap_type(info.return_type)
        related_model = get_django_definition(related_type, strict=True).model
        queryset = read_scoped_queryset(related_model, current_actor())
        if queryset is None:
            queryset = related_model._default_manager.none()
        queryset = run_type_get_queryset(queryset, related_type, info)
        return models.Prefetch(field_name, queryset=optimize(queryset, info))

    def resolve(root: models.Model, info: Info) -> Any:
        field = root._meta.get_field(field_name)
        if not is_to_one_relation(field):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} must be a to-one relation"
            )

        if reverse != isinstance(field, models.OneToOneRel):
            raise ImproperlyConfigured(f"{root._meta.label}.{field_name} has an incorrect relation direction")
        fk_id = field.field.target_field.value_from_object(root) if reverse else field.value_from_object(root)
        if fk_id is None:
            return None

        actor = current_actor()
        if actor is None:
            return None

        cached = root._state.fields_cache.get(field.name, _UNCACHED)
        if cached is None:
            return None
        if cached is not _UNCACHED and getattr(cached, "_rebac_actor", None) == actor:
            return cached

        related_model = field.remote_field.model
        queryset = related_model._default_manager.all()
        with_actor = getattr(queryset, "with_actor", None)
        if not callable(with_actor):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {related_model._meta.label}, "
                "whose default manager is not actor-scoped"
            )
        target_field = field.field if reverse else field.target_field
        queryset = with_actor(actor).filter(**{target_field.attname: fk_id})
        return run_type_get_queryset(queryset, unwrap_type(info.return_type), info).first()

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        only=[] if reverse else [f"{field_name}_id"],
        prefetch_related=[prefetch],
    )


def actor_scoped_to_many(field_name: str) -> Any:
    """Return a to-many field whose rows are scoped to the current actor.

    The parent may be actor-scoped through one relation while the selected
    to-many relation contains other protected rows. Resolve the relation through
    the target model's actor-scoped queryset instead of exposing the raw related
    manager. Both cached and queried rows retain their model's native ordering.
    """

    def resolve(root: models.Model) -> Any:
        field = root._meta.get_field(field_name)
        if not (
            getattr(field, "many_to_many", False)
            or getattr(field, "one_to_many", False)
        ):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} must be a forward or reverse to-many relation"
            )

        actor = current_actor()
        if actor is None:
            return []

        cached = getattr(root, "_prefetched_objects_cache", {}).get(field_name, _UNCACHED)
        if cached is not _UNCACHED and all(
            getattr(row, "_rebac_actor", None) == actor for row in cached
        ):
            return cached
        related_queryset = getattr(root, field_name).all()
        with_actor = getattr(related_queryset, "with_actor", None)
        if not callable(with_actor):
            raise ImproperlyConfigured(
                f"{root._meta.label}.{field_name} targets {field.related_model._meta.label}, "
                "whose related manager is not actor-scoped"
            )
        return with_actor(actor)

    return strawberry_django.field(
        resolver=resolve,
        field_name=field_name,
        prefetch_related=[field_name],
    )
