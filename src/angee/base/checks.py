"""Django system checks for Angee's runtime contracts."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from django.apps import AppConfig, apps
from django.conf import settings
from django.core import checks
from rebac.resources import model_resource_type
from rebac.schema import FieldBinding, PermExpr, Schema
from rebac.schema.parser import parse_zed

from angee.base.models.ownership import OwnerMixin
from angee.base.permissions import declared_rebac_definition, declared_rebac_schema, declared_schema_path


def check_database_routers(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Refuse a database router: Angee runs on one database.

    Owners open bare transactions and commit hooks on ``default``; a router
    would place rows outside them, or permission reads on a replica.
    """

    del app_configs, kwargs
    if not settings.DATABASE_ROUTERS:
        return []
    return [
        checks.Error(
            f"settings.DATABASE_ROUTERS must be empty; found {settings.DATABASE_ROUTERS!r}.",
            hint="Angee runs on one database, Django's default; remove the router.",
            id="angee.E050",
        )
    ]


def check_rebac_caveats(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Keep framework read scopes independent of request caveat context."""

    del kwargs
    configs = apps.get_app_configs() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(configs, key=lambda config: config.name):
        schema = declared_rebac_schema(config)
        if schema is None:
            continue
        for definition in sorted(schema.definitions, key=lambda definition: definition.resource_type):
            for relation in sorted(definition.relations, key=lambda relation: relation.name):
                if any(subject.with_caveat for subject in relation.allowed_subjects):
                    errors.append(
                        checks.Error(
                            f"{config.name}: {definition.resource_type}#{relation.name} declares a caveated subject.",
                            hint="Use live field-backed relations; actor-scoped querysets have no caveat context.",
                            obj=config,
                            id="angee.E024",
                        )
                    )
    return errors


def _sealed_expressions(schema: Schema, seals: Mapping[str, Collection[str]]) -> dict[str, PermExpr]:
    return {
        f"{resource_type}#{permission.name}": permission.expression
        for resource_type, names in seals.items()
        if (definition := schema.get_definition(resource_type)) is not None
        for permission in definition.permissions
        if permission.name in names
    }


def check_sealed_permissions(
    app_label: str,
    seals: Mapping[str, Collection[str]],
    check_id: str,
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Keep the permissions an owner seals exactly as its own declaration states them.

    ``seals`` maps resource types of ``app_label``'s ``permissions.zed`` to the
    permissions no fragment extends. A composed schema whose sealed expression
    differs from the declaration's was changed by a fragment, which then reaches
    that permission alone: fragments widen the permissions a sealed expression
    composes, or grant their need through their own records' reads. An owner
    without its declaration, or a seal on a permission the declaration does not
    state, is reported as well; the check reports and never raises. Owners
    register it once, bound with ``functools.partial``, under the security tag.
    """

    del kwargs
    try:
        owner: AppConfig | None = apps.get_app_config(app_label)
    except LookupError:
        owner = None
    declaration = declared_schema_path(owner) if owner is not None else None
    sealed = sorted(f"{resource_type}#{name}" for resource_type, names in seals.items() for name in names)
    if owner is None or declaration is None:
        return [checks.Error(
            f"{app_label} declares no permissions schema, so {', '.join(sealed)} have no declaration to compare.",
            hint=f"Restore {app_label}'s permissions.zed.",
            obj=owner,
            id=check_id,
        )]
    declared = _sealed_expressions(parse_zed(declaration.read_text(encoding="utf-8")), seals)
    if undeclared := [name for name in sealed if name not in declared]:
        return [checks.Error(
            f"{owner.name} seals {', '.join(undeclared)}, which its permissions.zed does not declare.",
            hint="Seal only permissions the owner's own declaration states.",
            obj=owner,
            id=check_id,
        )]
    configs = apps.get_app_configs() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(configs, key=lambda config: config.name):
        schema = declared_rebac_schema(config)
        if schema is None:
            continue
        changed = sorted(
            name for name, expression in _sealed_expressions(schema, seals).items() if expression != declared[name]
        )
        if changed:
            extended_by = schema.headers.get("rebac_extended_by", "")
            errors.append(
                checks.Error(
                    f"{config.name}: {', '.join(changed)} differ from {owner.name}'s declaration "
                    f"(extended by {extended_by or 'no fragment'}).",
                    hint="Union the fragment's arm into an extension point the sealed expression composes, "
                    "or grant the need through the fragment's own record's read.",
                    obj=config,
                    id=check_id,
                )
            )
    return errors


def check_ownership(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Reject models using the reserved owner relation without its model capability."""

    del kwargs
    models = (
        apps.get_models() if app_configs is None else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        definition = declared_rebac_definition(model)
        owner_relation = (
            next((relation for relation in definition.relations if relation.name == "owner"), None)
            if definition is not None
            else None
        )
        if owner_relation is not None and (
            not issubclass(model, OwnerMixin) or owner_relation.backing != FieldBinding(path="owner")
        ):
            errors.append(
                checks.Error(
                    f"{model._meta.label}: definition {model_resource_type(model)!r} names a relation owner "
                    "that is not the OwnerMixin owner column.",
                    hint="Compose OwnerMixin and back owner with rebac:field=owner. Name attribution used as "
                    "access creator (rebac:field=created_by) and a domain role by its own word.",
                    obj=model,
                    id="angee.E045",
                )
            )
    return errors
