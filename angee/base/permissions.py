"""REBAC declarations and authorization boundaries shared by framework layers."""

from __future__ import annotations

from functools import lru_cache

from django.apps import AppConfig, apps
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils.text import capfirst
from rebac.resources import model_for_resource_type, model_resource_type
from rebac.schema import Definition, FieldBinding, PermArrow, PermBinOp, PermRef, Schema, resolve_schema_path
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


def permission_subject_types(definition: Definition, permission: str) -> set[str]:
    """Return the resource types whose holders ``permission`` on ``definition`` can reach.

    An arrow reaches its relation's subject types, a relation named directly its
    own, and a permission of the same definition is followed; union and
    intersection operands count, an exclusion's subtracted operand does not.
    """

    relations = {relation.name: relation for relation in definition.relations}
    permissions = {item.name: item for item in definition.permissions}
    types: set[str] = set()
    followed: set[str] = set()
    pending = [PermRef(permission)]
    while pending:
        expression = pending.pop()
        if isinstance(expression, PermBinOp):
            pending.append(expression.left)
            if expression.op != "-":
                pending.append(expression.right)
        elif isinstance(expression, PermArrow) and expression.via in relations:
            types.update(subject.type for subject in relations[expression.via].allowed_subjects)
        elif isinstance(expression, PermRef):
            if expression.name in relations:
                types.update(subject.type for subject in relations[expression.name].allowed_subjects)
            elif expression.name in permissions and expression.name not in followed:
                followed.add(expression.name)
                pending.append(permissions[expression.name].expression)
    return types


def effective_rebac_schema(app_config: AppConfig) -> Schema | None:
    """Read the effective app schema through the content-keyed parser cache."""

    schema_path = resolve_schema_path(app_config)
    return _parse_schema(schema_path.read_text(encoding="utf-8")) if schema_path is not None else None


def rebac_relation_label(resource_type: str, name: str) -> str:
    """Return a declared field label or a readable REBAC relation name."""

    model = model_for_resource_type(resource_type)
    if model is not None:
        definition = effective_rebac_definition(model)
        relation = next((item for item in definition.relations if item.name == name), None) if definition else None
        if relation is not None and isinstance(relation.backing, FieldBinding) and "__" not in relation.backing.path:
            try:
                field = model._meta.get_field(relation.backing.path)
            except FieldDoesNotExist:
                pass
            else:
                return capfirst(str(field.verbose_name))
    return capfirst(name.replace("_", " "))


@lru_cache(maxsize=128)
def _parse_schema(source: str) -> Schema:
    """Cache parsed content, so rewritten effective sources cannot leave stale gates."""

    return parse_zed(source)
