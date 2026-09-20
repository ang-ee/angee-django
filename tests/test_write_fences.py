"""Tests for the base manager-owned write-fence ring."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.test.utils import isolate_apps

from angee.base.mixins import AuditMixin, ConditionalSharedReaderQuerySet
from angee.base.writes import (
    ImmutableEvidenceQuerySet,
    WriteFence,
    WriteFencedManagerMixin,
    WriteFencedModel,
    WriteFencedQuerySetMixin,
    WriteFenceToken,
)
from tests.conftest import _create_missing_tables, create_user


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


class ConditionalWrite(WriteFencedModel):
    """Row whose native owner fences only its retained form."""

    name = models.CharField(max_length=64)
    retained = models.BooleanField(default=False)
    write_fence = WriteFence[None](
        "test_conditional_write",
        atomic_error="conditional writes require atomic",
        nested_error="conditional writes cannot nest",
    )
    objects = _GuardedManager()
    system_objects = _GuardedManager()

    class Meta:
        app_label = "tests"
        db_table = "test_base_conditional_write"
        base_manager_name = "system_objects"

    def is_write_fenced(
        self,
        operation: str,
        values: Mapping[str, Any],
    ) -> bool:
        """Fence retained rows while leaving ordinary row writes native."""

        del operation, values
        return self.retained

    @classmethod
    def validate_queryset_write_fence(
        cls,
        operation: str,
        *,
        queryset: models.QuerySet[Any],
        objects: tuple[models.Model, ...],
        changed_fields: tuple[str, ...],
        values: Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> None:
        """Fence queryset writes that contain or create a retained row."""

        retained = any(getattr(row, "retained", False) for row in objects)
        retained = retained or values.get("retained") is True
        retained = retained or queryset.filter(retained=True).exists()
        if retained:
            super().validate_queryset_write_fence(
                operation,
                queryset=queryset,
                objects=objects,
                changed_fields=changed_fields,
                values=values,
                options=options,
            )


class OperationErrorWrite(WriteFencedModel):
    """Fenced row with distinct errors for every operation."""

    name = models.CharField(max_length=64)
    objects = _GuardedManager()
    system_objects = _GuardedManager()

    class Meta:
        app_label = "tests"
        base_manager_name = "system_objects"

    @classmethod
    def write_fence_error(cls, operation: str) -> Exception:
        """Expose the normalized operation in the test-facing error."""

        return ValidationError(f"blocked {operation}")


class AuditedWrite(AuditMixin, WriteFencedModel):
    """Fenced row whose audit actors remain nullable by Django's collector."""

    name = models.CharField(max_length=64)
    write_fence = WriteFence[None](
        "test_audited_write",
        atomic_error="audited writes require atomic",
        nested_error="audited writes cannot nest",
    )
    objects = _GuardedManager()
    system_objects = _GuardedManager()

    class Meta:
        app_label = "tests"
        db_table = "test_base_audited_write"
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

    messages = [error.msg for error in errors if error.id == "angee.E019"]
    assert any(".objects must resolve" in message for message in messages)


@isolate_apps("tests")
def test_write_fenced_model_check_rejects_an_unfenced_base_manager() -> None:
    """The collector-facing base manager must preserve the write ring."""

    class MissingBaseManagerFence(WriteFencedModel):
        name = models.CharField(max_length=64)
        objects = _GuardedManager()

        class Meta:
            app_label = "tests"

    messages = [error.msg for error in MissingBaseManagerFence.check() if error.id == "angee.E019"]

    assert len(messages) == 1
    assert "base manager" in messages[0]
    assert "WriteFencedQuerySetMixin" in messages[0]


@isolate_apps("tests")
def test_write_fenced_model_check_rejects_collector_bypass_relations() -> None:
    """Only forward CASCADE and SET_DEFAULT relations bypass every write hook."""

    class Target(models.Model):
        name = models.CharField(max_length=64)

        class Meta:
            app_label = "tests"

    class FencedRelations(WriteFencedModel):
        cascade_target = models.ForeignKey(
            Target,
            on_delete=models.CASCADE,
            related_name="cascade_rows",
        )
        default_target = models.ForeignKey(
            Target,
            on_delete=models.SET_DEFAULT,
            default=1,
            related_name="default_rows",
        )
        nullable_target = models.ForeignKey(
            Target,
            null=True,
            on_delete=models.SET_NULL,
            related_name="nullable_rows",
        )
        protected_target = models.ForeignKey(
            Target,
            on_delete=models.PROTECT,
            related_name="protected_rows",
        )
        many_targets = models.ManyToManyField(Target, related_name="fenced_rows")
        objects = _GuardedManager()
        system_objects = _GuardedManager()

        class Meta:
            app_label = "tests"
            base_manager_name = "system_objects"

    class ReverseCascade(models.Model):
        fenced = models.ForeignKey(FencedRelations, on_delete=models.CASCADE)

        class Meta:
            app_label = "tests"

    class FencedParent(WriteFencedModel):
        name = models.CharField(max_length=64)
        objects = _GuardedManager()
        system_objects = _GuardedManager()

        class Meta:
            app_label = "tests"
            base_manager_name = "system_objects"

    class FencedChild(FencedParent):
        detail = models.CharField(max_length=64)

        class Meta:
            app_label = "tests"

    errors = [error for model in (FencedRelations, FencedChild) for error in model.check() if error.id == "angee.E020"]

    assert {(error.obj.model.__name__, error.obj.name) for error in errors} == {
        ("FencedRelations", "cascade_target"),
        ("FencedRelations", "default_target"),
    }


