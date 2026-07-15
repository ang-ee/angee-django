"""Reconcile the REBAC permission schema to the composed addon set.

When an addon is uninstalled it leaves ``INSTALLED_APPS``, but ``rebac sync`` only
ever revisits *composed* apps (``apps.get_app_configs()``) — so the uninstalled
addon's ``Schema*`` rows orphan, and the library's ``rebac.E009`` system check then
fails for every checked command (``makemigrations``, ``migrate``, ``rebac sync``),
breaking the very rebuild the uninstall triggers.

:func:`reconcile_permission_schema` is the global counterpart to the library's
per-package prune: it removes every ``Schema*`` row owned by a package no longer in
the composed app set. ``platform`` owns the addon lifecycle (install / uninstall /
reconcile), so it owns this cleanup; the ``reconcile_permissions`` management command
runs it check-free, before the gated DB steps (see that command and the build
lifecycle). The composed set is ``apps.get_app_configs()`` — the same source
``rebac sync`` keys its packages on — not the addon-only rollups: a package is
orphaned only when its app is not loaded at all.

The same check-gated deadlock happens when a composed package removes or renames one
definition: ``rebac sync`` would prune the stale package-managed rows, but it cannot
start while the old row still fails system checks. The reconcile step therefore
compares package-managed schema rows with the current ``permissions.zed`` declarations
and prunes stale rows inside still-composed packages too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.apps import apps
from django.db import DatabaseError, transaction

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
    under ``system_context`` in one transaction.
    """

    from rebac import system_context
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

    with system_context(reason="angee.platform.reconcile_permission_schema"), transaction.atomic():
        _delete_stale_relationships(stale)
        for record in sorted(stale, key=_prune_key):
            target = record.target
            if target is not None:
                target.delete()
            record.delete()
    return len(stale)


def _current_schema_external_ids_by_package() -> dict[str, set[str]]:
    """Return the schema rows currently declared by each composed package.

    Mirrors ``django-zed-rebac``'s sync command enough to know which managed rows are
    still source-owned. Parse and validate every current declaration before deleting
    anything; an invalid schema must fail loudly rather than drive a destructive prune.
    """

    from rebac.schema.parser import parse_zed, validate_schema

    current: dict[str, set[str]] = {}
    seen_definitions: dict[str, str] = {}
    seen_caveats: dict[str, str] = {}
    for app_config in apps.get_app_configs():
        schema_path = _resolve_schema_path(app_config)
        if schema_path is None:
            continue

        package = app_config.name
        try:
            schema = parse_zed(schema_path.read_text(encoding="utf-8"))
        except Exception as error:
            raise PermissionSchemaReconcileError(f"{package}: {error}") from error
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
                    f"Duplicate definition {definition.resource_type!r} found in "
                    f"{previous} and {package}"
                )
            seen_definitions[definition.resource_type] = package
            external_ids.add(f"definition:{definition.resource_type}")
            external_ids.update(
                f"relation:{definition.resource_type}#{relation.name}"
                for relation in definition.relations
            )
            external_ids.update(
                f"permission:{definition.resource_type}#{permission.name}"
                for permission in definition.permissions
            )

        current[package] = external_ids
    return current


def _resolve_schema_path(app_config: Any) -> Path | None:
    """Return the package's REBAC schema path, matching ``rebac sync``."""

    rel = getattr(app_config, "rebac_schema", None)
    path = Path(app_config.path) / rel if rel is not None else Path(app_config.path) / "permissions.zed"
    return path if path.exists() else None


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
    """Delete relationship data whose resource or relation schema is stale."""

    from rebac import delete_relationships
    from rebac.types import RelationshipFilter

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
        delete_relationships(RelationshipFilter(resource_type=resource_type))
        delete_relationships(RelationshipFilter(subject_type=resource_type))

    for resource_type, relation in sorted(stale_relations):
        if resource_type in stale_resource_types:
            continue
        delete_relationships(RelationshipFilter(resource_type=resource_type, relation=relation))


def _prune_key(record: object) -> tuple[int, str]:
    """Order a managed record for deletion the way the rebac library does."""

    external_id = str(getattr(record, "external_id", ""))
    kind = external_id.split(":", 1)[0]
    return (_PRUNE_ORDER.get(kind, 2), external_id)
