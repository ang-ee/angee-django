"""Disk-resolved REBAC declarations shared by runtime framework layers."""

from __future__ import annotations

from functools import lru_cache

from django.apps import apps
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
    schema_path = resolve_schema_path(app_config)
    if schema_path is None:
        return None
    return _parse_schema(schema_path.read_text(encoding="utf-8")).get_definition(resource_type)


@lru_cache(maxsize=128)
def _parse_schema(source: str) -> Schema:
    """Cache parsed content, so rewritten effective sources cannot leave stale gates."""

    return parse_zed(source)