@pytest.fixture()
def guarded_write_table(transactional_db: Any) -> Iterator[None]:
    """Create standalone write-fence tables and remove them after the test."""

    del transactional_db
    table_models = (GuardedWrite, ConditionalWrite, AuditedWrite)
    created = _create_missing_tables(table_models)
    try:
        yield
    finally:
        for model in reversed(table_models):
            models.QuerySet(model=model, using=connection.alias)._raw_delete(connection.alias)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_write_fence_scope_requires_successful_consumption(transactional_db: Any) -> None:
    """The scope accepts one consumed token and rejects a silent no-op."""

    del transactional_db
    fence = WriteFence[None](
        "test_scope_consumption",
        atomic_error="scope requires atomic",
        nested_error="scope cannot nest",
    )
    consumed = WriteFenceToken(GuardedWrite, "delete", None, None)
    with transaction.atomic(), fence.scope(connection.alias, consumed):
        consumed.consume()

    unconsumed = WriteFenceToken(GuardedWrite, "bulk_create", None, None)
    with (
        transaction.atomic(),
        pytest.raises(
            RuntimeError,
            match="bulk_create write authority was not consumed",
        ),
    ):
        with fence.scope(connection.alias, unconsumed):
            pass


def test_write_fence_scope_preserves_body_exceptions(transactional_db: Any) -> None:
    """An operation failure is never masked by the consumption assertion."""

    del transactional_db
    fence = WriteFence[None](
        "test_scope_exception",
        atomic_error="scope requires atomic",
        nested_error="scope cannot nest",
    )
    token = WriteFenceToken(GuardedWrite, "delete", None, None)

    with transaction.atomic(), pytest.raises(LookupError, match="operation failed"):
        with fence.scope(connection.alias, token):
            raise LookupError("operation failed")
    assert not token.consumed


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


def test_conditional_fence_admits_ordinary_instance_and_queryset_writes(
    guarded_write_table: None,
) -> None:
    """Ordinary writes stay free while matching live authority is still consumed."""

    del guarded_write_table
    ordinary = ConditionalWrite(name="ordinary")
    ordinary.save()
    ordinary.name = "saved"
    with transaction.atomic():
        ConditionalWrite.objects._save(ordinary, update_fields={"name"})
    ConditionalWrite.objects.filter(pk=ordinary.pk).update(name="queryset")
    ordinary.delete()

    retained = ConditionalWrite(name="retained", retained=True)
    with pytest.raises(TypeError, match="native manager"):
        retained.save()
    with transaction.atomic():
        ConditionalWrite.objects._save(retained)
    with pytest.raises(TypeError, match="native manager"):
        ConditionalWrite.objects.filter(pk=retained.pk).update(name="bypass")
    with pytest.raises(TypeError, match="native manager"):
        retained.delete()

    token = WriteFenceToken(type(retained), "delete", id(retained), None)
    with (
        transaction.atomic(),
        ConditionalWrite.write_fence.scope(
            connection.alias,
            token,
        ),
    ):
        retained.delete(connection.alias)


def test_audit_actor_deletion_nullifies_fenced_rows(
    guarded_write_table: None,
) -> None:
    """Django's lazy SET_NULL update passes the shared audit exception."""

    del guarded_write_table
    author = create_user("write-fence-author")
    row = AuditedWrite(
        name="authored",
        created_by=author,
        updated_by=author,
    )
    with transaction.atomic():
        AuditedWrite.objects._save(row)

    author.delete()

    row.refresh_from_db()
    assert row.created_by_id is None
    assert row.updated_by_id is None


def test_write_fence_errors_are_owned_per_normalized_operation() -> None:
    """Instance and queryset ingresses use one operation-aware error owner."""

    row = OperationErrorWrite(pk=1, name="blocked")
    with pytest.raises(ValidationError, match="blocked save"):
        row.save()
    with pytest.raises(ValidationError, match="blocked delete"):
        row.delete()
    with pytest.raises(ValidationError, match="blocked update"):
        OperationErrorWrite.objects.filter(pk=1).update(name="bypass")
    with pytest.raises(ValidationError, match="blocked raw_delete"):
        OperationErrorWrite.objects.filter(pk=1)._raw_delete(connection.alias)


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
