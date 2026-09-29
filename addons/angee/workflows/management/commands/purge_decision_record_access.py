"""Remove retired per-Decision evidence tuples during the standing-access cutover."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    """Preview or apply the purge of retired ``pending_decision`` relationship rows."""

    help = (
        "Preview retired per-Decision evidence tuples; pass --apply to delete them. "
        "--check-pending lists pending Decisions whose reviewers cannot read their evidence."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Delete the retired tuples from both local stores.")
        parser.add_argument(
            "--check-pending",
            action="store_true",
            help="List pending Decisions whose issuer, assignees, or escalation cannot read their evidence.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the Decision owner and report counts and unreadable pending reviews."""

        del args
        decisions = apps.get_model("workflows", "Decision").objects
        if options["check_pending"]:
            failures = decisions.pending_evidence_failures()
            for decision, error in failures:
                self.stdout.write(f"{decision.sqid}: {'; '.join(error.messages)}")
            self.stdout.write(f"purge_decision_record_access: {len(failures)} pending Decision(s) need attention")
        purge = decisions.purge_record_access(apply=options["apply"])
        action = "deleted" if options["apply"] else "would delete"
        self.stdout.write(
            self.style.SUCCESS(
                f"purge_decision_record_access: {action} {purge.relationships} denormalized and "
                f"{purge.registry_relationships} registry relationship(s)"
            )
        )
