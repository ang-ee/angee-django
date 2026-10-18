"""Converge pre-P6 ownership columns and tuples after runtime migrations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F
from rebac import system_context
from rebac.models import active_relationship_model


def _columns(table: str) -> dict[str, Any]:
    """Return the current columns of ``table``, or an empty mapping when absent."""

    with connection.cursor() as cursor:
        if table not in connection.introspection.table_names(cursor):
            return {}
        return {
            column.name: column
            for column in connection.introspection.get_table_description(cursor, table)
        }


def _rename_column(table: str, old: str, new: str) -> bool:
    """Rename one legacy column exactly once; accept an already-converged table."""

    columns = _columns(table)
    if not columns or old not in columns:
        return False
    if new in columns:
        raise CommandError(
            f"{table} contains both {old} and {new}; refusing to guess which column is authoritative."
        )
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {quote(table)} RENAME COLUMN {quote(old)} TO {quote(new)}"
        )
    return True


def _make_nullable(table: str, column: str) -> bool:
    """Drop PostgreSQL's legacy NOT NULL constraint when it is still present."""

    columns = _columns(table)
    descriptor = columns.get(column)
    if descriptor is None or descriptor.null_ok:
        return False
    if connection.vendor != "postgresql":
        raise CommandError(
            f"{table}.{column} is still NOT NULL on {connection.vendor}; "
            "this P6 production step only alters legacy PostgreSQL schemas."
        )
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {quote(table)} ALTER COLUMN {quote(column)} DROP NOT NULL"
        )
    return True


def _model(app_label: str, model_name: str) -> type[Any] | None:
    """Resolve an optional composed model without requiring every addon on a stack."""

    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _backfill_created_owners() -> int:
    """Copy attribution to the new transferable owner only where owner is empty."""

    updated = 0
    for app_label, model_name in (
        ("parties", "Party"),
        ("parties", "Folder"),
        ("parties", "Circle"),
        ("workflows", "Workflow"),
    ):
        model = _model(app_label, model_name)
        if model is None or not _columns(model._meta.db_table):
            continue
        updated += model._base_manager.filter(
            owner_id__isnull=True,
            created_by_id__isnull=False,
        ).update(owner_id=F("created_by_id"))
    return updated


def _migrate_external_account_owners() -> tuple[int, int]:
    """Move legacy external-account owner tuples into the canonical owner column."""

    account_model = _model("integrate", "ExternalAccount")
    if account_model is None or not _columns(account_model._meta.db_table):
        return 0, 0

    relationship_model = active_relationship_model()
    # The active manager translates this wire-shaped lookup for both local
    # relationship stores (wide columns and registry FKs).
    tuples = relationship_model.objects.filter(
        resource_type="integrate/external_account",
        relation="owner",
        subject_type="auth/user",
        optional_subject_relation="",
    )
    owners: dict[str, set[str]] = defaultdict(set)
    for resource_id, subject_id in tuples.values_list("resource_id", "subject_id"):
        owners[str(resource_id)].add(str(subject_id))

    conflicts = sorted(resource_id for resource_id, subjects in owners.items() if len(subjects) != 1)
    if conflicts:
        raise CommandError(
            "External-account owner tuples are ambiguous for resources: " + ", ".join(conflicts)
        )

    user_model = get_user_model()
    migrated = 0
    for resource_id, subjects in owners.items():
        subject_id = next(iter(subjects))
        account = account_model._base_manager.filter(pk=resource_id).only("pk", "owner_id").first()
        if account is None:
            raise CommandError(f"Owner tuple names missing external account {resource_id}.")
        if not user_model._base_manager.filter(pk=subject_id).exists():
            raise CommandError(
                f"Owner tuple for external account {resource_id} names missing user {subject_id}."
            )
        if account.owner_id not in (None, user_model._meta.pk.to_python(subject_id)):
            raise CommandError(
                f"External account {resource_id} already has a different owner column value."
            )
        if account.owner_id is None:
            migrated += account_model._base_manager.filter(pk=account.pk, owner_id__isnull=True).update(
                owner_id=subject_id
            )

    removed, _details = tuples.delete()
    return migrated, removed


class Command(BaseCommand):
    """Apply the one idempotent post-migrate ownership convergence step."""

    help = "Converge P6 ownership columns, backfills, and legacy external-account tuples."

    def handle(self, *args: Any, **options: Any) -> None:
        """Rename columns first, then atomically move owner data and remove old tuples."""

        del args, options
        renamed = sum(
            (
                _rename_column("agents_agentsession", "owner_id", "user_id"),
                _rename_column("parties_handle", "owner_id", "controller_id"),
            )
        )
        nullable = _make_nullable("integrate_webhooksubscription", "owner_id")
        with transaction.atomic(), system_context(reason="p6 ownership data convergence"):
            backfilled = _backfill_created_owners()
            migrated, removed = _migrate_external_account_owners()
        self.stdout.write(
            self.style.SUCCESS(
                "P6 ownership data converged: "
                f"{renamed} column rename(s), {int(nullable)} nullability change(s), "
                f"{backfilled} creator owner(s), {migrated} external-account owner(s), "
                f"{removed} legacy tuple row(s) removed."
            )
        )
