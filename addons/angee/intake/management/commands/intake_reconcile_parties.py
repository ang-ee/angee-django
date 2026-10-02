"""Report legacy unconfirmed requester links before explicitly clearing them."""

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Delegate the dry-run-first reconciliation to the Need manager."""

    help = "Report unconfirmed captured parties and affected accounts; --apply clears eligible links."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Clear eligible unconfirmed party links.")

    def handle(self, *args: Any, **options: Any) -> None:
        count = 0
        for report in apps.get_model("intake", "Need").objects.reconcile_parties(apply=options["apply"]):
            status = "cleared" if report["applied"] else "would clear" if report["eligible"] else "retained"
            self.stdout.write(f'{report["need"]}: {status}; affected account: {report["account"] or "none"}')
            count += int(report["eligible"])
        self.stdout.write(f'{count} eligible request(s); {"applied" if options["apply"] else "dry run"}.')
