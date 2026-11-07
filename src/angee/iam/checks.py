"""Django system checks for IAM's account authority."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from rebac.schema import Schema
from rebac.schema.introspection import permission_sources
from rebac.schema.parser import parse_zed

from angee.base.permissions import declared_schema_path, effective_rebac_schema


def _view_as_arms(schema: Schema) -> set[str]:
    sources = permission_sources(schema, "auth/user", "view_as")
    return {
        *sources.direct_relations, *sources.builtins, *sources.subpermissions,
        *(f"{via}->{target}" for via, target in sources.arrows),
    }


def check_view_as_admin_only(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Keep account preview a platform-admin action in the composed schema.

    ``auth/user#view_as`` reaches exactly the arms of IAM's own unextended
    declaration (``admin->member``). A fragment that unions any other arm into it
    would let that relation act as the account; each addon grants its display
    need through its own record's read instead. An IAM without its own schema
    declaration is a broken installation, refused before any arm is compared.
    """

    del kwargs
    iam = apps.get_containing_app_config(__name__)
    declaration = declared_schema_path(iam) if iam is not None else None
    if declaration is None:
        raise ImproperlyConfigured(
            "angee.iam declares no permissions schema, so auth/user#view_as has no arms to compare; "
            "restore IAM's permissions.zed.",
        )
    allowed = _view_as_arms(parse_zed(declaration.read_text(encoding="utf-8")))
    configs = apps.get_app_configs() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(configs, key=lambda config: config.name):
        schema = effective_rebac_schema(config)
        if schema is None or schema.get_definition("auth/user") is None:
            continue
        arms = _view_as_arms(schema) - allowed
        if arms:
            extended_by = schema.headers.get("rebac_extended_by", "")
            errors.append(
                checks.Error(
                    f"{config.name}: auth/user#view_as reaches {', '.join(sorted(arms))} beyond "
                    f"{', '.join(sorted(allowed))} (extended by {extended_by or 'no fragment'}).",
                    hint="Account preview is granted to platform admins only; remove the fragment's view_as arm "
                    "and grant the display need through the owning record's read.",
                    obj=config,
                    id="angee.iam.E001",
                )
            )
    return errors
