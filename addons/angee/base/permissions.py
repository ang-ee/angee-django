"""REBAC declarations and authorization boundaries shared by framework layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from django.apps import AppConfig, apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from rebac import SubjectRef, check_new, current_actor, proposed_forward_relationships, to_subject_ref
from rebac import user_subject_type as _user_subject_type
from rebac.backends import backend
from rebac.managers import RebacQuerySet
from rebac.resources import model_resource_type
from rebac.schema import Definition, Schema, resolve_schema_path
from rebac.schema.introspection import relation_dependencies
from rebac.schema.parser import parse_zed


def declared_grants(model: type[models.Model]) -> dict[str, str]:
    """Validate the record-share relations declared on this model itself."""

    raw = model.__dict__.get("rebac_grantable", {})
    if not isinstance(raw, Mapping):
        raise ImproperlyConfigured(f"{model._meta.label}.rebac_grantable must be a mapping.")
    declaration: dict[str, str] = {}
    for relation, permission in raw.items():
        if not isinstance(relation, str) or not relation:
            raise ImproperlyConfigured(
                f"{model._meta.label}.rebac_grantable relation names must be non-empty strings."
            )
        if not isinstance(permission, str) or not permission:
            raise ImproperlyConfigured(f"{model._meta.label}.rebac_grantable[{relation!r}] must name a permission.")
        declaration[relation] = permission
    return declaration


def require_rebac_queryset[M: models.Model](model: type[M]) -> RebacQuerySet[M]:
    """Preserve the default manager's filters and require its native REBAC query contract."""

    queryset = model._default_manager.get_queryset()
    if not isinstance(queryset, RebacQuerySet):
        raise ImproperlyConfigured(f"{model._meta.label} requires a RebacQuerySet default manager.")
    return queryset


@dataclass(frozen=True, slots=True)
class _RebacSchemaBinding:
    source: Path | None
    declaration: str | PathLike[str] | None
    effective: str | PathLike[str] | None


_SCHEMA_BINDING = "_angee_rebac_schema_binding"


def declared_schema_path(app_config: AppConfig) -> Path | None:
    """Retain the native authored declaration before binding generated output."""

    binding = app_config.__dict__.get(_SCHEMA_BINDING)
    effective = binding.effective if isinstance(binding, _RebacSchemaBinding) else None
    if not isinstance(binding, _RebacSchemaBinding) or getattr(app_config, "rebac_schema", None) != effective:
        binding = _RebacSchemaBinding(
            resolve_schema_path(app_config), getattr(app_config, "rebac_schema", None), effective,
        )
        app_config.__dict__[_SCHEMA_BINDING] = binding
    return binding.source


def bind_rebac_schema(app_config: AppConfig, path: Path | None) -> None:
    """Bind generated input; ``None`` restores the original native declaration."""

    source = declared_schema_path(app_config)
    binding = app_config.__dict__[_SCHEMA_BINDING]
    assert isinstance(binding, _RebacSchemaBinding)
    effective = path if path is not None else binding.declaration
    setattr(app_config, "rebac_schema", effective)
    app_config.__dict__[_SCHEMA_BINDING] = _RebacSchemaBinding(source, binding.declaration, effective)


def merged_schema_relpath(package: str) -> Path:
    """Return the permission artifact address relative to compiler output."""

    return Path('permissions') / f'{package}.zed'


def effective_rebac_definition(model: type[models.Model]) -> Definition | None:
    """Read request policy through the native synced, scope-pinned backend."""

    resource_type = model_resource_type(model)
    return backend().schema().get_definition(resource_type) if resource_type else None


def effective_rebac_schema(app_config: AppConfig) -> Schema | None:
    """Read the bound compose snapshot for startup and declaration checks only."""

    schema_path = resolve_schema_path(app_config)
    return parse_zed(schema_path.read_text(encoding='utf-8')) if schema_path is not None else None


