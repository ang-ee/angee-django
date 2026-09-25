"""Tests for system-owned query elevation and row locking."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import FieldError
from django.db import connection, models, transaction
from django.db.models import OuterRef
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import RebacMixin

from angee.base.models import AngeeManager, AngeeModel, AngeeQuerySet, AngeeUnscopedManager, AngeeUnscopedQuerySet
from angee.base.scoping import system_queryset
from angee.testing.models import StepAttempt, StepRun
from angee.workflows.managers import StepAttemptQuerySet
from tests.conftest import Drive, File, Integration
from tests.tables import model_tables

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


class GuardedSystemQuerySet(AngeeQuerySet["GuardedSystemQueryThing"]):
    """Test-local queryset whose write rule must survive system elevation."""

    def update(self, **kwargs: object) -> int:
        """Reject the guarded field through every supported queryset ingress."""

        if "name" in kwargs:
            raise TypeError("name is guarded")
        return super().update(**kwargs)


class GuardedSystemManager(AngeeManager.from_queryset(GuardedSystemQuerySet)):  # type: ignore[misc]
    """Keep a manager-owned base predicate on elevated querysets."""

    def get_queryset(self) -> GuardedSystemQuerySet:
        """Expose only manager-selected rows without changing their queryset type."""

        return super().get_queryset().filter(selected=True)


class GuardedSystemQueryThing(AngeeModel):
    """Concrete model proving system paths preserve domain queryset ownership."""

    name = models.CharField(max_length=32)
    selected = models.BooleanField(default=True)

    objects = GuardedSystemManager()

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


@pytest.mark.parametrize("model", [Drive, File, Integration, StepRun, StepAttempt])
def test_locking_base_managers_preserve_unscoped_reads_and_default_owners(model: type[models.Model]) -> None:
    """Native base managers expose backend-gated locks without replacing actor managers."""

    manager = model._base_manager
    assert model._default_manager is model.objects
    assert callable(manager.lock_if_supported)
    queryset = manager.db_manager("default").lock_if_supported(of=("self",))
    assert queryset.model is model
    assert queryset._db == "default"
    assert queryset.query.select_for_update is True
    assert queryset.query.select_for_update_of == ("self",)
    if model is StepAttempt:
        assert manager is model.system_objects
        assert isinstance(queryset, StepAttemptQuerySet)
        with pytest.raises(TypeError, match="Step attempts do not support collection updates"):
            queryset.update(status="bypassed")
    else:
        assert isinstance(manager, AngeeUnscopedManager)
        assert isinstance(queryset, AngeeUnscopedQuerySet)


@pytest.mark.parametrize("queryset_class", [AngeeQuerySet, AngeeUnscopedQuerySet])
def test_lock_preserves_queryset_policy_and_declares_native_write_intent(queryset_class: Any) -> None:
    """Lock intent preserves the queryset and hints without mutating the original."""

    instance = SystemQueryThing(name="pending")
    original = queryset_class(model=SystemQueryThing, hints={"instance": instance}).filter(name="pending")
    locked = original.lock_if_supported(of=("self",))

    assert type(locked) is queryset_class
    assert locked is not original
    assert locked.query.where == original.query.where
    assert locked._hints == original._hints
    assert original._for_write is False
    assert original.query.select_for_update is False
    assert locked._for_write is True
    assert locked.query.select_for_update is True
    assert locked.query.select_for_update_of == ("self",)


@pytest.fixture
def system_query_tables() -> Iterator[None]:
    """Create the concrete system-query test tables."""

    with model_tables((SystemQueryThing, GuardedSystemQueryThing, ThirdPartySystemQueryThing)):
        yield


@POSTGRESQL_ONLY
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("model", [SystemQueryThing, ThirdPartySystemQueryThing])
def test_system_queryset_emits_for_update_on_postgresql(system_query_tables: None, model: type[models.Model]) -> None:
    """A requested system lock reaches PostgreSQL as ``FOR UPDATE`` SQL."""

    instance = system_queryset(model).create(name="locked")

    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        rows = list(system_queryset(model, lock=("self",)).filter(pk=instance.pk))

    assert rows == [instance]
    assert any("FOR UPDATE" in query["sql"].upper() for query in captured.captured_queries)


@override_settings(REBAC_ALLOW_SUDO=False)
@pytest.mark.django_db(transaction=True)
def test_system_querysets_ignore_the_user_sudo_toggle(system_query_tables: None) -> None:
    """Angee and third-party system paths remain elevated when user sudo is disabled."""

    assert SystemQueryThing.system_queryset().count() == 0
    assert system_queryset(ThirdPartySystemQueryThing).count() == 0


@pytest.mark.django_db(transaction=True)
def test_system_querysets_preserve_domain_queryset_and_manager_policy(system_query_tables: None) -> None:
    """Elevation bypasses REBAC without discarding the model's collection owner."""

    selected = GuardedSystemQueryThing._base_manager.create(name="selected", selected=True)
    GuardedSystemQueryThing._base_manager.create(name="excluded", selected=False)

    owned = GuardedSystemQueryThing.system_queryset()
    adapted = system_queryset(GuardedSystemQueryThing)

    assert isinstance(owned, GuardedSystemQuerySet)
    assert isinstance(adapted, GuardedSystemQuerySet)
    assert list(owned) == [selected]
    assert list(adapted) == [selected]
    with pytest.raises(TypeError, match="name is guarded"):
        owned.update(name="bypassed")
    with pytest.raises(TypeError, match="name is guarded"):
        adapted.update(name="bypassed")


