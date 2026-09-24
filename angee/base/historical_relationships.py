"""Frozen compatibility for materialized historical migrations only.

Do not use this module in new application code or migration declarations. These
functions preserve the historical REBAC storage shapes and exact-tuple semantics
expected by already-materialized migrations.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from django.core.exceptions import ImproperlyConfigured
from django.db import models, router, transaction
from rebac import ObjectRef, RelationshipTuple
from rebac.models import active_relationship_model

_DENORMALIZED_FIELDS = frozenset(
    {
        "id",
        "resource_type",
        "resource_id",
        "relation",
        "subject_type",
        "subject_id",
        "optional_subject_relation",
        "caveat_name",
        "caveat_context",
        "expires_at",
        "written_at_xid",
    }
)
_REGISTRY_FIELDS = frozenset(
    {
        "id",
        "resource_fk",
        "relation",
        "subject_fk",
        "optional_subject_relation",
        "caveat_name",
        "caveat_context",
        "expires_at",
        "written_at_xid",
    }
)
_RESOURCE_FIELDS = frozenset({"id", "resource_type", "resource_id", "content_type", "object_pk"})


def _field_names(model: type[models.Model]) -> frozenset[str]:
    return frozenset(field.name for field in model._meta.local_fields)


def _require_field(
    model: type[models.Model],
    name: str,
    field_type: type[models.Field],
    **attributes: Any,
) -> models.Field:
    field = model._meta.get_field(name)
    if not isinstance(field, field_type) or any(
        getattr(field, attribute) != expected for attribute, expected in attributes.items()
    ):
        raise ImproperlyConfigured(f"The historical REBAC field {model._meta.label}.{name} is unsupported.")
    return field


def _require_unique_constraint(
    model: type[models.Model],
    fields: tuple[str, ...],
) -> None:
    if not any(
        isinstance(constraint, models.UniqueConstraint)
        and tuple(constraint.fields) == fields
        and constraint.condition is None
        for constraint in model._meta.constraints
    ):
        raise ImproperlyConfigured(
            f"The historical REBAC model {model._meta.label} lacks its exact identity constraint."
        )


def _validate_common_relationship_fields(model: type[models.Model]) -> None:
    _require_field(model, "id", models.BigAutoField, primary_key=True)
    _require_field(model, "relation", models.CharField, max_length=64)
    _require_field(
        model,
        "optional_subject_relation",
        models.CharField,
        max_length=64,
        blank=True,
        default="",
    )
    _require_field(
        model,
        "caveat_name",
        models.CharField,
        max_length=64,
        blank=True,
        default="",
    )
    _require_field(
        model,
        "caveat_context",
        models.JSONField,
        null=True,
        blank=True,
    )
    _require_field(
        model,
        "expires_at",
        models.DateTimeField,
        null=True,
        blank=True,
    )
    _require_field(model, "written_at_xid", models.BigIntegerField, default=0)
    reverse_relations = tuple(
        field for field in model._meta.get_fields(include_hidden=True) if field.auto_created and not field.concrete
    )
    if model._meta.parents or model._meta.local_many_to_many or model._meta.private_fields or reverse_relations:
        raise ImproperlyConfigured(
            f"The historical REBAC model {model._meta.label} is not safe for exact raw deletion."
        )


def _historical_relationship_store(
    apps: Any,
) -> tuple[
    Literal["denormalized", "registry"],
    type[models.Model],
    type[models.Model] | None,
]:
    """Resolve one exact supported historical REBAC storage shape."""

    # The live owner selects only the configured storage *name*. All reads and
    # writes below remain bound to the historical app registry.
    model_name = active_relationship_model()._meta.object_name
    relationship = apps.get_model("rebac", model_name)
    fields = _field_names(relationship)
    if model_name == "Relationship" and fields == _DENORMALIZED_FIELDS:
        _validate_common_relationship_fields(relationship)
        for name in ("resource_type", "resource_id", "subject_type", "subject_id"):
            _require_field(relationship, name, models.CharField, max_length=64)
        _require_unique_constraint(
            relationship,
            (
                "resource_type",
                "resource_id",
                "relation",
                "subject_type",
                "subject_id",
                "optional_subject_relation",
                "caveat_name",
            ),
        )
        return "denormalized", relationship, None
    if model_name != "RelationshipRegistry" or fields != _REGISTRY_FIELDS:
        raise ImproperlyConfigured("The configured historical REBAC relationship storage shape is unsupported.")
    _validate_common_relationship_fields(relationship)
    resource = apps.get_model("rebac", "RebacResource")
    if _field_names(resource) != _RESOURCE_FIELDS:
        raise ImproperlyConfigured("The historical REBAC resource registry shape is unsupported.")
    _require_field(resource, "id", models.BigAutoField, primary_key=True)
    for name in ("resource_type", "resource_id"):
        _require_field(resource, name, models.CharField, max_length=64)
    _require_field(
        resource,
        "object_pk",
        models.CharField,
        max_length=64,
        blank=True,
        default="",
    )
    content_type = _require_field(
        resource,
        "content_type",
        models.ForeignKey,
        null=True,
        blank=True,
    )
    historical_content_type = apps.get_model("contenttypes", "ContentType")
    if (
        content_type.remote_field.model is not historical_content_type
        or content_type.remote_field.on_delete is not models.CASCADE
        or content_type.remote_field.related_name != "+"
    ):
        raise ImproperlyConfigured("The historical REBAC resource backing relation is unsupported.")
    _require_unique_constraint(resource, ("resource_type", "resource_id"))
    for name in ("resource_fk", "subject_fk"):
        field = relationship._meta.get_field(name)
        if (
            not isinstance(field, models.ForeignKey)
            or field.remote_field.model is not resource
            or field.remote_field.on_delete is not models.CASCADE
            or field.db_column != f"{name}_id"
        ):
            raise ImproperlyConfigured("The historical REBAC relationship registry endpoints are unsupported.")
    _require_unique_constraint(
        relationship,
        (
            "resource_fk",
            "relation",
            "subject_fk",
            "optional_subject_relation",
            "caveat_name",
        ),
    )
    return "registry", relationship, resource


def _relationship_facts(value: RelationshipTuple) -> dict[str, Any]:
    if not isinstance(value, RelationshipTuple):
        raise TypeError("Historical REBAC writes require RelationshipTuple values.")
    (
        resource_type,
        resource_id,
        relation,
        subject_type,
        subject_id,
        optional_subject_relation,
        caveat_name,
    ) = value.canonical_key()
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "relation": relation,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "optional_subject_relation": optional_subject_relation,
        "caveat_name": caveat_name,
        "caveat_context": value.caveat_context or None,
        "expires_at": value.expires_at,
    }


def ensure_historical_relationships(
    apps: Any,
    *,
    relationships: Iterable[RelationshipTuple],
) -> None:
    """Idempotently create exact tuples through historical migration models.

    This deliberately does not use the live REBAC backend: data migrations must
    remain bound to their historical app registry.
    """

    values = tuple(_relationship_facts(value) for value in relationships)
    storage, relationship, resource = _historical_relationship_store(apps)
    rows = relationship._base_manager
    with transaction.atomic():
        for facts in values:
            identity = {
                name: facts[name]
                for name in (
                    "relation",
                    "optional_subject_relation",
                    "caveat_name",
                )
            }
            exact = {
                "caveat_context": facts["caveat_context"],
                "expires_at": facts["expires_at"],
            }
            if storage == "denormalized":
                identity.update(
                    {
                        name: facts[name]
                        for name in (
                            "resource_type",
                            "resource_id",
                            "subject_type",
                            "subject_id",
                        )
                    }
                )
            else:
                assert resource is not None
                resources = resource._base_manager
                resource_row, _ = resources.get_or_create(
                    resource_type=facts["resource_type"],
                    resource_id=facts["resource_id"],
                )
                subject_row, _ = resources.get_or_create(
                    resource_type=facts["subject_type"],
                    resource_id=facts["subject_id"],
                )
                identity.update(
                    {
                        "resource_fk_id": resource_row.pk,
                        "subject_fk_id": subject_row.pk,
                    }
                )
            row, created = rows.get_or_create(**identity, defaults=exact)
            if not created and any(getattr(row, name) != expected for name, expected in exact.items()):
                raise ImproperlyConfigured("An existing historical REBAC relationship has conflicting exact facts.")


def delete_historical_relationships(
    apps: Any,
    *,
    relationships: Iterable[RelationshipTuple],
) -> None:
    """Delete only the exact historical tuples requested by a reverse migration."""

    values = tuple(_relationship_facts(value) for value in relationships)
    storage, relationship, _resource = _historical_relationship_store(apps)
    rows = relationship._base_manager
    with transaction.atomic():
        for facts in values:
            lookup = {
                "relation": facts["relation"],
                "optional_subject_relation": facts["optional_subject_relation"],
                "caveat_name": facts["caveat_name"],
                "expires_at": facts["expires_at"],
            }
            if facts["caveat_context"] is None:
                lookup["caveat_context__isnull"] = True
            else:
                lookup["caveat_context"] = facts["caveat_context"]
            if storage == "denormalized":
                lookup.update(
                    {
                        name: facts[name]
                        for name in (
                            "resource_type",
                            "resource_id",
                            "subject_type",
                            "subject_id",
                        )
                    }
                )
            else:
                lookup.update(
                    {
                        "resource_fk__resource_type": facts["resource_type"],
                        "resource_fk__resource_id": facts["resource_id"],
                        "subject_fk__resource_type": facts["subject_type"],
                        "subject_fk__resource_id": facts["subject_id"],
                    }
                )
            # Historical migration reversals must not dispatch live model
            # signals. The exact-shape gate above proves relationship rows have
            # no parent, M2M, private, or hidden/visible reverse dependents needing
            # Collector; registry resource rows are deliberately retained for
            # other grants.
            rows.filter(**lookup)._raw_delete(router.db_for_write(relationship))


def retarget_historical_resource(
    apps: Any,
    *,
    old: ObjectRef,
    new: ObjectRef,
) -> None:
    """Move one historical resource ID and its exact grants to a canonical ID.

    The registry keeps the old row and its FK grants when the target is absent.
    When both identities exist, only identical grant facts may merge. Migration
    callers supply historical models.
    """

    if not isinstance(old, ObjectRef) or not isinstance(new, ObjectRef):
        raise TypeError("Historical resource retargeting requires ObjectRef values.")
    if old.resource_type != new.resource_type:
        raise ValueError("A historical resource may only change its identity, not its type.")
    if old.resource_id == new.resource_id:
        return

    storage, relationship, resource = _historical_relationship_store(apps)
    rows = relationship._base_manager
    with transaction.atomic():
        if storage == "denormalized":
            if rows.filter(subject_type=old.resource_type, subject_id=old.resource_id).exists():
                raise ImproperlyConfigured("A historical resource used as a subject cannot be retargeted.")
            old_rows = (
                rows.select_for_update()
                .filter(
                    resource_type=old.resource_type,
                    resource_id=old.resource_id,
                )
                .order_by("pk")
            )
            for row in old_rows:
                duplicate = (
                    rows.select_for_update()
                    .filter(
                        resource_type=new.resource_type,
                        resource_id=new.resource_id,
                        relation=row.relation,
                        subject_type=row.subject_type,
                        subject_id=row.subject_id,
                        optional_subject_relation=row.optional_subject_relation,
                        caveat_name=row.caveat_name,
                    )
                    .first()
                )
                if duplicate is not None:
                    if duplicate.caveat_context != row.caveat_context or duplicate.expires_at != row.expires_at:
                        raise ImproperlyConfigured("Historical resource retargeting found conflicting grant facts.")
                    rows.filter(pk=row.pk)._raw_delete(router.db_for_write(relationship))
                else:
                    rows.filter(pk=row.pk).update(resource_id=new.resource_id)
            return

        assert resource is not None
        resources = resource._base_manager
        old_row = (
            resources.select_for_update()
            .filter(
                resource_type=old.resource_type,
                resource_id=old.resource_id,
            )
            .first()
        )
        if old_row is None:
            return
        if rows.filter(subject_fk_id=old_row.pk).exists():
            raise ImproperlyConfigured("A historical resource used as a subject cannot be retargeted.")
        new_row = (
            resources.select_for_update()
            .filter(
                resource_type=new.resource_type,
                resource_id=new.resource_id,
            )
            .first()
        )
        if new_row is None:
            resources.filter(pk=old_row.pk).update(resource_id=new.resource_id)
            return

        for field in ("content_type_id", "object_pk"):
            old_value = getattr(old_row, field)
            new_value = getattr(new_row, field)
            if old_value and new_value and old_value != new_value:
                raise ImproperlyConfigured("Historical resource retargeting found conflicting backing facts.")
            if old_value and not new_value:
                resources.filter(pk=new_row.pk).update(**{field: old_value})

        old_rows = rows.select_for_update().filter(resource_fk_id=old_row.pk).order_by("pk")
        for row in old_rows:
            duplicate = (
                rows.select_for_update()
                .filter(
                    resource_fk_id=new_row.pk,
                    relation=row.relation,
                    subject_fk_id=row.subject_fk_id,
                    optional_subject_relation=row.optional_subject_relation,
                    caveat_name=row.caveat_name,
                )
                .first()
            )
            if duplicate is not None:
                if duplicate.caveat_context != row.caveat_context or duplicate.expires_at != row.expires_at:
                    raise ImproperlyConfigured("Historical resource retargeting found conflicting grant facts.")
                rows.filter(pk=row.pk)._raw_delete(router.db_for_write(relationship))
            else:
                rows.filter(pk=row.pk).update(resource_fk_id=new_row.pk)
        resources.filter(pk=old_row.pk)._raw_delete(router.db_for_write(resource))
