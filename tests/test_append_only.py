"""Behavior of the shared append-only queryset and its audit exception."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection, models
from rebac import system_context

from angee.base.mixins import AppendOnlyQuerySet, AuditMixin
from angee.base.models import AngeeQuerySet
from tests.conftest import _clear_model_tables, _create_missing_tables, create_user


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


def test_append_only_allows_inserts_and_only_audit_nullification(transactional_db: Any) -> None:
    """Native inserts work; edits/deletion fail; clearing audit actors stays scoped."""

    del transactional_db
    with pytest.raises(ValidationError, match="contenttypes.ContentType rows cannot be edited"):
        AppendOnlyQuerySet(model=ContentType).update(created_by=None)
    created = _create_missing_tables((RetainedEvidence,))
    try:
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
            actor.delete()
            first.refresh_from_db()
            second.refresh_from_db()
            outside.refresh_from_db()
            assert (first.name, second.name, outside.name) == ("first", "second", "outside")
            assert first.created_by_id is first.updated_by_id is None
            assert second.created_by_id is second.updated_by_id is None
            assert outside.created_by_id == outside.updated_by_id == other_actor.pk
    finally:
        _clear_model_tables((RetainedEvidence,))
        if created:
            with connection.schema_editor() as editor:
                editor.delete_model(RetainedEvidence)
