"""Django retained-FK collection keeps the selected model's native capture contract."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from rebac import system_context

from angee.base.models.audit import AuditMixin
from angee.base.querysets import ClosedUpdateQuerySet
from angee.provenance.history import HistoryMixin
from angee.provenance.querysets import HistoryQuerySet
from tests.conftest import create_user


class CapturedAuditReference(HistoryMixin, AuditMixin):
    """Plain Django capture does not require an Angee record or REBAC resource."""

    name = models.CharField(max_length=40)

    class Meta:
        app_label = "scopedemo"
        db_table = "test_captured_audit_reference"


def test_collector_nullification_captures_each_persisted_audit_state(composed_tables: Any) -> None:
    """Deleting an auditor must retain the row and record both native FK updates."""

    del composed_tables
    actor = create_user("captured-retained-auditor")
    with system_context(reason="test audit collector capture"):
        row = CapturedAuditReference.objects.create(name="retained", created_by=actor, updated_by=actor)
        assert row.history.count() == 1
        actor.delete()
        row.refresh_from_db()
        assert row.name == "retained"
        assert row.created_by_id is row.updated_by_id is None
        versions = list(row.history.order_by("history_id"))
        assert [version.history_type for version in versions] == ["+", "~", "~"]
        assert versions[0].created_by_id == versions[0].updated_by_id
        assert (versions[-1].name, versions[-1].created_by_id, versions[-1].updated_by_id) == (
            "retained", None, None,
        )


class ClosedCapturedQuerySet(ClosedUpdateQuerySet, HistoryQuerySet):
    """Close generic updates before the native capture owner."""


class ClosedCapturedAuditReference(HistoryMixin, AuditMixin):
    name = models.CharField(max_length=40)
    objects = ClosedCapturedQuerySet.as_manager()

    class Meta:
        app_label = "scopedemo"
        db_table = "test_closed_captured_audit_reference"


class ClosedAuditReference(AuditMixin):
    name = models.CharField(max_length=40)
    objects = ClosedUpdateQuerySet.as_manager()

    class Meta:
        app_label = "scopedemo"
        db_table = "test_closed_audit_reference"
        base_manager_name = "objects"


@pytest.mark.parametrize("model", [ClosedAuditReference, ClosedCapturedAuditReference])
def test_closed_update_policy_retains_native_audit_nullification(model, composed_tables: Any) -> None:
    """Collector-owned nulls pass the guard and keep capture when selected."""

    del composed_tables
    actor = create_user("closed-retained-auditor")
    with system_context(reason="test closed audit collector"):
        row = model.objects.create(name="retained", created_by=actor, updated_by=actor)
        with pytest.raises(ValidationError, match="cannot be edited"):
            model.objects.none().update(name="forbidden")
        with pytest.raises(ValidationError, match="cannot be edited"):
            model.objects.filter(pk=row.pk).update(created_by=None)
        actor.delete()
        row.refresh_from_db()
        assert (row.name, row.created_by_id, row.updated_by_id) == ("retained", None, None)
        if model is ClosedCapturedAuditReference:
            versions = list(row.history.order_by("history_id"))
            assert [version.history_type for version in versions] == ["+", "~", "~"]
            assert versions[0].created_by_id == versions[0].updated_by_id
            assert (versions[-1].name, versions[-1].created_by_id, versions[-1].updated_by_id) == (
                "retained", None, None,
            )
