"""Behavior of the shared append-only queryset and Django collector writes."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.migrations.writer import MigrationWriter
from rebac import system_context

from angee.base.mixins import AppendOnlyQuerySet, AuditMixin, audit_set_null
from angee.base.models import AngeeQuerySet
from tests.conftest import create_user


class RetainedEvidenceQuerySet(AppendOnlyQuerySet["RetainedEvidence"], AngeeQuerySet["RetainedEvidence"]):
    """Compose append-only policy with the ordinary authorization chain."""


class RetainedEvidence(AuditMixin, models.Model):
    """Minimal audited row exercising the shared collection policy."""

    name = models.CharField(max_length=64)
    objects = models.Manager.from_queryset(RetainedEvidenceQuerySet)()

    class Meta:
        app_label = "base"
        db_table = "test_append_only_evidence"
        base_manager_name = "objects"


def test_audit_foreign_keys_declare_serializable_materialized_nullification() -> None:
    """Both audit fields retain the stable collector policy in migration state."""

    for name in ("created_by", "updated_by"):
        on_delete = RetainedEvidence._meta.get_field(name).remote_field.on_delete
        assert on_delete is audit_set_null
        serialized, imports = MigrationWriter.serialize(on_delete)
        assert serialized == "angee.base.mixins.audit_set_null"
        assert imports == {"import angee.base.mixins"}


def test_append_only_rejects_collection_mutation_and_collector_nullifies_audit_fks(
    transactional_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit FK policy bypasses a queryset whose update path stays closed."""

    del transactional_db
    with pytest.raises(ValidationError, match="contenttypes.ContentType rows cannot be edited"):
        AppendOnlyQuerySet(model=ContentType).update(created_by=None)
    actor = create_user("append-only-auditor")
    other_actor = create_user("append-only-other-auditor")
    with system_context(reason="test append-only policy"):
        first = RetainedEvidence.objects.create(name="first", created_by=actor, updated_by=actor)
        second, outside = RetainedEvidence.objects.bulk_create(
            RetainedEvidence(name=name, created_by=user, updated_by=user)
            for name, user in (("second", actor), ("outside", other_actor))
        )
        selected = RetainedEvidence.objects.filter(pk__in=[first.pk, second.pk])
        with pytest.raises(ValidationError, match="edited"):
            selected.update(name="changed")
        with pytest.raises(ValidationError, match="edited"):
            selected.update(created_by=actor)
        with pytest.raises(ValidationError, match="edited"):
            selected.update(created_by=None)
        with pytest.raises(ValidationError, match="edited"):
            selected.update(created_by=None, name="changed")
        with pytest.raises(ValidationError, match="edited"):
            selected.bulk_update([first], ["name"])
        with pytest.raises(ValidationError, match="edited"):
            selected.bulk_update([first], ["created_by"])
        with pytest.raises(ValidationError, match="edited"):
            selected.bulk_create(
                [RetainedEvidence(pk=first.pk, name="changed")],
                update_conflicts=True,
                update_fields=["name"],
                unique_fields=["pk"],
            )
        with pytest.raises(ValidationError, match="edited"):
            selected.bulk_create([], ignore_conflicts=True)
        with pytest.raises(ValidationError, match="deleted"):
            selected.delete()
        with pytest.raises(ValidationError, match="deleted"):
            selected._raw_delete("default")

        first.created_by = None
        with pytest.raises(ValidationError, match="edited"):
            selected.bulk_update([first], ["created_by"])

        def reject_update(*args: Any, **kwargs: Any) -> int:
            del args, kwargs
            raise AssertionError("Django's deletion collector must bypass QuerySet.update().")

        monkeypatch.setattr(RetainedEvidenceQuerySet, "update", reject_update)
        actor.delete()
        first.refresh_from_db()
        second.refresh_from_db()
        outside.refresh_from_db()
        assert (first.name, second.name, outside.name) == ("first", "second", "outside")
        assert first.created_by_id is first.updated_by_id is None
        assert second.created_by_id is second.updated_by_id is None
        assert outside.created_by_id == outside.updated_by_id == other_actor.pk


def test_owner_writes_preserve_downstream_queryset_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner admission never skips a later queryset's authorization or policy."""

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise PermissionDenied("downstream write guard")

    monkeypatch.setattr(AngeeQuerySet, "update", deny)
    monkeypatch.setattr(AngeeQuerySet, "bulk_create", deny)
    queryset = RetainedEvidence.objects.filter(pk=1)
    with pytest.raises(PermissionDenied, match="downstream write guard"):
        queryset.owner_update(name="changed")
    with pytest.raises(PermissionDenied, match="downstream write guard"):
        queryset.owner_bulk_create([RetainedEvidence(name="new")])
