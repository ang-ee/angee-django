"""Angee build-time management commands."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from django.core.management.base import (
    BaseCommand,
    CommandError,
    CommandParser,
)
from django.db import OperationalError, connections

from angee.compose.runtime import Runtime


class Command(BaseCommand):
    """Expose Angee runtime build, provision, and cleanup commands."""

    help = "Build, provision, and inspect Angee runtime output."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Add Angee subcommands."""

        subcommands = parser.add_subparsers(dest="subcommand", required=True)

        build = subcommands.add_parser("build")
        build.add_argument("--check", action="store_true")
        build.set_defaults(handler=self._handle_build)

        clean = subcommands.add_parser("clean")
        clean.set_defaults(handler=self._handle_clean)

        provision = subcommands.add_parser(
            "provision",
            help="Bring the stack's Django runtime up from a fresh checkout.",
        )
        provision.add_argument(
            "--demo",
            action="store_true",
            help="Load demo-tier resource data (resources load --include-demo).",
        )
        provision.add_argument(
            "--bootstrap-admin",
            action="store_true",
            help="Create the first admin user from settings (bootstrap_admin).",
        )
        provision.add_argument(
            "--force-rebac",
            action="store_true",
            help="Force-overwrite REBAC schema on sync (rebac sync --force-overwrite).",
        )
        provision.add_argument(
            "--wait-db",
            type=int,
            default=60,
            metavar="SECONDS",
            help="Seconds to wait for the default database (default: 60).",
        )
        provision.set_defaults(handler=self._handle_provision)

    def handle(self, *args: Any, **options: Any) -> None:
        """Dispatch the selected subcommand."""

        del args
        handler = cast(Callable[[dict[str, Any]], None], options["handler"])
        handler(options)

    def _handle_build(self, options: dict[str, Any]) -> None:
        """Emit/check runtime sources and materialize addon migrations."""

        runtime = Runtime.from_django()
        try:
            if options["check"]:
                runtime.check()
                message = "angee build --check: ok"
            else:
                runtime.build()
                message = "angee build: ok"
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(message))

    def _handle_clean(self, options: dict[str, Any]) -> None:
        """Delete generated runtime sources."""

        del options
        try:
            Runtime.from_django().clean()
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS("angee clean: ok"))

    def _handle_provision(self, options: dict[str, Any]) -> None:
        """Own the whole runtime bring-up lifecycle as one command.

        This is the single owner of the stack bring-up sequence — it replaces the
        dev stack's job DAG and the local stack's inline ``&&``-chain, so both
        layouts run one ``manage.py angee provision`` instead of restating the
        steps. The order is fixed:

        1. Wait for the default database to accept connections (in-process).
        2. ``angee build`` — emit the concrete runtime and materialize applicable
           addon-owned migrations onto each downstream app's current leaf.
        3. ``reconcile_permissions`` — prune stale package-managed REBAC schema
           before any check-gated DB step (see angee.platform.permissions).
        4. ``makemigrations`` — bare; the composer owns app discovery.
        5. ``migrate --noinput``.
        6. ``rebac sync --yes`` (``--force-overwrite`` when ``--force-rebac``).
        7. ``resources load`` (``--include-demo`` when ``--demo``).
        8. ``schema`` — render the GraphQL SDL.
        9. ``bootstrap_admin`` — only when ``--bootstrap-admin``.

        Every step after the database wait runs in a fresh interpreter (see
        :meth:`_run_step`). The composer is emit-only: this process imported the
        OLD generated runtime at boot, so running makemigrations/migrate in-process
        would operate on stale models. A fresh child loads the freshly emitted
        concrete models — the contract documented in AGENTS.md "Run From The Root".
        """

        self._wait_for_database(options["wait_db"])
        manage_py = self._manage_py_path()
        for step in self._provision_plan(options):
            self._run_step(manage_py, step)
        self.stdout.write(self.style.SUCCESS("angee provision: ok"))

    @staticmethod
    def _provision_plan(options: dict[str, Any]) -> list[list[str]]:
        """Map the provision flags to the ordered child ``manage.py`` argv suffixes.

        Pure: it reads only the option flags and returns the step list, so the
        plan (contents, ordering, the build-before-migrate invariant) is testable
        without spawning a process or opening a database. The database wait is not
        a step here — it runs in-process before the plan executes.
        """

        rebac_sync = ["rebac", "sync", "--yes"]
        if options["force_rebac"]:
            rebac_sync.append("--force-overwrite")
        resources_load = ["resources", "load"]
        if options["demo"]:
            resources_load.append("--include-demo")
        plan = [
            ["angee", "build"],
            ["reconcile_permissions"],
            ["makemigrations"],
            ["migrate", "--noinput"],
            rebac_sync,
            resources_load,
            ["schema"],
        ]
        if options["bootstrap_admin"]:
            plan.append(["bootstrap_admin"])
        return plan

    @staticmethod
    def _manage_py_path() -> str:
        """Resolve the ``manage.py`` this command was invoked through.

        Provision is always invoked via ``python manage.py angee provision``, so
        ``sys.argv[0]`` is the entrypoint; resolve it to an absolute path so each
        child spawns the same entrypoint regardless of the child's cwd.
        """

        return str(Path(sys.argv[0]).resolve())

    def _wait_for_database(self, seconds: int) -> None:
        """Block until the default database accepts a connection, or time out.

        Retries ``ensure_connection`` on a 1s interval up to ``seconds``, closing
        the probe connection on success. On timeout it raises ``CommandError``
        carrying the last connection error so the failure names the real cause.
        """

        connection = connections["default"]
        last_error: OperationalError | None = None
        for attempt in range(1, max(seconds, 1) + 1):
            try:
                connection.ensure_connection()
            except OperationalError as error:
                last_error = error
            else:
                connection.close()
                self.stdout.write(self.style.SUCCESS("angee provision: database ready"))
                return
            if attempt < seconds:
                self.stdout.write(f"angee provision: waiting for database ({attempt}/{seconds})...")
                time.sleep(1)
        raise CommandError(f"angee provision: database did not accept connections within {seconds}s: {last_error}")

    def _run_step(self, manage_py: str, step: list[str]) -> None:
        """Run one provision step in a fresh interpreter, streaming its output.

        The child inherits this process's env, cwd, and stdout/stderr (no capture),
        so a fresh interpreter loads the freshly emitted concrete models. A
        non-zero exit aborts provision with a ``CommandError`` naming the step.
        """

        label = " ".join(step)
        self.stdout.write(self.style.MIGRATE_HEADING(f"angee provision: {label}"))
        self.stdout.flush()
        result = subprocess.run([sys.executable, manage_py, *step], check=False)
        if result.returncode != 0:
            raise CommandError(f"angee provision: step '{label}' failed (exit {result.returncode})")
