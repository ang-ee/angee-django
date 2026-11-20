"""PostgreSQL contracts for ``ANGEE_DB_PREPARED_STATEMENTS``, through the options ``angee.base.conf`` builds.

Each test builds this test database's ``DATABASE_URL`` options with the deployment
code itself (``_deployment_urls``), reconnects with them, and reads what PostgreSQL
reports for the session in ``pg_prepared_statements``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import environ
import psycopg
import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import ProgrammingError, connection, models
from django.db.backends.signals import connection_created
from rebac import ObjectRef, actor_context, check_permission, system_context
from rebac.errors import PermissionDenied

from angee.base.conf import PREPARE_THRESHOLD, _deployment_urls
from angee.server.signals import PREPARED_STATEMENTS_MAX_UID, connect_prepared_statements_max
from tests.conftest import Backend, Drive, create_platform_admin
from tests.tables import model_tables

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL prepared statements required")

# psycopg prepares a statement after PREPARE_THRESHOLD executions; PostgreSQL plans the
# prepared statement for its values on its next five and may choose a generic plan on
# the sixth. This is the first execution on the connection that may use a generic plan.
GENERIC_PLAN_READS = PREPARE_THRESHOLD + 6

SETTINGS = ("ANGEE_DB_PREPARED_STATEMENTS", "ANGEE_DB_POOL")


def _deployed(monkeypatch: pytest.MonkeyPatch, **environment: str) -> dict[str, Any]:
    """Return the settings ``angee.base.conf`` builds for this test database's URL under ``environment``."""

    database = connection.settings_dict
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://{quote(database['USER'])}:{quote(database['PASSWORD'])}"
        f"@{database['HOST']}:{database['PORT']}/{database['NAME']}",
    )
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    namespace: dict[str, Any] = {}
    _deployment_urls(namespace, environ.Env())
    return namespace


def _other_sessions() -> int:
    """Count the client sessions on the test database other than this connection's own."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            " AND backend_type = 'client backend' AND pid <> pg_backend_pid()"
        )
        return cursor.fetchone()[0]


@contextmanager
def _connected(options: dict[str, Any]) -> Iterator[None]:
    """Reconnect with ``options`` as the connection's OPTIONS; restore them and reconnect on exit.

    Closing requires that no transaction is open, so callers are transactional tests.
    The block leaves no session behind: a pool or connection the options opened would
    keep the test database from being dropped.
    """

    sessions = _other_sessions()
    current = connection.settings_dict["OPTIONS"]
    previous = dict(current)
    connection.close()
    current.clear()
    current.update(options)
    try:
        connection.ensure_connection()
        yield
    finally:
        connection.close()
        if "pool" in current:
            connection.close_pool()
        current.clear()
        current.update(previous)
    assert _other_sessions() <= sessions, "a session opened with the options outlived the block"


def _prepared_statements() -> dict[str, tuple[int, int]]:
    """Return the session's prepared statements with their generic and custom plan counts.

    A client-side binding cursor reads the view, so the read is never prepared itself.
    """

    connection.ensure_connection()
    with psycopg.ClientCursor(connection.connection) as cursor:
        cursor.execute("SELECT statement, generic_plans, custom_plans FROM pg_prepared_statements")
        return {statement: (generic, custom) for statement, generic, custom in cursor.fetchall()}


def _binds_on_the_client() -> bool:
    """Whether the connection writes parameters into the text: PostgreSQL then needs no type for an untyped NULL.

    Server-side binding sends parameters apart from the text, and ``%s IS NULL`` leaves PostgreSQL without a type.
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s IS NULL", [None])
            return cursor.fetchone() == (True,)
    except ProgrammingError:
        return False


def _read_content_types(reads: int) -> None:
    rows = ContentType.objects.filter(app_label="contenttypes", model="contenttype")
    for _ in range(reads):
        assert [row.model for row in rows.all()] == ["contenttype"]


def _content_type_reads() -> dict[str, tuple[int, int]]:
    prepared = _prepared_statements()
    return {statement: plans for statement, plans in prepared.items() if "django_content_type" in statement}


def test_the_setting_unset_binds_on_the_client_and_prepares_nothing(transactional_db: None, monkeypatch: Any) -> None:
    del transactional_db
    options = _deployed(monkeypatch)["DATABASES"]["default"]["OPTIONS"]
    assert "server_side_binding" not in options and "prepare_threshold" not in options
    with _connected(options):
        _read_content_types(GENERIC_PLAN_READS)
        assert _binds_on_the_client()
        assert _prepared_statements() == {}


def test_the_setting_prepares_a_repeated_read_and_reaches_a_generic_plan(
    transactional_db: None, monkeypatch: Any,
) -> None:
    del transactional_db
    options = _deployed(monkeypatch, ANGEE_DB_PREPARED_STATEMENTS="true")["DATABASES"]["default"]["OPTIONS"]
    with _connected(options):
        _read_content_types(PREPARE_THRESHOLD)
        assert _content_type_reads() == {}, "a statement run once is never prepared"
        _read_content_types(GENERIC_PLAN_READS - PREPARE_THRESHOLD)
        [(generic, custom)] = _content_type_reads().values()
        assert generic >= 1
        assert generic + custom == GENERIC_PLAN_READS - PREPARE_THRESHOLD
        assert not _binds_on_the_client()
    assert _binds_on_the_client(), "the test connection is back to its own options"


def test_reconnecting_restores_the_test_connection_after_a_failure(transactional_db: None, monkeypatch: Any) -> None:
    del transactional_db
    options = _deployed(monkeypatch, ANGEE_DB_PREPARED_STATEMENTS="true")["DATABASES"]["default"]["OPTIONS"]
    with pytest.raises(ZeroDivisionError), _connected(options):
        1 / 0  # noqa: B018 - a failure inside the block
    assert "prepare_threshold" not in connection.settings_dict["OPTIONS"]
    assert _binds_on_the_client()


