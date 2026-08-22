"""Disk-resolved REBAC declarations shared by runtime framework layers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django.apps import apps
from django.db import models
from rebac.resources import model_resource_type
from rebac.schema.ast import Definition, Schema
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
    schema_setting = getattr(app_config, "rebac_schema", "permissions.zed")
    schema_path = Path(app_config.path) / schema_setting
    if not schema_path.is_file():
        return None
    return _parse_schema(schema_path).get_definition(resource_type)


@lru_cache(maxsize=None)
def _parse_schema(path: Path) -> Schema:
    """Parse one immutable-for-the-process effective Zed source."""

    return parse_zed(path.read_text(encoding="utf-8"))
