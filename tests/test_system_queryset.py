"""Tests for system-owned query elevation and row locking."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.db import connection, models, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import RebacMixin

from angee.base.models import AngeeModel
from angee.base.scoping import system_queryset

POSTGRESQL_ONLY = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL-only: SQLite does not emit SELECT ... FOR UPDATE.",
)
SQLITE_ONLY = pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="SQLite-only: pins the supported no-row-lock floor.",
)


class SystemQueryThing(AngeeModel):
    """Concrete Angee model used to exercise the system queryset owner."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"


class ThirdPartySystemQueryThing(RebacMixin):
    """REBAC model used to exercise the third-party scoping adapter."""

    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"
        base_manager_name = "objects"


@pytest.fixture
def system_query_tables() -> Iterator[None]:
    """Create the concrete system-query test tables."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(SystemQueryThing)
        schema_editor.create_model(ThirdPartySystemQueryThing)
    try:
        yield
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(ThirdPartySystemQueryThing)
            schema_editor.delete_model(SystemQueryThing)


@POSTGRESQL_ONLY
@pytest.mark.django_db(transaction=True)
def test_system_queryset_emits_for_update_on_postgresql(system_query_tables: None) -> None:
    """A requested system lock reaches PostgreSQL as ``FOR UPDATE`` SQL."""

    instance = SystemQueryThing._base_manager.create(name="locked")

    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        rows = list(SystemQueryThing.system_queryset(lock=()).filter(pk=instance.pk))

    assert rows == [instance]
    assert any("FOR UPDATE" in query["sql"].upper() for query in captured.captured_queries)


@override_settings(REBAC_ALLOW_SUDO=False)
@pytest.mark.django_db(transaction=True)
def test_system_querysets_ignore_the_user_sudo_toggle(system_query_tables: None) -> None:
    """Angee and third-party system paths remain elevated when user sudo is disabled."""

    assert SystemQueryThing.system_queryset().count() == 0
    assert system_queryset(ThirdPartySystemQueryThing).count() == 0


@SQLITE_ONLY
@pytest.mark.django_db(transaction=True)
def test_system_queryset_keeps_sqlite_unlocked(system_query_tables: None) -> None:
    """SQLite evaluates a requested system lock without emitting lock SQL."""

    instance = SystemQueryThing._base_manager.create(name="unlocked")

    with CaptureQueriesContext(connection) as captured:
        rows = list(SystemQueryThing.system_queryset(lock=()).filter(pk=instance.pk))

    assert rows == [instance]
    assert all("FOR UPDATE" not in query["sql"].upper() for query in captured.captured_queries)
