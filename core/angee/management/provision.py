"""Native provisioning of an explicitly prebuilt Django runtime."""

from __future__ import annotations

import shlex
import time
from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connections
from django.db.models.signals import post_migrate

_ANALYZE_RECEIVER = "angee.provision.analyze_migrated"


def run(command: BaseCommand, options: dict[str, Any]) -> None:
    """Provision one prebuilt native Django runtime in the current process.

    The operator's dependency graph builds the artifact before this command.
    Native command transactions, database readiness, planner statistics and
    explicitly requested installed addon commands retain their existing owners.
    """

    after_commands = []
    for command_line in options.get("after_if_installed", ()):
        try:
            after_command = shlex.split(command_line)
        except ValueError as error:
            raise CommandError(f"Invalid addon command line {command_line!r}: {error}") from error
        if not after_command:
            raise CommandError("--after-if-installed requires a command line.")
        after_commands.append(after_command)

    wait_for_database(command, options["wait_db"])
    for step in plan(options):
        if step[0] == "migrate":
            migrate_and_analyze(command, step)
        else:
            run_step(command, step)
    for after_command in after_commands:
        if after_command[0] in get_commands():
            run_step(command, after_command)
    command.stdout.write(command.style.SUCCESS("angee provision: ok"))


def plan(options: dict[str, Any]) -> list[list[str]]:
    """Map the provision flags to the ordered management command arguments.

    Pure: it reads only the option flags and returns the step list, so the
    plan (contents, ordering, the read-only validation-before-migrate invariant) is testable
    without spawning a process or opening a database. The database wait is not
    a step here — it runs in-process before the plan executes.
    """

    rebac_sync = ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes"]
    if options["force_rebac"]:
        rebac_sync.append("--force-overwrite")
    plan = [
        ["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        rebac_sync,
        ["check", "--database", "default"],
    ]
    return plan


def wait_for_database(command: BaseCommand, seconds: int) -> None:
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
            command.stdout.write(command.style.SUCCESS("angee provision: database ready"))
            return
        if attempt < seconds:
            command.stdout.write(f"angee provision: waiting for database ({attempt}/{seconds})...")
            time.sleep(1)
    raise CommandError(f"angee provision: database did not accept connections within {seconds}s: {last_error}")


def migrate_and_analyze(command: BaseCommand, step: list[str]) -> None:
    """Run the migrate step, then refresh planner statistics of the tables it changed.

    An added or altered column has no statistics until ``ANALYZE`` runs, and
    autovacuum analyzes on row churn, not schema changes, so a large table can
    keep planning on estimates that are orders of magnitude off.
    """

    applied: list[tuple[Any, bool]] = []

    def record(sender: Any, plan: list[tuple[Any, bool]] | None = None, **kwargs: Any) -> None:
        # migrate sends the same plan once per app; keep the first.
        if plan and not applied:
            applied.extend(plan)

    post_migrate.connect(record, weak=False, dispatch_uid=_ANALYZE_RECEIVER)
    try:
        run_step(command, step)
    finally:
        post_migrate.disconnect(dispatch_uid=_ANALYZE_RECEIVER)
    connection = connections["default"]
    if connection.vendor != "postgresql" or not applied:
        return
    tables = analyzed_tables(applied, connection.introspection.table_names())
    if not tables:
        return
    command.stdout.write(command.style.MIGRATE_HEADING(f"angee provision: analyze {len(tables)} migrated tables"))
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {', '.join(connection.ops.quote_name(table) for table in tables)}")


def analyzed_tables(plan: list[tuple[Any, bool]], existing: Iterable[str]) -> list[str]:
    """Return the existing tables of every app the plan migrates forward, sorted."""

    labels = {migration.app_label for migration, backwards in plan if not backwards}
    present = set(existing)
    return sorted(
        model._meta.db_table
        for label in labels
        if label in apps.app_configs
        for model in apps.get_app_config(label).get_models(include_auto_created=True)
        if model._meta.managed and model._meta.db_table in present
    )


def run_step(command: BaseCommand, step: list[str]) -> None:
    """Run one command in the current registry with its CLI check policy.

    ``call_command`` defaults to skipping checks, so explicitly preserve
    each checked command's CLI policy. Commands that opt out of checks don't
    accept the ``skip_checks`` option. Command-owned transactions stay independent;
    any failure stops the sequence and identifies the command that failed.
    """

    label = " ".join(step)
    command.stdout.write(command.style.MIGRATE_HEADING(f"angee provision: {label}"))
    command.stdout.flush()
    try:
        subcommand = load_command_class(get_commands()[step[0]], step[0])
        check_options = {"skip_checks": "--skip-checks" in step} if subcommand.requires_system_checks else {}
        call_command(
            subcommand,
            *step[1:],
            stdout=command.stdout,
            stderr=command.stderr,
            **check_options,
        )
    except SystemExit as error:
        if error.code not in (None, 0):
            raise CommandError(f"angee provision: step '{label}' failed (exit {error.code})") from error
    except Exception as error:
        raise CommandError(f"angee provision: step '{label}' failed: {error}") from error
    finally:
        command.stdout.flush()
        command.stderr.flush()
