"""Framework-generic fixtures requiring pytest and pytest-django."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db.models.signals import post_migrate
from pytest_django.django_compat import is_django_unittest
from pytest_django.plugin import DjangoDbBlocker

from angee.changesets import changeset
from angee.compose.apps import ComposeConfig
from angee.testing.permissions import composed_permissions as composed_permissions


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker a module uses to exercise production boundaries itself."""

    config.addinivalue_line(
        "markers",
        "entrypoint_boundaries: the module opens its own changeset boundaries; the autouse test boundary stays closed",
    )


@pytest.fixture(scope="session")
def django_db_setup(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[None]:
    """Install the host's permission schema and restore it after transaction flushes.

    Native Django database setup owns table creation. A composed host gets the
    same REBAC lifecycle as a provisioned application. Bare source-model tests
    own their per-test schemas through ``composed_permissions`` instead.
    """

    if not any(isinstance(config, ComposeConfig) for config in apps.get_app_configs()):
        yield
        return

    rebac_app = apps.get_app_config("rebac")

    def sync_rebac_schema(**kwargs: Any) -> None:
        call_command("rebac", "sync", verbosity=0)

    post_migrate.connect(sync_rebac_schema, sender=rebac_app, weak=False)
    try:
        with django_db_blocker.unblock():
            sync_rebac_schema()
        yield
    finally:
        post_migrate.disconnect(sync_rebac_schema, sender=rebac_app)


@pytest.fixture(autouse=True)
def captured_changeset(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give every test one attribution boundary, as an entrypoint gives a request or a command.

    Strict mode refuses a captured write without an open changeset, so a host that
    loads this plugin gets the boundary with it. The boundary declares no activity,
    so a seed load or any other explicit inner boundary refines it instead of
    conflicting. It depends on the test's database fixture so it is torn down, and
    its changeset closed, while database access is still allowed. It is published to
    the process (``process=True``): a thread or pool the test starts writes under it
    in its own row, as under a management command. Modules marked
    ``entrypoint_boundaries`` exercise the production entrypoints' own boundaries and
    run without this one.
    """

    if request.node.get_closest_marker("entrypoint_boundaries"):
        yield
        return
    names = set(request.fixturenames)
    if not is_django_unittest(request) and (
        request.node.get_closest_marker("django_db") or {"db", "transactional_db", "composed_tables"} & names
    ):
        request.getfixturevalue("transactional_db" if {"transactional_db", "composed_tables"} & names else "db")
    with changeset(fresh=True, change_reason="pytest", process=True):
        yield


@pytest.fixture(autouse=True)
def restore_composed_permission_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore generated permission paths and source annotations after each test."""
    for config in apps.get_app_configs():
        for key in ("rebac_schema", "_angee_rebac_schema_source", "_angee_rebac_schema_effective"):
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
