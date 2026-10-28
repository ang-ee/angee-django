"""Django retained-FK collection keeps the selected model's native capture contract."""

from __future__ import annotations

from typing import Any

from django.db import models
from rebac import system_context

from angee.base.models.audit import AuditMixin
from angee.provenance.history import HistoryMixin
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
