"""Foundation permission fixtures requiring pytest and pytest-django."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db.models.signals import post_migrate
from pytest_django.plugin import DjangoDbBlocker

from angee.iam.testing.permissions import emit_permissions, installed_field_owners

if TYPE_CHECKING:
    from angee.iam.testing.graphql import GraphQLDocumentRead


@pytest.fixture(scope="session")
def django_db_setup(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[None]:
    """Install the host's permission schema and restore it after transaction flushes.

    Native Django database setup owns table creation. Synchronize the host
    schema once and after each transactional test flush. Tests that bind their
    own schema use ``composed_permissions`` for the additional sync.
    """

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
    from angee.iam.permissions import _SCHEMA_BINDING

    for config in apps.get_app_configs():
        for key in ("rebac_schema", _SCHEMA_BINDING):
            existed = key in config.__dict__
            monkeypatch.setitem(config.__dict__, key, config.__dict__.get(key))
            if not existed:
                del config.__dict__[key]


@pytest.fixture()
def composed_tables(db: None) -> None:
    """Use Django's savepoint-isolated tables and session-synchronized permissions.

    Tests requiring separate connections or commit visibility declare
    ``django_db(transaction=True)``; pytest-django then selects its transactional
    database fixture and the post-migrate hook restores the REBAC schema.
    """

    del db


@pytest.fixture
def composed_permissions(composed_tables: None, restore_composed_permission_bindings: None, tmp_path: Path) -> None:
    """Bind installed contributions and let native sync persist their schema."""

    from angee.iam.build import extension_source_map

    configs = list(apps.get_app_configs())
    sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    runtime = tmp_path / "permissions"
    emit_permissions(configs, runtime, sources=sources)
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)


@pytest.fixture
def capture_graphql_document() -> Callable[..., GraphQLDocumentRead]:
    """Expose the shipped document guard to framework and consumer tests."""

    from angee.iam.testing.graphql import measure_graphql_document

    return measure_graphql_document
