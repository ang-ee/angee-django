"""Preview what each two-way stream of an integration would write, writing nothing."""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from rebac import system_context

from angee.integrate.streams import preview_push


class Command(BaseCommand):
    """Print, for each current stream, what a push would write and what conflicts hold back.

    A thin dispatcher over :func:`angee.integrate.streams.preview_push`. Run it on a
    paused integration after a mapping change: the change has settled when every
    stream previews no writes, and the integration can resume.
    """

    help = "Show what a push would write for each stream of the given integrations, without writing."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Take integration public ids and an optional key listing."""

        parser.add_argument("integrations", nargs="+", help="Integration public ids, such as int_98FqgRua.")
        parser.add_argument("--keys", action="store_true", help="List each external key, not only the counts.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Preview every current stream of each named integration."""

        del args
        integration_model = apps.get_model("integrate", "Integration")
        stream_model = apps.get_model("integrate", "SyncStream")
        with system_context(reason="integrate.sync_preview"):
            for sqid in options["integrations"]:
                integration = integration_model.objects.filter(sqid=sqid).first()
                if integration is None:
                    raise CommandError(f"There is no integration {sqid}.")
                adapter = integration.concrete_capability().backend
                stream_keys = stream_model.objects.filter(integration_id=integration.pk).values_list("key", flat=True)
                for key in sorted(set(stream_keys)):
                    for stream in stream_model.objects.current_for_bridge(integration, key):
                        preview = preview_push(stream, adapter)
                        self.stdout.write(
                            f"{sqid} {stream.key} {stream.partition}: {len(preview.writes)} to write, "
                            f"{len(preview.held)} held back by conflicts or refusals"
                        )
                        if options["keys"]:
                            for external_key in preview.writes:
                                self.stdout.write(f"  write {external_key}")
                            for external_key in preview.held:
                                self.stdout.write(f"  held  {external_key}")
