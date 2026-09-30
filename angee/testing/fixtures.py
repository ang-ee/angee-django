"""Framework-generic fixtures requiring pytest and pytest-django."""

import pytest
from django.apps import AppConfig, apps
from django.core.management import call_command
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import StateApps


@pytest.fixture
def permission_app_configs() -> list[AppConfig]:
    """Select installed addons whose permission extensions the test host composes."""

    return list(apps.get_app_configs())


@pytest.fixture
def historical_rebac_models() -> StateApps:
    """Use native pre-index models to represent policy written before an upgrade."""

    return MigrationLoader(None).project_state([("rebac", "0006_schema_write_owners")]).apps


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
