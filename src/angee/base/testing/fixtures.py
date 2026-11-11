"""Foundation registry and permission fixtures requiring pytest and pytest-django."""

import gc
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.apps import AppConfig, apps
from django.conf import settings
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.fields.related_descriptors import ReverseManyToOneDescriptor, ReverseOneToOneDescriptor
from django.db.models.signals import post_migrate
from pytest_django.plugin import DjangoDbBlocker
from rebac.backends import reset_backend

from angee.base.permissions import SCHEMA_BINDING
from angee.base.testing.permissions import composed_permissions as composed_permissions
from angee.base.testing.permissions import emit_permissions

type Rows = tuple[tuple[str, str, list[tuple[Any, ...]]], ...]


@contextmanager
def installed_app_registry_restored() -> Iterator[None]:
    """Return Django's installed app registry to its state before the block.

    Django cannot unregister a model, so a model class a test creates on an installed
    app label would stay in ``apps.all_models`` for every later test in the process,
    where system checks, the deletion collector and resource-type lookups meet it in
    file order. A model from a module already imported when the block began was
    created by the block and is unregistered, with its auto-created through models; a
    module first imported in the block keeps its models registered, as its import does.
    Installed app configs the block adds, replaces or removes are put back, as
    are reverse descriptors installed on surviving models by removed relations.
    """

    registered = {label: dict(entries) for label, entries in apps.all_models.items()}
    descriptors = {
        model: {
            name: value for name, value in vars(model).items()
            if isinstance(value, ReverseManyToOneDescriptor | ReverseOneToOneDescriptor)
        }
        for entries in registered.values() for model in entries.values()
    }
    configs = dict(apps.app_configs)
    imported = set(sys.modules)
    try:
        yield
    finally:
        removed = set()
        restored = apps.app_configs != configs
        if restored:
            apps.app_configs.clear()
            apps.app_configs.update(configs)
        for label, entries in apps.all_models.items():
            kept = registered.get(label, {}) | {
                name: model
                for name, model in entries.items()
                if model.__module__ in sys.modules and model.__module__ not in imported
            }
            if entries != kept:
                removed.update(set(entries.values()) - set(kept.values()))
                entries.clear()
                entries.update(kept)
                restored = True
        for entries in apps.all_models.values():
            for model in entries.values():
                for name, value in tuple(vars(model).items()):
                    if isinstance(value, ReverseOneToOneDescriptor):
                        related_model = value.related.related_model
                    elif isinstance(value, ReverseManyToOneDescriptor):
                        related_model = value.rel.related_model
                    else:
                        continue
                    if related_model in removed:
                        previous = descriptors.get(model, {})
                        if name in previous:
                            setattr(model, name, previous[name])
                        else:
                            delattr(model, name)
        if restored:
            apps.clear_cache()


@pytest.fixture(autouse=True)
def installed_app_registry() -> Iterator[None]:
    """Give every test the installed app registry it started with."""

    with installed_app_registry_restored():
        yield


@pytest.fixture
def monkeypatch(installed_app_registry: None) -> Iterator[pytest.MonkeyPatch]:
    """Undo native monkeypatches before the installed model registry is restored."""

    with pytest.MonkeyPatch.context() as patch:
        yield patch


SESSION_BASELINE = pytest.StashKey[Rows]()


def prime_content_types() -> None:
    """Warm Django's content-type cache for every installed model, as every test starts with it."""

    content_types = apps.get_model("contenttypes", "ContentType").objects
    content_types.clear_cache()
    content_types.get_for_models(*apps.get_models(), for_concrete_models=False)


