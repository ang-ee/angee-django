"""Foundation permission fixtures requiring pytest and pytest-django."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db.models.signals import post_migrate
from pytest_django.plugin import DjangoDbBlocker

from angee.base.testing.permissions import composed_permissions as composed_permissions


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

    if not getattr(settings, "COMPOSE_RUNTIME_DIR", None):
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
