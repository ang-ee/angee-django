"""Database preparation and durable readiness for a verified composition.

Hooks are idempotent and may be run again after a crash. PostgreSQL serializes
runners with a database-scoped advisory lock. Other vendors require one runner.
"""
from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from django.apps import apps
from django.apps.registry import Apps
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import BaseCommand, CommandError, OutputWrapper
from django.db import DatabaseError, OperationalError, connections, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.migrations.exceptions import InconsistentMigrationHistory, NodeNotFoundError
from django.db.migrations.executor import MigrationExecutor
from django.db.models.signals import post_migrate
from django.utils.functional import classproperty
from django.utils.timezone import now

# One lock for every receipt digest in the same database. A newer composition
# must not race an older runner during the record's invalidation/completion.
PROVISION_LOCK = 0x416E676565507276


class ProvisionRecorder:
    """Own the bootstrap table exactly where MigrationRecorder cannot migrate it.

    The private registry keeps this infrastructure model outside both authored
    declarations and generated migration state. All queries use the chosen alias.
    """

    _record_class: Any = None

    @classproperty
    def Record(cls) -> type[models.Model]:
        if cls._record_class is None:
            class Record(models.Model):
                id = models.PositiveSmallIntegerField(primary_key=True, default=1)
                state = models.CharField(max_length=16)
                digest = models.CharField(max_length=64)
                compiler = models.CharField(max_length=255)
                updated = models.DateTimeField(default=now)

                class Meta:
                    apps = Apps()
                    app_label = "base_provision"
                    db_table = "base_provision"
                    managed = False
                    constraints = [models.CheckConstraint(condition=models.Q(id=1), name="base_provision_singleton")]

            cls._record_class = Record
        return cls._record_class

    def __init__(self, connection: BaseDatabaseWrapper) -> None:
        self.connection = connection
        self._has_table = False

    @property
    def records(self) -> Any:
        return self.Record.objects.using(self.connection.alias)

    def has_table(self) -> bool:
        if not self._has_table:
            with self.connection.cursor() as cursor:
                self._has_table = self.Record._meta.db_table in self.connection.introspection.table_names(cursor)
        return self._has_table

    def ensure_schema(self) -> None:
        """Create the singleton table under the provision lock before migrate."""
        if not self.has_table():
            with self.connection.schema_editor() as editor:
                editor.create_model(self.Record)
            self._has_table = True

    def read(self) -> Any:
        return self.records.filter(pk=1).first() if self.has_table() else None

    def write(self, state: str, digest: str, compiler: str) -> None:
        """Commit one lifecycle change independently of subsequent commands."""
        if self.connection.in_atomic_block or not self.connection.get_autocommit():
            raise CommandError("Provision records require an independent autocommit statement")
        self.records.update_or_create(pk=1, defaults={
            "state": state, "digest": digest, "compiler": compiler, "updated": now(),
        })


@dataclass(frozen=True)
class ProvisionContext:
    """The selected database and native command IO supplied to each app hook."""

    database: str
    stdout: OutputWrapper
    stderr: OutputWrapper
    verbosity: int = 1

    def call(self, command: str, *args: Any, **options: Any) -> None:
        """Dispatch a native command; hooks supply its supported alias option."""
        call_command(command, *args, stdout=self.stdout, stderr=self.stderr, verbosity=self.verbosity, **options)


class ProvisionHook(Protocol):
    """An AppConfig's idempotent database preparation after migrations."""

    def compose_provision(self, context: ProvisionContext) -> None: ...


@contextmanager
def advisory_lock(connection: BaseDatabaseWrapper) -> Iterator[None]:
    """Hold a session lock on a physical, unpooled PostgreSQL connection."""
    if connection.vendor != "postgresql":
        yield
        return
    import psycopg

    params = connection.get_connection_params()
    params.pop("pool", None)
    params["autocommit"] = True
    physical = psycopg.connect(**params)
    locked = False
    try:
        with physical.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (PROVISION_LOCK,))
            locked = True
        yield
    finally:
        try:
            if locked:
                with physical.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (PROVISION_LOCK,))
        finally:
            physical.close()


