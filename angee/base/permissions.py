"""REBAC declarations and authorization boundaries shared by framework layers."""

from __future__ import annotations

from functools import lru_cache

from django.apps import AppConfig, apps
from django.db import models
from rebac.resources import model_resource_type
from rebac.schema import Definition, Schema, resolve_schema_path
from rebac.schema.parser import parse_zed


def effective_rebac_definition(model: type[models.Model]) -> Definition | None:
    """Return ``model``'s effective Zed definition parsed from its disk source.

    The composer points an extended definition's owning ``AppConfig.rebac_schema``
    at the emitted base-plus-extensions source.  Resolving that declaration rather
    than assuming ``permissions.zed`` therefore covers both ordinary addons and
    composed ``permissions.extends.zed`` contributions without consulting the
    runtime backend or its database-backed schema.
    """

    resource_type = model_resource_type(model)
    if not resource_type:
        return None
    try:
        app_config = apps.get_app_config(model._meta.app_label)
    except LookupError:
        return None
    schema = effective_rebac_schema(app_config)
    return schema.get_definition(resource_type) if schema is not None else None


def effective_rebac_schema(app_config: AppConfig) -> Schema | None:
    """Read the effective app schema through the content-keyed parser cache."""

    schema_path = resolve_schema_path(app_config)
    return _parse_schema(schema_path.read_text(encoding="utf-8")) if schema_path is not None else None


@lru_cache(maxsize=128)
def _parse_schema(source: str) -> Schema:
    """Cache parsed content, so rewritten effective sources cannot leave stale gates."""

    return parse_zed(source)
