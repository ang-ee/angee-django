"""Angee build-time management commands."""

from __future__ import annotations

import subprocess
import sys
import time
from argparse import SUPPRESS
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from django.core.management import call_command, get_commands, load_command_class
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
        build.add_argument("--fresh-history", action="store_true")
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
        provision.add_argument("--fresh-history", action="store_true")
        provision.add_argument("--post-build", action="store_true", help=SUPPRESS)
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
            fresh_history = options.get("fresh_history", False)
            if options["check"]:
                if fresh_history:
                    raise CommandError("angee build --check does not accept --fresh-history")
                runtime.check()
                message = "angee build --check: ok"
            else:
                if fresh_history:
                    self._require_fresh_history_database()
                dependency_result = runtime.build(fresh_history=fresh_history)
                style = self.style.WARNING if dependency_result.skipped else self.style.SUCCESS
                self.stdout.write(style(f"angee build: addon dependencies {dependency_result.value}"))
                message = "angee build: ok"
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(message))

    def _handle_clean(self, options: dict[str, Any]) -> None:
        """Delete generated runtime sources."""

        del options
        try:
            Runtime.clean_configured()
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
        3. ``makemigrations --skip-checks`` — every provision defers system checks
           until migrations and permission sync have reconciled persisted state
           with the newly emitted model graph.
        4. With ``--fresh-history`` only, ``angee build --fresh-history`` — attach
           explicitly classified historical declarations to the newly generated
           final-model initial graph while retaining current operational migrations.
        5. ``migrate --noinput --skip-checks`` with checks deferred on every provision.
        6. ``reconcile_permissions`` — prune stale package-managed REBAC schema
           only after identity migrations have preserved moved rows.
        7. ``rebac --skip-checks sync --yes`` (``--force-overwrite`` when
           ``--force-rebac``) — replace old persisted policy before validating it.
        8. ``check`` — enforce the complete model and persisted-REBAC contract
           after migration and sync, before user data or schema output proceeds.
        9. ``resources load`` (``--include-demo`` when ``--demo``).
        10. ``schema`` — render the GraphQL SDL.
        11. ``bootstrap_admin`` — only when ``--bootstrap-admin``.

        Build runs in the parent. One fresh interpreter then loads the emitted
        models and runs the remaining commands together via ``call_command``.
        Django gives migration commands their own loaders; REBAC invalidates its
        backend after sync. The completed schema builds can therefore be reused
        between checks and SDL generation. Each command retains its transactions.

        ``--post-build`` is the internal child entrypoint. It must not repeat
        build or start another child, because the new app registry is the
        boundary that makes the emitted runtime visible to subsequent commands.
        """

        steps = self._provision_plan(options)
        if options["post_build"]:
            for step in steps[1:]:
                self._run_step(step)
            return

        self._wait_for_database(options["wait_db"])
        self._run_step(steps[0])
        child = [sys.executable, self._manage_py_path(), "angee", "provision", "--post-build"]
        for option in ("demo", "force_rebac", "bootstrap_admin", "fresh_history"):
            if options[option]:
                child.append(f"--{option.replace('_', '-')}")
        result = subprocess.run(child, check=False)
        if result.returncode != 0:
            raise CommandError(f"angee provision: post-build commands failed (exit {result.returncode})")
        self.stdout.write(self.style.SUCCESS("angee provision: ok"))

    @staticmethod
    def _provision_plan(options: dict[str, Any]) -> list[list[str]]:
        """Map the provision flags to the ordered management command arguments.

        Pure: it reads only the option flags and returns the step list, so the
        plan (contents, ordering, the build-before-migrate invariant) is testable
        without spawning a process or opening a database. The database wait is not
        a step here — it runs in-process before the plan executes.
        """

        rebac_sync = ["rebac", "--skip-checks", "sync", "--yes"]
        if options["force_rebac"]:
            rebac_sync.append("--force-overwrite")
        resources_load = ["resources", "load"]
        if options["demo"]:
            resources_load.append("--include-demo")
        fresh_history = options.get("fresh_history", False)
        build = ["angee", "build", "--fresh-history"] if fresh_history else ["angee", "build"]
        plan = [
            build,
            ["makemigrations", "--skip-checks"],
        ]
        if fresh_history:
            plan.append(build)
        plan.extend(
            [
                ["migrate", "--noinput", "--skip-checks"],
                ["reconcile_permissions"],
                rebac_sync,
                ["check"],
                resources_load,
                ["schema"],
            ]
        )
        if options["bootstrap_admin"]:
            plan.append(["bootstrap_admin"])
        return plan

    @staticmethod
    def _require_fresh_history_database() -> None:
        """Refuse baseline generation unless the selected database is empty."""

        connection = connections["default"]
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
        if tables:
            rendered = ", ".join(sorted(tables)[:5])
            raise CommandError(
                "angee fresh-history requires an empty database; found " + rendered
            )

    @staticmethod
    def _manage_py_path() -> str:
        """Resolve the ``manage.py`` this command was invoked through.

        Provision is always invoked via ``python manage.py angee provision``, so
        ``sys.argv[0]`` is the entrypoint; resolve it to an absolute path so the
        post-build child uses the same entrypoint regardless of its cwd.
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

    def _run_step(self, step: list[str]) -> None:
        """Run one command in the current registry with its CLI check policy.

        ``call_command`` defaults to skipping checks, so explicitly preserve
        each checked command's CLI policy. Commands that opt out of checks don't
        accept the ``skip_checks`` option. Command-owned transactions stay independent;
        any failure stops the sequence and identifies the command that failed.
        """

        label = " ".join(step)
        self.stdout.write(self.style.MIGRATE_HEADING(f"angee provision: {label}"))
        self.stdout.flush()
        try:
            command = load_command_class(get_commands()[step[0]], step[0])
            check_options = {"skip_checks": "--skip-checks" in step} if command.requires_system_checks else {}
            call_command(
                command,
                *step[1:],
                stdout=self.stdout,
                stderr=self.stderr,
                **check_options,
            )
        except SystemExit as error:
            if error.code not in (None, 0):
                raise CommandError(f"angee provision: step '{label}' failed (exit {error.code})") from error
        except Exception as error:
            raise CommandError(f"angee provision: step '{label}' failed: {error}") from error
        finally:
            self.stdout.flush()
            self.stderr.flush()
