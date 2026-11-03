"""Source state and capture attribution have independent native history fields."""

import json

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from simple_history.utils import bulk_update_with_history

from angee.provenance.context import changeset, current_changeset
from angee.provenance.engine.exceptions import ReplayRefused
from tests.provenance_engine.capture_app.models import DomainChangesetNote
from tests.provenance_engine.storage.models import Changeset

pytestmark = pytest.mark.django_db(transaction=True)


def test_domain_changeset_survives_save_bulk_capture_export_and_inverse(actor):
    with changeset(actor=actor):
        row = DomainChangesetNote.objects.create(changeset="domain-before", title="before")
    with changeset(actor=actor):
        row.changeset = "domain-after"
        row.title = "after"
        row.save(update_fields=["changeset", "title"])
        boundary = current_changeset()
    historical = row.history.first()
    assert historical.changeset == "domain-after"
    assert historical.history_changeset_id == boundary.pk
    assert historical.history_source_complete is True
    document = json.loads(boundary.document(actor=actor))
    values = document["history"][row._meta.label_lower][0]
    assert values["changeset"] == "domain-after"
    assert values["history_changeset"] == boundary.pk
    assert boundary in Changeset.objects.touching(row)
    inverse = boundary.revert(actor=actor)
    row.refresh_from_db()
    assert (row.changeset, row.title) == ("domain-before", "before")
    inverse.revert(actor=actor)
    row.refresh_from_db()
    assert (row.changeset, row.title) == ("domain-after", "after")
    with changeset(actor=actor):
        row.changeset = "domain-bulk"
        bulk_update_with_history([row], DomainChangesetNote, ["changeset"])
    assert row.history.first().changeset == "domain-bulk"
    with changeset(actor=actor):
        DomainChangesetNote.objects.filter(pk=row.pk).update(changeset="domain-queryset")
    assert row.history.first().changeset == "domain-queryset"


@pytest.mark.parametrize("incomplete", ["current", "previous"])
def test_incomplete_source_state_refuses_lossless_diff_and_replay(actor, incomplete):
    with changeset(actor=actor):
        row = DomainChangesetNote.objects.create(changeset="before", title="before")
    with changeset(actor=actor):
        row.title = "after"
        row.save()
        boundary = current_changeset()
    version = row.history.first() if incomplete == "current" else row.history.last()
    row.history.filter(history_id=version.history_id).update(history_source_complete=False)
    latest = row.history.first()
    assert latest.title == "after"  # Retained snapshots are still explicitly queryable.
    if incomplete == "current":
        with pytest.raises(ReplayRefused, match="incomplete"):
            boundary.document(actor=actor)
    else:
        assert boundary.document(actor=actor)
    owner = DomainChangesetNote.history.model._provenance_history
    assert not owner.source_state_complete(latest, row.history.last())
    with pytest.raises(ReplayRefused, match="incomplete"):
        owner.diff(latest, latest)
    with pytest.raises(ReplayRefused, match="incomplete"):
        boundary.revert(actor=actor)
    row.refresh_from_db()
    assert row.title == "after"


def test_migration_retains_capture_without_fabricating_lost_source_changeset():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_target = [("capture_test", "0005_attributednote_historicalattributednote")]
    executor.migrate(old_target)
    try:
        old = executor.loader.project_state(old_target).apps
        receipts = old.get_model("provenance", "Changeset")
        source = receipts.objects.create(state="closed", outcome="success")
        capture = receipts.objects.create(state="closed", outcome="success")
        attributed = old.get_model("capture_test", "AttributedNote")
        row = attributed.objects.create(title="retained", changeset_id=source.pk)
        history = old.get_model("capture_test", "HistoricalAttributedNote")
        from django.utils import timezone

        version = history.objects.create(
            id=row.pk,
            title="retained",
            changeset_id=capture.pk,
            history_type="~",
            history_date=timezone.now(),
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        current = executor.loader.project_state(latest).apps
        retained = current.get_model("capture_test", "HistoricalAttributedNote").objects.get(pk=version.pk)
        assert retained.history_changeset_id == capture.pk
        assert retained.changeset_id is None
        assert retained.history_source_complete is False
        assert current.get_model("capture_test", "AttributedNote").objects.get(pk=row.pk).changeset_id == source.pk
    finally:
        MigrationExecutor(connection).migrate(latest)
