"""Django system checks for IAM's account authority."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from rebac.schema.introspection import permission_sources

from angee.base.permissions import effective_rebac_schema

_PREVIEW_ADMIN_ARM = "admin->member"


def check_view_as_admin_only(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Keep account preview a platform-admin action in the composed schema.

    ``auth/user#view_as`` is IAM's ``admin->member``. A fragment that unions
    any other arm into it would let that relation act as the account; each
    addon grants its display need through its own record's read instead.
    """

    del kwargs
    configs = apps.get_app_configs() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(configs, key=lambda config: config.name):
        schema = effective_rebac_schema(config)
        if schema is None or schema.get_definition("auth/user") is None:
            continue
        sources = permission_sources(schema, "auth/user", "view_as")
        arms = {
            *sources.direct_relations, *sources.builtins, *sources.subpermissions,
            *(f"{via}->{target}" for via, target in sources.arrows),
        } - {_PREVIEW_ADMIN_ARM}
        if arms:
            extended_by = schema.headers.get("rebac_extended_by", "")
            errors.append(
                checks.Error(
                    f"{config.name}: auth/user#view_as reaches {', '.join(sorted(arms))} beyond "
                    f"{_PREVIEW_ADMIN_ARM} (extended by {extended_by or 'no fragment'}).",
                    hint="Account preview is granted to platform admins only; remove the fragment's view_as arm "
                    "and grant the display need through the owning record's read.",
                    obj=config,
                    id="angee.iam.E001",
                )
            )
    return errors
