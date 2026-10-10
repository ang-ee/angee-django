"""Preview what each stream of an integration would write back, writing nothing."""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from rebac import system_context

from angee.integrate.streams import preview_push


class Command(BaseCommand):
    """Print, for each stream an integration declares, what a push would write and what it holds back.

    A thin dispatcher over :func:`angee.integrate.streams.preview_push`. Before
    resuming an integration paused for a mapping change, every stream should
    preview no writes.
    """

    help = "Show what a push would write for each stream of the given integrations, without writing."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Take integration public ids and an optional key listing."""

        parser.add_argument("integrations", nargs="+", help="Integration public ids, such as int_98FqgRua.")
        parser.add_argument("--keys", action="store_true", help="List each external key, not only the counts.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Preview the current stream of every partition each named integration declares."""

        del args
        integration_model = apps.get_model("integrate", "Integration")
        stream_model = apps.get_model("integrate", "SyncStream")
        with system_context(reason="integrate.preview_push"):
            for sqid in options["integrations"]:
                integration = integration_model.objects.filter(sqid=sqid).first()
                if integration is None:
                    raise CommandError(f"There is no integration {sqid}.")
                adapter = integration.concrete_capability().backend
                try:
                    for definition in adapter.streams():
                        streams = stream_model.objects.current_for_bridge(integration, definition.key)
                        for stream in streams.filter(partition=definition.partition):
                            self._report(sqid, stream, preview_push(stream, adapter), keys=options["keys"])
                finally:
                    adapter.close()

    def _report(self, sqid: str, stream: Any, preview: Any, *, keys: bool) -> None:
        """Write one stream's counts, and its keys when asked."""

        self.stdout.write(
            f"{sqid} {stream.key} {stream.partition}: {len(preview.writes)} to write, "
            f"{len(preview.held)} held back by conflicts or refusals"
        )
        if keys:
            for external_key in preview.writes:
                self.stdout.write(f"  write {external_key}")
            for external_key in preview.held:
                self.stdout.write(f"  held  {external_key}")
