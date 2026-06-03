"""Refresh the operator console's codegen SDL from the live daemon.

The daemon owns its GraphQL schema; rather than hand-maintain types, the console
derives them from the daemon's own SDL. This command introspects the running
daemon over the addon's authenticated connection and writes the contract where
frontend codegen reads it (`web/schema/operator.graphql`). Run it from the dev
stack once the daemon is up; it is the daemon-side analogue of `manage.py schema`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

import angee.operator
from angee.operator.daemon import OperatorDaemon

SDL_PATH = Path(angee.operator.__file__).resolve().parent / "web" / "schema" / "operator.graphql"
"""The codegen schema input, committed in the operator web package."""


class Command(BaseCommand):
    help = "Introspect the operator daemon and write its SDL for frontend codegen."

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
            raise CommandError(
                "operator daemon URL/token not configured "
                "(ANGEE_OPERATOR_URL / ANGEE_OPERATOR_TOKEN)"
            )
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
            raise CommandError(
                f"operator daemon unreachable after {options['retries']} attempts; "
                "SDL not refreshed"
            )
        SDL_PATH.write_text(sdl if sdl.endswith("\n") else f"{sdl}\n")
        self.stdout.write(self.style.SUCCESS(f"operator schema -> {SDL_PATH}"))
