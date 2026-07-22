"""Import SMS + iMessage from an unencrypted iPhone backup into a messaging channel."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.dateparse import parse_datetime
from rebac import system_context

from angee.integrate_iphone.backup import BackupError
from angee.messaging_integrate_imessage.backend import ImessageChannelBackend
from angee.messaging_integrate_imessage.connect import create_imessage_channel
from angee.messaging_integrate_imessage.importer import import_backup


class Command(BaseCommand):
    """Parse an unencrypted iPhone backup and land its messages on one channel.

    The store reader and the ingest drive live in the addon; this command parses
    arguments and dispatches. Idempotent: a re-run (or a resumed interrupted run)
    converges on the same rows.
    """

    help = "Import SMS + iMessage from an unencrypted iPhone backup directory."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the backup location, target channel, and selection options."""

        parser.add_argument("backup_dir", help="The unencrypted iPhone backup directory.")
        parser.add_argument("--channel", help="Target iMessage channel (sqid).")
        parser.add_argument(
            "--create",
            metavar="NAME",
            help="Create a disconnected iMessage channel with this name instead of --channel.",
        )
        parser.add_argument("--owner", help="Owner username for --create.")
        parser.add_argument("--since", help="Import messages on/after this ISO-8601 instant.")
        parser.add_argument("--batch-size", type=int, default=500, help="Messages per ingest batch.")
        parser.add_argument("--limit", type=int, help="Stop after this many messages.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and count without writing anything.",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Skip each chat's already-imported prefix (advance an interrupted import).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Resolve the channel, open the backup, and drive the batched import."""

        del args
        since = None
        if options["since"]:
            since = parse_datetime(options["since"])
            if since is None or since.tzinfo is None:
                raise CommandError("--since must be an ISO-8601 instant with a timezone.")
        channel = self._channel(options)
        try:
            total = import_backup(
                channel,
                options["backup_dir"],
                since=since,
                limit=options["limit"],
                batch_size=options["batch_size"],
                dry_run=options["dry_run"],
                resume=options["resume"],
                on_batch=lambda done: self.stdout.write(f"{done} message(s) processed…"),
            )
        except BackupError as error:
            raise CommandError(str(error)) from error
        verb = "Parsed" if options["dry_run"] else "Imported"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} message(s) into {channel.display_name}."))

    def _channel(self, options: dict[str, Any]) -> Any:
        """Resolve the target channel from ``--channel`` or create one for ``--create``."""

        if options["channel"] and options["create"]:
            raise CommandError("Pass either --channel or --create, not both.")
        if options["channel"]:
            model = apps.get_model("messaging", "Channel")
            with system_context(reason="imessage_import.resolve_channel"):
                channel = model.objects.filter(sqid=options["channel"]).first()
            if channel is None or channel.backend_class != ImessageChannelBackend.key:
                raise CommandError(f"No iMessage channel {options['channel']!r}.")
            return channel
        if options["create"]:
            if not options["owner"]:
                raise CommandError("--create needs --owner <username>.")
            with system_context(reason="imessage_import.resolve_owner"):
                owner = get_user_model().objects.filter(username=options["owner"]).first()
            if owner is None:
                raise CommandError(f"No user {options['owner']!r}.")
            return create_imessage_channel(owner, name=options["create"])
        raise CommandError("Pass --channel <sqid> or --create <name> --owner <username>.")
