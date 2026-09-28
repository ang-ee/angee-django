"""Preserve intended author-only reads before removing page attribution access."""

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    """Report lost author access or preserve its read portion as explicit shares."""

    help = (
        "Preview the page attribution conversion that migrate applies before permission sync, "
        "or apply it manually. Ownerless-vault pages are reported but never granted."
    )
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Require an explicit preview or apply mode."""

        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true", help="List affected page/author pairs without writing.")
        mode.add_argument("--apply", action="store_true", help="Grant viewer for each author losing read access.")
        parser.add_argument("--vault", action="append", help="Limit to this vault public ID; may be repeated.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the page manager and print its inventory and grant count."""

        del args
        try:
            changes = apps.get_model("knowledge", "Page").objects.migrate_attribution(
                apply=options["apply"], vaults=options["vault"],
            )
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        if not changes:
            self.stdout.write("migrate_page_attribution: nothing pending.")
            return
        for change in changes:
            losses = ",".join(
                name for name, lost in (("read", change.loses_read), ("write", change.loses_write)) if lost
            )
            category = "ownerless-vault" if change.ownerless_vault else "attribution"
            self.stdout.write(
                f"{change.public_id}\tauthor={change.author_id}\tclass={category}\tloses={losses or 'none'}"
            )
        reads = sum(change.loses_read for change in changes)
        writes = sum(change.loses_write for change in changes)
        shares = sum(change.grants_viewer for change in changes)
        excluded = sum(change.ownerless_vault for change in changes)
        action = "created" if options["apply"] else "would create"
        self.stdout.write(
            f"migrate_page_attribution: {reads} lost read(s), {writes} lost write(s); "
            f"{action} {shares} viewer share(s); {excluded} ownerless-vault page(s) excluded."
        )