def database_rows(connection: BaseDatabaseWrapper) -> Rows:
    """Return each table a flush clears that holds rows, with an ``INSERT`` statement and its parameters."""

    quote = connection.ops.quote_name
    rows = []
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.django_table_names(only_existing=True, include_views=False)):
            cursor.execute(f"SELECT * FROM {quote(table)}")
            if values := cursor.fetchall():
                columns = [column[0] for column in cursor.description]
                rows.append((
                    quote(table),
                    f"INSERT INTO {quote(table)} ({', '.join(map(quote, columns))}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})",
                    values,
                ))
    return tuple(rows)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[Any], request: pytest.FixtureRequest,
) -> Generator[None, Any, Any]:
    """Record the default database as each ``django_db_setup`` layer returns.

    The outermost layer returns last, so the record that stands is the whole session
    baseline, including the rows a host's extension of the fixture loads. The objects
    still alive by then are frozen, so later full collections scan only what tests allocate.
    """

    value = yield
    if fixturedef.argname == "django_db_setup":
        with request.getfixturevalue("django_db_blocker").unblock():
            request.config.stash[SESSION_BASELINE] = database_rows(connections[DEFAULT_DB_ALIAS])
        gc.collect()
        gc.freeze()
    return value


@pytest.fixture(scope="session", autouse=True)
def composed_permission_inputs(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Bind the installed addons' merged permission inputs for the whole session.

    A composed host binds its generated inputs when it starts; a bare source host
    gets the same composition here, before any test reads a declaration or the
    database synchronizes it.
    """

    if getattr(settings, "COMPOSE_SNAPSHOT", None) is None:
        emit_permissions(apps.get_app_configs(), tmp_path_factory.mktemp("permissions"))


@pytest.fixture(scope="session")
def django_db_setup(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
    composed_permission_inputs: None,
    pytestconfig: pytest.Config,
) -> Iterator[None]:
    """Synchronize the session's permission baseline once and restore its rows after transactional flushes.

    Native Django database setup owns table creation. REBAC synchronizes the bound
    inputs with a forced overwrite: a disposable or reused test database has no
    local policy to preserve. A host extends this fixture to load its own baseline
    rows; once its outermost layer returns, every row of the default database is the
    recorded baseline. Tests roll back to it. A transactional test's flush empties
    every table and re-sends ``post_migrate``. At the first app's turn every recorded
    table is emptied again and refilled with the recorded rows, keys included, so the
    later turns' receivers find the rows they converge instead of rebuilding them. At
    the last app's turn the recorded rows are restored once more, undoing what those
    receivers still write (audit rows, reflections the baseline recorded stale), and
    Django's content-type cache and REBAC's process backend start again from them.
    """

    configs = [config for config in apps.get_app_configs() if config.models_module is not None]
    first, last = configs[0], configs[-1]

    def restore(*, sender: AppConfig, using: str, **kwargs: Any) -> None:
        if using != DEFAULT_DB_ALIAS:
            return
        connection, recorded = connections[using], pytestconfig.stash[SESSION_BASELINE]
        with transaction.atomic(using=using), connection.cursor() as cursor:
            for table, _, _ in recorded:
                cursor.execute(f"DELETE FROM {table}")
            for _, statement, values in recorded:
                cursor.executemany(statement, values)
        if sender is last:
            prime_content_types()
            reset_backend()

    with django_db_blocker.unblock():
        call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
        prime_content_types()
    for sender in (first, last):
        post_migrate.connect(restore, sender=sender, weak=False)
    try:
        yield
    finally:
        for sender in (first, last):
            post_migrate.disconnect(restore, sender=sender)


@pytest.fixture(autouse=True)
def restore_composed_permission_bindings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore generated permission paths, source annotations and REBAC's backend after each test.

    A test may install its own schema on the process backend
    (``rebac.testing.install_schema``); the next test reads the stored baseline again.
    """

    for config in apps.get_app_configs():
        for key in ("rebac_schema", SCHEMA_BINDING):
            existed = key in config.__dict__
            monkeypatch.setitem(config.__dict__, key, config.__dict__.get(key))
            if not existed:
                del config.__dict__[key]
    yield
    reset_backend()
