"""Import a Facebook Download Your Information directory into one channel."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from rebac import system_context

from angee.messaging_integrate_facebook.connect import (
    confirmed_facebook_channel,
    get_or_create_facebook_channel,
)
from angee.messaging_integrate_facebook.importer import import_archive


class Command(BaseCommand):
    """Resolve a target and dispatch Facebook takeout parsing to its facade."""

    help = "Import a Facebook Download Your Information directory."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the export root, target channel, and import controls."""

        parser.add_argument("root", help="Extracted Facebook export directory.")
        parser.add_argument("--channel", help="Target Facebook channel (sqid).")
        parser.add_argument(
            "--create",
            metavar="NAME",
            help="Create/reuse a channel with this name.",
        )
        parser.add_argument("--owner", help="Owner username required by --create.")
        parser.add_argument("--resume", action="store_true", help="Skip imported thread prefixes.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and count without writes.",
        )
        parser.add_argument("--limit", type=int, help="Stop after this many messages.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve the channel, invoke the facade, and print its counts."""

        del args
        channel = self._channel(options)
        try:
            counts = import_archive(
                options["root"],
                channel,
                resume=options["resume"],
                dry_run=options["dry_run"],
                limit=options["limit"],
            )
        except (OSError, ValidationError, ValueError) as error:
            raise CommandError(str(error)) from error
        verb = "Parsed" if options["dry_run"] else "Imported"
        summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f"{verb} Facebook export: {summary}."))

    def _channel(self, options: dict[str, Any]) -> Any:
        """Resolve exactly one existing or idempotently created Facebook channel."""

        if bool(options["channel"]) == bool(options["create"]):
            raise CommandError("Pass exactly one of --channel or --create.")
        if options["channel"]:
            try:
                return confirmed_facebook_channel(options["channel"])
            except ValidationError as error:
                raise CommandError(str(error)) from error
        if not options["owner"]:
            raise CommandError("--create needs --owner <username>.")
        with system_context(reason="facebook_import.resolve_owner"):
            owner = get_user_model()._base_manager.filter(username=options["owner"]).first()
        if owner is None:
            raise CommandError(f"No user {options['owner']!r}.")
        return get_or_create_facebook_channel(owner, name=options["create"])
