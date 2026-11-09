"""Foundation registry and permission fixtures requiring pytest and pytest-django."""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db.models.signals import post_migrate
from pytest_django.plugin import DjangoDbBlocker

from angee.base.permissions import SCHEMA_BINDING
from angee.base.testing.permissions import composed_permissions as composed_permissions


@contextmanager
def installed_app_registry_restored() -> Iterator[None]:
    """Return Django's installed app registry to its state before the block.

    Django cannot unregister a model, so a model class a test creates on an installed
    app label would stay in ``apps.all_models`` for every later test in the process,
    where system checks, the deletion collector and resource-type lookups meet it in
    file order. A model from a module already imported when the block began was
    created by the block and is unregistered, with its auto-created through models; a
    module first imported in the block keeps its models registered, as its import does.
    Installed app configs the block adds, replaces or removes are put back.
    """

    registered = {label: dict(entries) for label, entries in apps.all_models.items()}
    configs = dict(apps.app_configs)
    imported = set(sys.modules)
    try:
        yield
    finally:
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
                entries.clear()
                entries.update(kept)
                restored = True
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


@pytest.fixture(scope="session")
def django_db_setup(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[None]:
    """Install the host's permission schema and restore it after transaction flushes.

    Native Django database setup owns table creation. A composed host gets the
    current generated REBAC declarations, including on a reused test database.
    Disposable test policy has no local overrides to preserve. Bare source-model
    tests own their per-test schemas through ``composed_permissions`` instead.
    """

    if not getattr(settings, "COMPOSE_OUTPUT_DIR", None):
        yield
        return

    rebac_app = apps.get_app_config("rebac")

    def sync_rebac_schema(**kwargs: Any) -> None:
        call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)

    post_migrate.connect(sync_rebac_schema, sender=rebac_app, weak=False)
    try:
        with django_db_blocker.unblock():
            sync_rebac_schema()
        yield
    finally:
        post_migrate.disconnect(sync_rebac_schema, sender=rebac_app)


@pytest.fixture(autouse=True)
def restore_composed_permission_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore generated permission paths and source annotations after each test."""

    for config in apps.get_app_configs():
        for key in ("rebac_schema", SCHEMA_BINDING):
            existed = key in config.__dict__
            monkeypatch.setitem(config.__dict__, key, config.__dict__.get(key))
            if not existed:
                del config.__dict__[key]


@pytest.fixture()
def composed_tables(transactional_db: None) -> None:
    """Use Django's test tables and synchronize their REBAC permissions.

    ``transactional_db`` delegates table creation and per-test cleanup to
    pytest-django and Django. Permission synchronization runs after native
    database setup and after the previous test's flush.
    """

    del transactional_db
    call_command("rebac", "sync", verbosity=0)
