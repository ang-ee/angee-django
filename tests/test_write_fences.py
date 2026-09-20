"""Tests for the base manager-owned write-fence ring."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.test.utils import isolate_apps

from angee.base.mixins import ConditionalSharedReaderQuerySet
from angee.base.writes import (
    ImmutableEvidenceQuerySet,
    WriteFence,
    WriteFencedManagerMixin,
    WriteFencedModel,
    WriteFencedQuerySetMixin,
)
from tests.conftest import _create_missing_tables


class _EvidenceWriteSink:
    """Record cooperative operations admitted by the immutable base."""

    def update(self, **kwargs: Any) -> int:
        self.updated = kwargs
        return 1

    def bulk_create(self, objects: Any, **kwargs: Any) -> list[Any]:
        self.inserted = (objects, kwargs)
        return list(objects)


class _ExceptionalEvidence(ImmutableEvidenceQuerySet[Any], _EvidenceWriteSink):
    """Evidence owner admitting one explicit nullification shape."""

    def validate_evidence_update(self, values: Mapping[str, Any]) -> bool:
        return values == {"actor_id": None}


class _GuardedQuerySet(WriteFencedQuerySetMixin, models.QuerySet[Any]):
    """Queryset carrying the shared bulk-write ring."""


class _GuardedManager(
    WriteFencedManagerMixin,
    models.Manager.from_queryset(_GuardedQuerySet),  # type: ignore[misc]
):
    """Test manager composing the shared exact-instance save helper."""


class GuardedWrite(WriteFencedModel):
    """Concrete row whose only writer is its manager."""

    name = models.CharField(max_length=64)
    write_fence = WriteFence[None](
        "test_guarded_write",
        atomic_error="guarded writes require atomic",
        nested_error="guarded writes cannot nest",
    )
    objects = _GuardedManager()
    system_objects = _GuardedManager()

    class Meta:
        app_label = "tests"
        db_table = "test_base_guarded_write"
        base_manager_name = "system_objects"


def test_immutable_evidence_base_preserves_explicit_owner_exceptions() -> None:
    """Mutations stay closed except for the subclass's declared update shape."""

    evidence = _ExceptionalEvidence()
    with pytest.raises(ValidationError, match="edited"):
        evidence.update(value=1)
    assert evidence.update(actor_id=None) == 1
    assert evidence.updated == {"actor_id": None}
    with pytest.raises(ValidationError, match="deleted"):
        evidence.delete()
    with pytest.raises(ValidationError, match="edited"):
        evidence.bulk_update([], [])
    with pytest.raises(ValidationError, match="edited"):
        evidence.bulk_create([], update_conflicts=True)


def test_policy_querysets_carry_their_write_ingress_owner() -> None:
    """A policy cannot be composed without the ingress methods that invoke it."""

    assert issubclass(ImmutableEvidenceQuerySet, WriteFencedQuerySetMixin)
    assert issubclass(ConditionalSharedReaderQuerySet, WriteFencedQuerySetMixin)


@isolate_apps("tests")
def test_write_fenced_model_check_rejects_an_unfenced_manager_queryset() -> None:
    """Every manager on a concrete fenced model must preserve the write ring."""

    class MissingQuerySetFence(WriteFencedModel):
        name = models.CharField(max_length=64)
        objects = models.Manager()

        class Meta:
            app_label = "tests"

    errors = MissingQuerySetFence.check()

    assert [error.id for error in errors if error.id == "angee.E019"] == ["angee.E019"]


@pytest.fixture()
def guarded_write_table(transactional_db: Any) -> Iterator[None]:
    """Create the standalone write-fence table and remove it after the test."""

    del transactional_db
    created = _create_missing_tables((GuardedWrite,))
    try:
        yield
    finally:
        models.QuerySet(model=GuardedWrite, using=connection.alias)._raw_delete(connection.alias)
        if created:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(GuardedWrite)


def test_manager_save_owns_exact_instance_and_one_use_authority(
    guarded_write_table: None,
) -> None:
    """Direct saves fail while the shared helper admits its exact row once."""

    del guarded_write_table
    row = GuardedWrite(name="owned")
    with pytest.raises(TypeError, match="native manager"):
        row.save()

    with transaction.atomic():
        GuardedWrite.objects._save(row)
    assert row.pk is not None

    row.name = "changed"
    with pytest.raises(TypeError, match="native manager"):
        row.save()
    with transaction.atomic():
        GuardedWrite.objects._save(row, update_fields={"name"})
    assert GuardedWrite.objects.get(pk=row.pk).name == "changed"


def test_queryset_write_ingresses_share_the_model_fence(guarded_write_table: None) -> None:
    """Update, batch insert/update, and delete cannot bypass the native manager."""

    del guarded_write_table
    row = GuardedWrite(name="owned")
    with transaction.atomic():
        GuardedWrite.objects._save(row)

    with pytest.raises(TypeError, match="native manager"):
        GuardedWrite.objects.filter(pk=row.pk).update(name="bypass")
    with pytest.raises(TypeError, match="native manager"):
        GuardedWrite.objects.bulk_create([GuardedWrite(name="bypass")])
    row.name = "bypass"
    with pytest.raises(TypeError, match="native manager"):
        GuardedWrite.objects.bulk_update([row], ["name"])
    with pytest.raises(TypeError, match="native manager"):
        GuardedWrite.objects.filter(pk=row.pk).delete()
    with pytest.raises(TypeError, match="native manager"):
        GuardedWrite.objects.filter(pk=row.pk)._raw_delete(connection.alias)


def test_configured_write_fence_managers_pass_the_model_system_check() -> None:
    """The default and base managers both resolve the guarded queryset type."""

    assert not [error for error in GuardedWrite.check() if error.id == "angee.E019"]
