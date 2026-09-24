"""Discard every locally stored REBAC grant while preserving schema and audit history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from rebac import app_settings
from rebac.backends.local import mark_relationships_changed
from rebac.models import RebacResource, Relationship, RelationshipRegistry


@dataclass(frozen=True)
class GrantCounts:
    """Stored rows discarded by a grant reset."""

    relationships: int
    registry_relationships: int
    resources: int

def grant_counts() -> GrantCounts:
    """Return counts for both local relationship stores and their registry."""

    if app_settings.REBAC_BACKEND != "local":
        raise CommandError("reset_rebac_grants supports only the local REBAC backend")
    return GrantCounts(
        relationships=Relationship._base_manager.count(),
        registry_relationships=RelationshipRegistry._base_manager.count(),
        resources=RebacResource._base_manager.count(),
    )


def reset_rebac_grants() -> GrantCounts:
    """Atomically clear both local grant stores and their identity registry."""

    with transaction.atomic():
        counts = grant_counts()
        Relationship._base_manager.all().delete()
        RelationshipRegistry._base_manager.all().delete()
        RebacResource._base_manager.all().delete()
        transaction.on_commit(mark_relationships_changed)
    return counts


class Command(BaseCommand):
    """Preview or apply the deliberate local REBAC grant reset."""

    help = "Preview locally stored REBAC grants; pass --apply to discard them."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Discard both local relationship stores and their resource registry.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        counts = reset_rebac_grants() if options["apply"] else grant_counts()
        action = "discarded" if options["apply"] else "would discard"
        self.stdout.write(
            f"REBAC grant reset {action}: {counts.relationships} denormalized relationships, "
            f"{counts.registry_relationships} registry relationships, "
            f"{counts.resources} registered resources"
        )
        if options["apply"]:
            self.stdout.write(
                "After migrations and 'rebac sync', run 'bootstrap_admin' before resuming traffic."
            )