@pytest.mark.parametrize("pool", [False, True], ids=["connection", "pool"])
def test_the_cap_keeps_the_most_recently_used_statements(
    transactional_db: None, monkeypatch: Any, settings: Any, pool: bool,
) -> None:
    """psycopg deallocates the least recently used statement past the cap when the connection next sees a new
    statement, so the session holds at most one statement over the cap, and only until then."""

    del transactional_db
    environment = {"ANGEE_DB_PREPARED_STATEMENTS": "true", **({"ANGEE_DB_POOL": "true"} if pool else {})}
    deployed = _deployed(monkeypatch, **environment)
    settings.ANGEE_DB_PREPARED_STATEMENTS_MAX = 2
    # An earlier import of the ASGI application connected the receiver with the default cap.
    connection_created.disconnect(dispatch_uid=PREPARED_STATEMENTS_MAX_UID)
    connect_prepared_statements_max()

    def run(alias: str) -> list[str]:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT %s::int AS {alias}", [1])
        prepared = sorted(statement.rsplit(" AS ", 1)[1] for statement in _prepared_statements())
        assert len(prepared) <= 2 + 1
        return prepared

    try:
        with _connected(deployed["DATABASES"]["default"]["OPTIONS"]):
            assert connection.connection.prepared_max == 2
            for alias in ("first", "second", "third"):
                for _ in range(PREPARE_THRESHOLD + 1):
                    prepared = run(alias)
            assert prepared == ["first", "second", "third"]
            assert run("fourth") == ["second", "third"]
            # The next checkout, from the pool or a new connection, is bounded again.
            connection.connection.prepared_max = 100
            connection.close()
            connection.ensure_connection()
            assert connection.connection.prepared_max == 2
    finally:
        connection_created.disconnect(dispatch_uid=PREPARED_STATEMENTS_MAX_UID)


def test_a_foreign_key_column_is_added_and_dropped_with_the_setting_on(
    transactional_db: None, monkeypatch: Any,
) -> None:
    """Django's schema editor adds a foreign-key column, and drops its constraint, with two commands in one
    statement (``ALTER TABLE … REFERENCES …; SET CONSTRAINTS … IMMEDIATE``). PostgreSQL cannot prepare two
    commands, so they must stay unprepared, also when the same text runs again on the connection."""

    del transactional_db

    class Target(models.Model):
        class Meta:
            app_label = "auth"
            db_table = "auth_prepared_fk_target"

    class Source(models.Model):
        class Meta:
            app_label = "auth"
            db_table = "auth_prepared_fk_source"

    class SourceWithTarget(models.Model):
        target = models.ForeignKey(Target, null=True, on_delete=models.CASCADE, related_name="+")

        class Meta:
            app_label = "auth"
            db_table = "auth_prepared_fk_source"

    def columns() -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, Source._meta.db_table)
        return {column.name for column in description}

    target = SourceWithTarget._meta.get_field("target")
    options = _deployed(monkeypatch, ANGEE_DB_PREPARED_STATEMENTS="true")["DATABASES"]["default"]["OPTIONS"]
    with model_tables((Target, Source)), _connected(options):
        for _ in range(2):
            with connection.schema_editor() as editor:
                editor.add_field(Source, target)
            assert "target_id" in columns()
            with connection.schema_editor() as editor:
                editor.remove_field(Source, target)
            assert "target_id" not in columns()


@pytest.fixture
def drive(tmp_path: Path, transactional_db: None) -> Any:
    del transactional_db
    with system_context(reason="test prepared statements drive"):
        backend = Backend._base_manager.create(
            slug="local",
            label="Local",
            backend_class="local",
            backend_config={"root": str(tmp_path), "base_url": "/media/"},
        )
        return Drive._base_manager.create(backend=backend, slug="assets", name="Assets", prefix="assets")


def test_a_create_rule_check_binds_with_the_setting_on(drive: Any, monkeypatch: Any) -> None:
    """``storage/drive#create`` admits an admin and denies a stranger, its parameters bound by the server."""

    admin = create_platform_admin(username="prepared-create-admin")
    stranger = get_user_model().objects.create_user(username="prepared-create-stranger", email="stranger@example.com")
    options = _deployed(monkeypatch, ANGEE_DB_PREPARED_STATEMENTS="true")["DATABASES"]["default"]["OPTIONS"]
    with _connected(options):
        with actor_context(admin):
            created = Drive._default_manager.create(backend=drive.backend, slug="admin-drive", name="Admin Drive")
        assert created.pk is not None
        with actor_context(stranger), pytest.raises(PermissionDenied):
            Drive._default_manager.create(backend=drive.backend, slug="stranger-drive", name="Stranger Drive")


def test_a_point_check_binds_with_the_setting_on(transactional_db: None, monkeypatch: Any) -> None:
    """A recursive role check (``includes->effective_member``) is one compiled point statement whose
    resource id the server binds: it admits the platform admin and denies a stranger."""

    del transactional_db
    admin = create_platform_admin(username="prepared-point-admin")
    stranger = get_user_model().objects.create_user(username="prepared-point-stranger", email="point@example.com")
    role = ObjectRef("storage/role", "storage_admin")
    options = _deployed(monkeypatch, ANGEE_DB_PREPARED_STATEMENTS="true")["DATABASES"]["default"]["OPTIONS"]
    with _connected(options):
        assert check_permission("effective_member", role, actor=admin).allowed
        assert not check_permission("effective_member", role, actor=stranger).allowed
