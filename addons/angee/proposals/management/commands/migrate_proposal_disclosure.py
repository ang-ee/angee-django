"""Inspect or apply the proposal disclosure transition before permission sync."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from angee.proposals.disclosure import DisclosureTransition


class Command(BaseCommand):
    """Dispatch transition policy to its owner."""

    help = "Preview or migrate legacy proposal ceremony tuples."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        transition = DisclosureTransition()
        try:
            changes = transition.apply() if options["apply"] else transition.changes()
            count = 0
            for change in changes:
                prefix = "BLOCKED" if change.blocker else change.operation
                self.stdout.write(f"{prefix}: {change.resource} {change.subject}")
                count += 1
        except ValidationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"{count} transition operations {'applied' if options['apply'] else 'identified'}.")
