"""Preview the addon-owned historical proposal disclosure transition."""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.migrations.state import ProjectState

from angee.proposals.runtime_migrations.legacy_disclosure import transition


class Command(BaseCommand):
    """Dispatch transition policy to its owner."""

    help = "Preview legacy proposal ceremony conversion; Django migrations apply it."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", required=True)
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        historical = ProjectState.from_apps(apps).apps
        count = 0
        for operation, resource, subject in transition(historical, options["database"]):
            self.stdout.write(f"{operation}: {resource} {subject}")
            count += 1
        self.stdout.write(f"{count} transition operations identified.")
