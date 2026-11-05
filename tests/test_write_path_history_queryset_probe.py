"""Reviewer probe: a HistoryQuerySet.update followed by a stale partial save."""
from __future__ import annotations

import pytest
from rebac import system_context

from tests.tables import model_tables
from tests.test_write_path_probes import RvHistoryProbe

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_tables")]


def test_probe_queryset_update_then_partial_save() -> None:
    with model_tables((RvHistoryProbe, RvHistoryProbe.history.model)):
        with system_context(reason="tests.write_path.qs"):
            row = RvHistoryProbe.objects.create(value="a", note="n1")
            RvHistoryProbe.objects.filter(pk=row.pk).update(note="updated")
            row.value = "b"
            row.save(update_fields={"value"})
            versions = list(
                RvHistoryProbe.history.filter(id=row.pk).order_by("history_id").values_list("value", "note")
            )
            db = RvHistoryProbe._base_manager.filter(pk=row.pk).values_list("value", "note").get()
    print(f"RV queryset-update history versions={versions} db={db}")
    assert versions[-1] == db
