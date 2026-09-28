"""Django system checks for Angee's runtime persistence contracts."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.db import DEFAULT_DB_ALIAS, router
from django.db import models as django_models
from rebac.backends.local import LocalBackend
from rebac.backends.local_query import LocalQueryScope, UnsupportedScope
from rebac.models import RebacResource, Relationship, RelationshipRegistry
from rebac.resources import model_resource_type
from rebac.schema import FieldBinding, PermRef, Schema, permission_sources
from rebac.types import SubjectRef

from angee.base.mixins import CreationKeyMixin, HierarchyQuerySet, ItemOwnershipMixin, OwnerMixin
from angee.base.permissions import effective_rebac_definition, effective_rebac_schema


def check_rebac_caveats(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Reject caveated subjects in effective schemas before SQL scoping falls back to enumeration."""

    del kwargs
    configs = apps.get_app_configs() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(configs, key=lambda config: config.name):
        schema = effective_rebac_schema(config)
        if schema is None:
            continue
        for definition in sorted(schema.definitions, key=lambda definition: definition.resource_type):
            for relation in sorted(definition.relations, key=lambda relation: relation.name):
                if any(subject.with_caveat for subject in relation.allowed_subjects):
                    errors.append(
                        checks.Error(
                            f"{config.name}: {definition.resource_type}#{relation.name} declares a caveated subject.",
                            hint="Use live field-backed relations; caveats cannot be compiled into SQL read scopes.",
                            obj=config,
                            id="angee.E024",
                        )
                    )
    return errors


