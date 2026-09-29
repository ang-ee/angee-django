"""Django system checks for Angee's runtime persistence contracts."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.db import DEFAULT_DB_ALIAS, router
from django.db import models as django_models
from rebac.models import RebacResource, Relationship, RelationshipRegistry
from rebac.resources import model_resource_type
from rebac.schema import AllowedSubject, FieldBinding, PermArrow, PermRef

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
        owner_model = model._meta.get_field("owner").model
        if owner_model is model and model.owner_container is not None:
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
        if owner_model is model and (definition is None or (
            not any(permission.name == model.owner_transfer_permission for permission in definition.permissions)
            or not any(
                relation.name == "owner" and relation.backing == FieldBinding(path="owner")
                for relation in definition.relations
            )
        )):
            errors.append(
                checks.Error(
                    f"{model._meta.label} must declare an owner relation backed by owner "
                    f"and permission {model.owner_transfer_permission!r}.",
                    hint="Declare ownership and transfer on the grant root's effective permissions.zed.",
                    obj=model,
                    id="angee.E022",
                )
            )
        transfer = owner_model.owner_transfer_permission
        expected_gates: tuple[PermRef | PermArrow, ...] = (PermRef(transfer),)
        expected_expression = transfer
        if owner_model is not model:
            parent_path = "__".join(
                path.join_field.name for path in model._meta.get_path_to_parent(owner_model)
            )
            parent_type = model_resource_type(owner_model)
            parent_gates = tuple(
                PermArrow(relation.name, transfer)
                for relation in definition.relations if (
                    relation.backing == FieldBinding(path=parent_path)
                    and relation.allowed_subjects == (AllowedSubject(type=parent_type or ""),)
                )
            ) if definition is not None else ()
            expected_gates = parent_gates
            expected_expression = " or ".join(f"{gate.via}->{gate.target}" for gate in parent_gates) or (
                f"<relation to {parent_type} backed by {parent_path}>->{transfer}"
            )
        if definition is None or not any(
            permission.name == "write__owner" and permission.expression in expected_gates
            for permission in definition.permissions
        ):
            errors.append(
                checks.Error(
                    f"{model._meta.label}: definition {model_resource_type(model)!r} must declare "
                    f"write__owner = {expected_expression}.",
                    hint="Gate owner writes on the owning model's transfer permission; "
                    "multi-table children delegate through their field-backed parent relation.",
                    obj=model,
                    id="angee.E027",
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
