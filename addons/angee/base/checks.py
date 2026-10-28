"""Django system checks for Angee's runtime contracts."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from rebac.resources import model_resource_type
from rebac.schema import FieldBinding

from angee.base.models.ownership import OwnerMixin
from angee.base.permissions import effective_rebac_definition, effective_rebac_schema


def check_rebac_caveats(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Keep framework read scopes independent of request caveat context."""

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
                            hint="Use live field-backed relations; actor-scoped querysets have no caveat context.",
                            obj=config,
                            id="angee.E024",
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
        definition = effective_rebac_definition(model)
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