def wait_for_database(command: BaseCommand, seconds: int, *, using: str = "default") -> None:
    """Retry native connection establishment while preserving the real error."""
    connection = connections[using]
    last_error: OperationalError | None = None
    for attempt in range(1, max(seconds, 1) + 1):
        try:
            connection.ensure_connection()
        except OperationalError as error:
            last_error = error
            connection.close()
        else:
            connection.close()
            return
        if attempt < seconds:
            command.stdout.write(f"provision: waiting for database ({attempt}/{seconds})...")
            time.sleep(1)
    raise CommandError(f"provision: database {using!r} did not accept connections within {seconds}s: {last_error}")


def require_provisioned(using: str = "default", timeout: float = 0) -> None:
    """Require this process's verified receipt and all migrations to be ready.

    Missing/in-progress records and stale digests refuse readiness. Timeout only
    waits for another provisioner; this function never repairs database state.
    """
    from django_compose.serving import verified_receipt

    deadline = time.monotonic() + timeout
    receipt = verified_receipt()
    while True:
        connection = connections[using]
        try:
            record = ProvisionRecorder(connection).read()
            reason = (
                "missing provision record" if record is None
                else f"provision state is {record.state}" if record.state != "complete"
                else "provision receipt digest differs from this process" if record.digest != receipt.DIGEST
                else "provision compiler version differs from this process" if record.compiler != receipt.COMPILER
                else ""
            )
            if not reason:
                executor = MigrationExecutor(connection)
                executor.loader.check_consistent_history(connection)
                if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                    reason = "unapplied migrations"
        except (DatabaseError, InconsistentMigrationHistory, NodeNotFoundError) as error:
            reason = str(error)
        if not reason:
            return
        if time.monotonic() >= deadline:
            raise CommandError(f"Database {using!r} is not provisioned: {reason}")
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def run(command: BaseCommand, options: dict[str, Any]) -> None:
    """Prepare one database under the receipt lease and provision lock."""
    from django_compose.serving import receipt_lease

    using = options["database"]
    if options["check"]:
        require_provisioned(using=using, timeout=options["wait_db"])
        command.stdout.write(command.style.SUCCESS("provision: ready"))
        return
    wait_for_database(command, options["wait_db"], using=using)
    with receipt_lease() as receipt, advisory_lock(connections[using]):
        recorder = ProvisionRecorder(connections[using])
        recorder.ensure_schema()
        recorder.write("in_progress", receipt.DIGEST, receipt.COMPILER)
        run_step(command, ["check_migrations"])
        run_step(command, ["check_migrations", "--imports"])
        executor = MigrationExecutor(connections[using])
        executor.loader.check_consistent_history(connections[using])
        migrate_and_analyze(command, ["migrate", "--database", using, "--noinput", "--skip-checks"], using=using)
        context = ProvisionContext(using, command.stdout, command.stderr, options["verbosity"])
        for config in apps.get_app_configs():
            hook = getattr(config, "compose_provision", None)
            if hook is not None:
                try:
                    hook(context)
                except Exception as error:
                    raise CommandError(f"provision: {config.name}.compose_provision failed: {error}") from error
        run_step(command, ["check", "--database", using])
        recorder.write("complete", receipt.DIGEST, receipt.COMPILER)
    command.stdout.write(command.style.SUCCESS("provision: ok"))


_ANALYZE_RECEIVER = "angee.base.provision.analyze_migrated"


def migrate_and_analyze(command: BaseCommand, step: list[str], *, using: str = "default") -> None:
    """Run the migrate step, then refresh planner statistics of the tables it changed.

    An added or altered column has no statistics until ``ANALYZE`` runs, and
    autovacuum analyzes on row churn, not schema changes, so a large table can
    keep planning on estimates that are orders of magnitude off.
    """

    applied: list[tuple[Any, bool]] = []

    def record(sender: Any, plan: list[tuple[Any, bool]] | None = None, **kwargs: Any) -> None:
        # migrate sends the same plan once per app; keep the first.
        if kwargs.get("using") == using and plan and not applied:
            applied.extend(plan)

    post_migrate.connect(record, weak=False, dispatch_uid=_ANALYZE_RECEIVER)
    try:
        run_step(command, step)
    finally:
        post_migrate.disconnect(dispatch_uid=_ANALYZE_RECEIVER)
    connection = connections[using]
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
