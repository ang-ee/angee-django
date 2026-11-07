"""Django system checks for messaging's composed read permissions."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from rebac.schema import PermExpr, Schema
from rebac.schema.parser import parse_zed

from angee.base.permissions import declared_schema_path, effective_rebac_schema

DERIVED_READS = {"messaging/thread": ("read", "read_inbox"), "messaging/message": ("read", "read_inbox")}
"""The reads messaging derives from its extension points, which fragments never extend."""


def _derived_reads(schema: Schema) -> dict[str, PermExpr]:
    return {
        f"{resource_type}#{permission.name}": permission.expression
        for resource_type, names in DERIVED_READS.items()
        if (definition := schema.get_definition(resource_type)) is not None
        for permission in definition.permissions
        if permission.name in names
    }


def check_derived_reads(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Keep thread and message ``read`` and ``read_inbox`` exactly as messaging declares them.

    Fragments widen ``messaging/thread#read_granted`` with grants that hold for any
    conversation, which both reads take, and ``messaging/thread#chatter_read`` with a
    record's read of its own chatter, which only ``read`` takes; message grants go to
    ``messaging/message#read_granted``. An arm unioned straight into a derived read
    reaches one of the two reads and not the other: the inbox would drop a grant
    generic reads keep, or keep one they refuse. A messaging without its own schema
    declaration is a broken installation, refused before any read is compared.
    """

    del kwargs
    messaging = apps.get_containing_app_config(__name__)
    declaration = declared_schema_path(messaging) if messaging is not None else None
    if declaration is None:
        raise ImproperlyConfigured(
            "angee.messaging declares no permissions schema, so its thread and message reads have no "
            "declaration to compare; restore messaging's permissions.zed.",
        )
    declared = _derived_reads(parse_zed(declaration.read_text(encoding="utf-8")))
    configs = apps.get_app_configs() if app_configs is None else app_configs
    errors: list[checks.CheckMessage] = []
    for config in sorted(configs, key=lambda config: config.name):
        schema = effective_rebac_schema(config)
        if schema is None:
            continue
        extended = sorted(
            name for name, expression in _derived_reads(schema).items() if expression != declared.get(name)
        )
        if extended:
            extended_by = schema.headers.get("rebac_extended_by", "")
            errors.append(
                checks.Error(
                    f"{config.name}: {', '.join(extended)} differ from messaging's declaration "
                    f"(extended by {extended_by or 'no fragment'}).",
                    hint="Union conversation grants into messaging/thread#read_granted or "
                    "messaging/message#read_granted, and a record's chatter read into "
                    "messaging/thread#chatter_read; messaging derives read and read_inbox from them.",
                    obj=config,
                    id="messaging.E003",
                )
            )
    return errors
