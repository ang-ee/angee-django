"""Framework-generic fixtures requiring pytest and pytest-django."""

from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic


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


@pytest.fixture
def workflow_permissions(composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Compose installed permission contributions for source-model tests."""
    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs)
    runtime = tmp_path / "permissions"
    for relative, content in sources.items():
        write_atomic(runtime / relative, content)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
