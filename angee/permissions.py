"""REBAC declarations and authorization boundaries shared by framework layers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.apps import AppConfig, apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import DatabaseError, models
from django.utils.text import capfirst
from rebac import system_context
from rebac.managers import RebacQuerySet
from rebac.resources import model_for_resource_type, model_resource_type
from rebac.schema import Definition, FieldBinding, Schema, resolve_schema_path, validate_schema
from rebac.schema.parser import parse_zed


def require_rebac_queryset[M: models.Model](model: type[M]) -> RebacQuerySet[M]:
    """Preserve the default manager's filters and require its native REBAC query contract."""

    queryset = model._default_manager.get_queryset()
    if not isinstance(queryset, RebacQuerySet):
        raise ImproperlyConfigured(f"{model._meta.label} requires a RebacQuerySet default manager.")
    return queryset


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


# A managed record's ``external_id`` is ``<kind>:<name>``; only schema rows are ours
# to prune (the library also manages relationship rows under other prefixes).
_SCHEMA_PREFIXES = ("caveat:", "definition:", "relation:", "permission:")
# Delete a definition after the permissions/caveats that reference it, mirroring the
# library's ``_stale_record_prune_order`` so foreign keys resolve cleanly.
_PRUNE_ORDER = {"relation": 0, "permission": 0, "definition": 1, "caveat": 1}


class PermissionSchemaReconcileError(RuntimeError):
    """Raised when current REBAC schema declarations cannot be reconciled safely."""


def reconcile_permission_schema() -> int:
    """Prune stale package-managed ``Schema*`` rows.

    Idempotent and best-effort: returns the number of stale managed rows pruned,
    and is a no-op on a fresh/unmigrated database (no ``Schema*`` tables yet). Runs
    under ``system_context`` in the library's policy-write transaction, which
    serializes schema changes and validates the resulting policy once.
    """

    # This module is imported while model classes are being declared.
    from rebac import schema_changes
    from rebac.models import PackageManagedRecord

    composed = {app_config.name for app_config in apps.get_app_configs()}
    current_external_ids = _current_schema_external_ids_by_package()
    try:
        stale = [
            record
            for record in PackageManagedRecord.objects.all()
            if _is_stale_schema_record(record, composed, current_external_ids)
        ]
    except DatabaseError:  # rebac tables not migrated yet (fresh database)
        return 0
    if not stale:
        return 0

    with system_context(reason="angee.reconcile_permission_schema"), schema_changes():
        for record in sorted(stale, key=_prune_key):
            target = record.target
            if target is not None:
                target.delete()
            record.delete()
        _delete_stale_relationships(stale)
    return len(stale)


def _current_schema_external_ids_by_package() -> dict[str, set[str]]:
    """Return the schema rows currently declared by each composed package.

    Mirrors ``django-zed-rebac``'s sync command enough to know which managed rows are
    still source-owned. Parse and validate every current declaration before deleting
    anything; an invalid schema must fail loudly rather than drive a destructive prune.
    """

    current: dict[str, set[str]] = {}
    seen_definitions: dict[str, str] = {}
    seen_caveats: dict[str, str] = {}
    for app_config in apps.get_app_configs():
        # Removing the last permissions file retires all package-managed schema,
        # even when the addon remains installed for its other capabilities.
        current[app_config.name] = set()
        package = app_config.name
        try:
            schema = effective_rebac_schema(app_config)
        except Exception as error:
            raise PermissionSchemaReconcileError(f"{package}: {error}") from error
        if schema is None:
            continue
        errors = validate_schema(schema)
        if errors:
            joined = "; ".join(errors)
            raise PermissionSchemaReconcileError(f"{package}: {joined}")

        external_ids: set[str] = set()
        for caveat in schema.caveats:
            previous = seen_caveats.get(caveat.name)
            if previous is not None:
                raise PermissionSchemaReconcileError(
                    f"Duplicate caveat {caveat.name!r} found in {previous} and {package}"
                )
            seen_caveats[caveat.name] = package
            external_ids.add(f"caveat:{caveat.name}")

        for definition in schema.definitions:
            previous = seen_definitions.get(definition.resource_type)
            if previous is not None:
                raise PermissionSchemaReconcileError(
                    f"Duplicate definition {definition.resource_type!r} found in {previous} and {package}"
                )
            seen_definitions[definition.resource_type] = package
            external_ids.add(f"definition:{definition.resource_type}")
            external_ids.update(
                f"relation:{definition.resource_type}#{relation.name}" for relation in definition.relations
            )
            external_ids.update(
                f"permission:{definition.resource_type}#{permission.name}" for permission in definition.permissions
            )

        current[package] = external_ids
    return current


def _is_stale_schema_record(
    record: Any,
    composed: set[str],
    current_external_ids: dict[str, set[str]],
) -> bool:
    """Return whether a package-managed schema row no longer has a source owner."""

    external_id = str(getattr(record, "external_id", ""))
    if not external_id.startswith(_SCHEMA_PREFIXES):
        return False
    package = str(getattr(record, "package", ""))
    if package not in composed:
        return True
    expected = current_external_ids.get(package)
    if expected is None:
        return False
    return external_id not in expected


def _delete_stale_relationships(records: list[Any]) -> None:
    """Directly delete stored relationship rows whose schema is stale."""

    from rebac.models import active_relationship_model

    relationship_model = active_relationship_model()
    stale_resource_types: set[str] = set()
    stale_relations: set[tuple[str, str]] = set()
    for record in records:
        kind, _, name = str(getattr(record, "external_id", "")).partition(":")
        if kind == "definition" and name:
            stale_resource_types.add(name)
        elif kind == "relation" and "#" in name:
            resource_type, relation = name.split("#", maxsplit=1)
            stale_relations.add((resource_type, relation))

    for resource_type in sorted(stale_resource_types):
        relationship_model.objects.filter(resource_type=resource_type).delete()
        relationship_model.objects.filter(subject_type=resource_type).delete()

    for resource_type, relation in sorted(stale_relations):
        if resource_type in stale_resource_types:
            continue
        relationship_model.objects.filter(
            resource_type=resource_type,
            relation=relation,
        ).delete()
        relationship_model.objects.filter(
            subject_type=resource_type,
            optional_subject_relation=relation,
        ).delete()


def _prune_key(record: object) -> tuple[int, str]:
    """Order a managed record for deletion the way the rebac library does."""

    external_id = str(getattr(record, "external_id", ""))
    kind = external_id.split(":", 1)[0]
    return (_PRUNE_ORDER.get(kind, 2), external_id)
