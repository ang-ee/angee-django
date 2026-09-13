"""Migrate stored model-backed REBAC references to canonical primary-key ids."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from rebac import system_context

from angee.base.identity_migration import migrate_rebac_ids, plan_rebac_id_migration


class Command(BaseCommand):
    """Preview or apply the one-shot REBAC primary-key identity cutover."""

    help = (
        "Preview the REBAC primary-key identity migration; pass --apply with application "
        "writers stopped to write it."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the fully preflighted migration while application writers are stopped.",
        )
        parser.add_argument("--database", default="default", help="Database alias to migrate.")

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        using = options["database"]
        with system_context(reason="base.migrate_rebac_ids"):
            plan = (
                migrate_rebac_ids(using=using)
                if options["apply"]
                else plan_rebac_id_migration(using=using)
            )
        action = "applied" if options["apply"] else "dry run"
        self.stdout.write(
            f"REBAC id migration {action}: {plan.references} references, {plan.changes} changes"
        )
