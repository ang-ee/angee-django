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
"""

from __future__ import annotations

from django.apps import apps
from django.db import DatabaseError, transaction

# A managed record's ``external_id`` is ``<kind>:<name>``; only schema rows are ours
# to prune (the library also manages relationship rows under other prefixes).
_SCHEMA_PREFIXES = ("caveat:", "definition:", "relation:", "permission:")
# Delete a definition after the permissions/caveats that reference it, mirroring the
# library's ``_stale_record_prune_order`` so foreign keys resolve cleanly.
_PRUNE_ORDER = {"permission": 0, "definition": 1, "caveat": 1}


def reconcile_permission_schema() -> int:
    """Prune ``Schema*`` rows for packages no longer in the composed app set.

    Idempotent and best-effort: returns the number of orphaned managed rows pruned,
    and is a no-op on a fresh/unmigrated database (no ``Schema*`` tables yet). Runs
    under ``system_context`` in one transaction.
    """

    from rebac import system_context
    from rebac.models import PackageManagedRecord

    composed = {app_config.name for app_config in apps.get_app_configs()}
    try:
        orphaned = [
            record
            for record in PackageManagedRecord.objects.exclude(package__in=composed)
            if record.external_id.startswith(_SCHEMA_PREFIXES)
        ]
    except DatabaseError:  # rebac tables not migrated yet (fresh database)
        return 0
    if not orphaned:
        return 0

    with system_context(reason="angee.platform.reconcile_permission_schema"), transaction.atomic():
        for record in sorted(orphaned, key=_prune_key):
            target = record.target
            if target is not None:
                target.delete()
            record.delete()
    return len(orphaned)


def _prune_key(record: object) -> tuple[int, str]:
    """Order a managed record for deletion the way the rebac library does."""

    external_id = str(getattr(record, "external_id", ""))
    kind = external_id.split(":", 1)[0]
    return (_PRUNE_ORDER.get(kind, 2), external_id)
