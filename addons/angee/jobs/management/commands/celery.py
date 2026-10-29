"""Load the installed jobs app through Django before entering Celery's CLI."""

from __future__ import annotations

from click import ClickException
from django.core.management.base import BaseCommand

from angee.jobs.celery import app


class Command(BaseCommand):
    """Let Django load addon roots and Celery own its complete command line."""

    help = "Run the configured Celery application (worker, beat, inspect, and other native commands)."

    def run_from_argv(self, argv: list[str]) -> None:
        """Forward trailing arguments without re-parsing Celery's options.

        ManagementUtility has already populated Django before discovering this
        addon command. Celery owns its checks and worker/task lifecycle; the host
        classifies this dispatcher as a process without a shared work boundary.
        """

        try:
            exit_code = app.start(argv[2:])
        except ClickException as error:
            error.show()
            exit_code = error.exit_code
        raise SystemExit(exit_code)
