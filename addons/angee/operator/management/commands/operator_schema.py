"""Export the running operator daemon's SDL into the composed host's runtime.

The daemon owns its GraphQL schema; rather than hand-maintain types, the console
derives them from the daemon's own SDL. This command introspects the running
daemon over the addon's authenticated connection and writes the SDL to
`<ANGEE_RUNTIME_DIR>/schemas/external/operator.graphql`, which frontend codegen
prefers over the package's committed snapshot (`web/schema/operator.graphql`).
It is the daemon-side analogue of `manage.py schema`: generated output belongs to
the runtime, so a stack's job never rewrites a source checkout. Refresh the
committed snapshot by copying this file when the supported operator changes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from angee.operator.daemon import OperatorDaemon


def sdl_path() -> Path:
    """The live SDL's runtime location, read by codegen before the committed snapshot."""

    return Path(settings.ANGEE_RUNTIME_DIR) / "schemas" / "external" / "operator.graphql"


class Command(BaseCommand):
    help = "Introspect the operator daemon and write its SDL into the runtime for frontend codegen."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--retries",
            type=int,
            default=15,
            help="Attempts to reach the daemon before failing (1s apart).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        daemon = OperatorDaemon.from_settings()
        if daemon.admin_bearer is None or daemon.server_base is None:
            raise CommandError("operator daemon URL/token not configured (ANGEE_OPERATOR_URL / ANGEE_OPERATOR_TOKEN)")
        # Wait for daemon readiness (the stack job runs once the service starts,
        # which may precede it actually serving), then fail loudly rather than
        # leave a stale contract masquerading as success.
        sdl = None
        for attempt in range(max(1, int(options["retries"]))):
            sdl = daemon.introspect_sdl()
            if sdl is not None:
                break
            time.sleep(1.0)
        if sdl is None:
            raise CommandError(f"operator daemon unreachable after {options['retries']} attempts; SDL not refreshed")
        path = sdl_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sdl if sdl.endswith("\n") else f"{sdl}\n")
        self.stdout.write(self.style.SUCCESS(f"operator schema -> {path}"))