@pytest.mark.django_db(transaction=True)
def test_unscoped_locks_keep_native_base_manager_visibility(system_query_tables: None) -> None:
    """Adding locks must not hide rows excluded by the default manager's policy."""

    selected = GuardedSystemQueryThing._base_manager.create(name="selected", selected=True)
    excluded = GuardedSystemQueryThing._base_manager.create(name="excluded", selected=False)
    manager = AngeeUnscopedManager()
    manager.model = GuardedSystemQueryThing

    with transaction.atomic():
        queryset = manager.db_manager("default").lock_if_supported()
        assert set(queryset.values_list("pk", flat=True)) == {selected.pk, excluded.pk}
        assert manager.db_manager("default").locked_get(pk=excluded.pk) == excluded

    assert type(GuardedSystemQueryThing._base_manager) is models.Manager
    assert list(GuardedSystemQueryThing.system_queryset()) == [selected]


@pytest.mark.django_db(transaction=True)
def test_readable_scalar_subquery_infers_fallback_type(system_query_tables: None) -> None:
    """A fallback keeps the selected field type even when its Python value differs."""

    expression = SystemQueryThing.objects.filter(pk=OuterRef("pk")).readable_scalar_subquery("pk", default=0)

    assert expression.output_field is SystemQueryThing._meta.pk
    try:
        expression.resolve_expression(SystemQueryThing.objects.all().query)
    except FieldError as error:  # pragma: no cover - assertion gives the useful failure
        pytest.fail(f"fallback produced incompatible Django expression types: {error}")


@pytest.mark.django_db(transaction=True)
def test_readable_scalar_subquery_keeps_denied_empty_string(system_query_tables: None) -> None:
    """With no actor, an explicitly requested text fallback remains observable."""

    row = SystemQueryThing._base_manager.create(name="private")
    projected = SystemQueryThing._base_manager.annotate(
        readable_name=SystemQueryThing.objects.filter(pk=OuterRef("pk")).readable_scalar_subquery(
            "name",
            default="",
        )
    ).get(pk=row.pk)

    assert projected.readable_name == ""


@SQLITE_ONLY
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("model", [SystemQueryThing, ThirdPartySystemQueryThing])
def test_system_queryset_keeps_sqlite_unlocked(system_query_tables: None, model: type[models.Model]) -> None:
    """SQLite evaluates a requested system lock without emitting lock SQL."""

    instance = system_queryset(model).create(name="unlocked")

    with CaptureQueriesContext(connection) as captured:
        rows = list(system_queryset(model, lock=("self",)).filter(pk=instance.pk))

    assert rows == [instance]
    assert all("FOR UPDATE" not in query["sql"].upper() for query in captured.captured_queries)