def declared_rebac_definition(model: type[models.Model]) -> Definition | None:
    """Read one model declaration without consulting database-backed policy."""

    resource_type = model_resource_type(model)
    if not resource_type:
        return None
    try:
        config = apps.get_app_config(model._meta.app_label)
    except LookupError:
        return None
    schema = effective_rebac_schema(config)
    return schema.get_definition(resource_type) if schema is not None else None


def instance_actor(instance: models.Model) -> SubjectRef | None:
    """Return the instance's pinned actor, falling back to the ambient actor.

    Instance verbs and audit defaults share this resolution, including during
    elevated writes. Plain Django models have only the ambient actor.
    """

    # Schema declarations also load before Django setup; the native mixin is model-defined.
    from rebac import RebacMixin

    actor = instance.actor() if isinstance(instance, RebacMixin) else None
    return actor if actor is not None else current_actor()


def actor_user_id(actor: Any) -> Any | None:
    """Return ``actor``'s user primary key, or ``None`` when no user backs it.

    Model-backed REBAC subjects use the database primary key, so the canonical
    actor id is already the value required by user foreign-key columns.
    Non-user subjects return ``None``.
    """

    if actor is None or not actor.subject_id:
        return None
    if actor.subject_type != _user_subject_type():
        return None
    pk = get_user_model()._meta.pk
    if pk is None:
        return None
    try:
        return pk.to_python(actor.subject_id)
    except (TypeError, ValueError, ValidationError):
        return None


def subject_reaches_user(subject: models.Model | SubjectRef, user_id: Any) -> bool:
    """Return whether ``subject`` resolves to, or contains, the user with ``user_id``.

    A subject reaches a user when it is that user's own reference, a wildcard,
    or a userset such as ``auth/group:7#member`` whose relation the user holds on
    the referenced object. A record invariant that keeps one user out of its
    holders composes this before a grant, an assignment, or an admission and
    raises :class:`angee.base.exceptions.RecordAccessSubjectRefused` on ``True``.
    ``user_id`` of ``None`` names nobody and is never reached.
    """

    if user_id is None:
        return False
    reference = to_subject_ref(subject)
    if reference.subject_id == "*":
        return True
    user = SubjectRef.of(_user_subject_type(), str(user_id))
    if reference == user:
        return True
    if not reference.optional_relation:
        return False

    return backend().check_access(subject=user, action=reference.optional_relation, resource=reference.object).allowed


@dataclass(frozen=True, slots=True)
class DirectRecordAccess:
    """One direct declared-relation tuple on a shareable record."""

    relation: str
    subject: SubjectRef


@dataclass(frozen=True, slots=True)
class CandidateRelationships:
    """The relations a row would carry for one action, as rebac's candidate preflight reads them.

    This adapter composes rebac's public field-backed candidate projection.
    A caller asks about a row that is not, or not yet, the stored one (an
    unsaved task's assignee, a retained history snapshot) and checks a subject
    against the relations its fields and ledger would give it.
    """

    resource_type: str
    definition: Definition
    required: frozenset[str]
    relationships: dict[str, tuple[SubjectRef, ...] | None]

    def allows(self, subject: Any, action: str) -> bool:
        """Whether ``subject`` may perform ``action`` on the candidate these relations describe."""

        return check_new(
            subject=to_subject_ref(subject), action=action, resource_type=self.resource_type,
            relationships=self.relationships,
        ).allowed


def candidate_relationships(instance: models.Model, action: str) -> CandidateRelationships | None:
    """Read the relations ``instance`` would carry for ``action``; ``None`` when its type has no definition."""

    resource_type = model_resource_type(type(instance))
    if resource_type is None:
        return None

    schema = backend().schema()
    definition = schema.get_definition(resource_type)
    if definition is None:
        return None
    required = frozenset(relation_dependencies(schema, resource_type, action))
    relationships = proposed_forward_relationships(
        instance,
        definition,
        required_relations=required,
        using=instance._state.db,
    )
    return CandidateRelationships(resource_type, definition, required, relationships)