def _uses_authenticated(
    schema: Schema,
    resource_type: str,
    action: str,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> bool:
    """Follow native dependency facts across arrows and subject sets, including cycles."""

    key = (resource_type, action)
    definition = schema.get_definition(resource_type)
    if key in seen or definition is None:
        return False
    sources = permission_sources(schema, resource_type, action)
    if "authenticated" in sources.builtins:
        return True
    relations = {relation.name: relation for relation in definition.relations}
    targets = {
        (subject.type, target)
        for via, target in sources.arrows
        if (relation := relations.get(via)) is not None
        for subject in relation.allowed_subjects
    } | {
        (subject.type, subject.relation)
        for name in sources.direct_relations
        for subject in relations[name].allowed_subjects
        if subject.relation
    }
    return any(_uses_authenticated(schema, target, permission, seen | {key}) for target, permission in sorted(targets))


def check_authenticated_scopes(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require SQL scopes wherever authenticated membership makes tuple enumeration incomplete."""

    del kwargs
    configs = {config.name: config for config in apps.get_app_configs()}
    if app_configs is not None:
        configs.update((config.name, config) for config in app_configs)
    schemas = [
        schema for _, config in sorted(configs.items())
        if (schema := effective_rebac_schema(config)) is not None
    ]
    schema = Schema(
        definitions=[definition for source in schemas for definition in source.definitions],
        caveats=[caveat for source in schemas for caveat in source.caveats],
    )
    affected = {
        (definition.resource_type, permission.name)
        for definition in schema.definitions
        for permission in definition.permissions
        if _uses_authenticated(schema, definition.resource_type, permission.name)
    }
    if not affected:
        return []
    backend = LocalBackend()
    backend.set_schema(schema)
    selected = configs.values() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(selected, key=lambda config: config.name):
        for model in sorted(config.get_models(), key=lambda model: model._meta.label_lower):
            resource_type = model_resource_type(model)
            for target, action in sorted(affected):
                if target != resource_type:
                    continue
                scope = LocalQueryScope(backend, SubjectRef.of("auth/user", "1"), router.db_for_read(model))
                try:
                    scope.predicate(model, action, resource_type)
                except UnsupportedScope:
                    errors.append(checks.Error(
                        f"{resource_type}#{action} reaches authenticated but cannot compile to a SQL scope.",
                        hint="Remove recursive role arms and other unsupported scope constructs; "
                        "tuple enumeration cannot represent authenticated membership.",
                        obj=model,
                        id="angee.E026",
                    ))
    return errors


def check_ownership(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require grant roots to back ownership and gate owner writes on transfer."""

    del kwargs
    models = (
        apps.get_models()
        if app_configs is None
        else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        if not issubclass(model, OwnerMixin):
            continue
        if model._meta.get_field("owner").model is not model:
            continue
        if model.owner_container is not None:
            try:
                field = model._meta.get_field(model.owner_container)
            except FieldDoesNotExist:
                field = None
            if (
                not isinstance(field, django_models.ForeignKey)
                or not isinstance(field.related_model, type)
                or not issubclass(field.related_model, ItemOwnershipMixin)
            ):
                errors.append(
                    checks.Error(
                        f"{model._meta.label}.owner_container must name a foreign key to an ItemOwnershipMixin model.",
                        obj=model,
                        id="angee.E025",
                    )
                )
        definition = effective_rebac_definition(model)
        if definition is None or (
            not any(permission.name == model.owner_transfer_permission for permission in definition.permissions)
            or not any(
                relation.name == "owner" and relation.backing == FieldBinding(path="owner")
                for relation in definition.relations
            )
            or not any(
                permission.name == "write__owner"
                and permission.expression == PermRef(model.owner_transfer_permission)
                for permission in definition.permissions
            )
        ):
            errors.append(
                checks.Error(
                    f"{model._meta.label} must declare an owner relation backed by owner "
                    f"and permission {model.owner_transfer_permission!r}, "
                    f"with write__owner = {model.owner_transfer_permission}.",
                    hint="Declare ownership, transfer and its owner field gate on the grant root's "
                    "effective permissions.zed.",
                    obj=model,
                    id="angee.E022",
                )
            )
    return errors


def check_creation_key_constraints(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require every creation-key adopter to declare its scope's unique constraint."""

    del kwargs
    models = (
        apps.get_models()
        if app_configs is None
        else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        if not issubclass(model, CreationKeyMixin):
            continue
        if not any(
            constraint == model.creation_key_constraint(name=constraint.name)
            for constraint in model._meta.constraints
        ):
            errors.append(
                checks.Error(
                    f"{model._meta.label} must declare its creation-key uniqueness constraint.",
                    hint="Include CreationKeyMixin.creation_key_constraint() with the declared creation_key_scope.",
                    obj=model,
                    id="angee.E023",
                )
            )
    return errors


def check_hierarchy_queryset_order(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Reject queryset ordering that skips another guard during path rewrites."""

    del kwargs
    models = (
        apps.get_models()
        if app_configs is None
        else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        for manager in model._meta.managers:
            queryset_type = type(manager.get_queryset())
            if not issubclass(queryset_type, HierarchyQuerySet):
                continue
            preceding = queryset_type.__mro__[:queryset_type.__mro__.index(HierarchyQuerySet)]
            if any(not issubclass(owner, HierarchyQuerySet) or "update" in owner.__dict__ for owner in preceding):
                errors.append(
                    checks.Error(
                        f"{model._meta.label}.{manager.name} must compose HierarchyQuerySet "
                        "before every other queryset guard.",
                        hint="Put HierarchyQuerySet first and define update guards in a following queryset base.",
                        obj=model,
                        id="angee.E021",
                    )
                )
    return errors


def check_rebac_database(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require REBAC-managed models to use the default write database."""

    del kwargs
    models = (
        apps.get_models()
        if app_configs is None
        else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        if model not in (Relationship, RelationshipRegistry, RebacResource) and not model_resource_type(model):
            continue
        alias = router.db_for_write(model)
        if alias != DEFAULT_DB_ALIAS:
            errors.append(
                checks.Error(
                    f"REBAC-managed model {model._meta.label} routes writes to {alias!r}; "
                    "REBAC requires the default database.",
                    hint="Update DATABASE_ROUTERS so REBAC-managed models write to 'default'.",
                    obj=model,
                    id="angee.E020",
                )
            )
    return errors
