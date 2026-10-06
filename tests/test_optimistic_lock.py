"""Instance-save revisions, including parent-table CAS and writer races."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models.expressions import CombinedExpression
from django.db.models.signals import post_save, pre_save
from django.test.utils import CaptureQueriesContext

from angee.base.mixins import StaleRevisionError, require_revision
from tests.core_persistence import RenamedRevisionRow, RevisionChild, RevisionRow

pytestmark = pytest.mark.django_db
POSTGRES_LOCK = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="PostgreSQL optimistic-lock two-writer contract",
)


@pytest.mark.parametrize("model", [RevisionRow, RevisionChild])
@pytest.mark.parametrize("guarded", [False, True])
def test_instance_save_bumps_the_committed_revision(model, guarded):
    row = model.objects.create(**({"id": 11, "child_id": 77} if model is RevisionChild else {}))
    assert row.revision == 1
    stale = model.objects.get(pk=row.pk)
    row.title = "first"
    row.save(expected_revision=1 if guarded else None)
    assert row.revision == 2
    stale.title = "second"
    if guarded:
        with pytest.raises(StaleRevisionError) as caught:
            stale.save(expected_revision=1)
        assert (caught.value.expected, caught.value.current) == (1, 2)
        assert stale.revision == 1
    else:
        stale.save()
        assert stale.revision == 3
    row.refresh_from_db()
    assert (row.title, row.revision) == (("first", 2) if guarded else ("second", 3))


@pytest.mark.parametrize("guarded", [False, True])
def test_partial_save_bumps_revision_without_writing_other_dirty_fields(guarded):
    row = RevisionRow.objects.create()
    row.title, row.other = "changed", "must not persist"
    row.save(update_fields=["title"], expected_revision=1 if guarded else None)
    assert row.revision == 2
    row.refresh_from_db()
    assert (row.title, row.other, row.revision) == ("changed", "retained", 2)


def test_deferred_save_preserves_unloaded_columns_and_bumps_revision():
    row = RevisionRow.objects.create()
    partial = RevisionRow.objects.only("id", "title").get(pk=row.pk)
    partial.title = "changed"
    partial.save()
    row.refresh_from_db()
    assert (row.title, row.other, row.revision) == ("changed", "retained", 2)


@pytest.mark.parametrize("guarded", [False, True])
def test_a_renamed_counter_keeps_the_revision_contract(guarded):
    assert "revision" not in {field.name for field in RenamedRevisionRow._meta.fields}
    row = RenamedRevisionRow.objects.create()
    stale = RenamedRevisionRow.objects.get(pk=row.pk)
    row.title = "first"
    row.save(update_fields=["title"], expected_revision=1 if guarded else None)
    assert row.save_count == 2
    row.require_revision(2)
    stale.title = "second"
    with pytest.raises(StaleRevisionError) as caught:
        stale.save(expected_revision=1)
    assert (caught.value.expected, caught.value.current) == (1, 2)
    with pytest.raises(StaleRevisionError):
        stale.require_revision(2)
    row.refresh_from_db()
    assert (row.title, row.save_count) == ("first", 2)


def test_queryset_update_does_not_bump_revision():
    row = RevisionRow.objects.create()
    RevisionRow.objects.filter(pk=row.pk).update(title="bulk")
    row.refresh_from_db()
    assert (row.title, row.revision) == ("bulk", 1)


@pytest.mark.parametrize("model", [RevisionRow, RevisionChild])
def test_empty_partial_save_checks_committed_revision_without_writing(model, django_assert_num_queries):
    row = model.objects.create(**({"id": 11, "child_id": 77} if model is RevisionChild else {}))
    stale = model.objects.get(pk=row.pk)
    row.save()
    with django_assert_num_queries(0):
        stale.save(update_fields=[])
    with django_assert_num_queries(1), pytest.raises(StaleRevisionError) as caught:
        stale.save(update_fields=[], expected_revision=1)
    assert caught.value.current == 2
    with django_assert_num_queries(1):
        stale.save(update_fields=[], expected_revision=2)
    row.refresh_from_db()
    assert row.revision == 2


@pytest.mark.parametrize("model", [RevisionRow, RevisionChild])
@pytest.mark.parametrize("save_kwargs", [{}, {"expected_revision": 1}, {"expected_revision": 1, "update_fields": []}])
def test_vanished_revision_owner_is_not_resurrected(model, save_kwargs):
    row = model.objects.create(**({"id": 11, "child_id": 77} if model is RevisionChild else {}))
    RevisionRow.objects.filter(pk=row.id).delete()
    with pytest.raises(StaleRevisionError) as caught:
        row.save(**save_kwargs)
    assert caught.value.current is None
    assert caught.value.expected == save_kwargs.get("expected_revision")
    assert not RevisionRow.objects.exists()
    assert not RevisionChild.objects.exists()


def test_preset_primary_key_inserts_but_cannot_overwrite_an_existing_row():
    first = RevisionRow(id=41, title="retained")
    first.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        RevisionRow(id=41, title="overwrite").save()
    first.refresh_from_db()
    assert (first.title, first.revision) == ("retained", 1)


@pytest.mark.parametrize("value", [None, True, False, "1", 1.0, 0, -1, 2**31])
def test_revision_comparator_rejects_nonportable_values(value):
    with pytest.raises(ValidationError) as caught:
        require_revision(expected=value, current=1)
    assert "expected_revision" in caught.value.message_dict


@pytest.mark.parametrize("value", [1, 2**31 - 1])
def test_revision_comparator_accepts_portable_boundaries(value):
    require_revision(expected=value, current=value)


@pytest.mark.parametrize("value", [True, "1", 1.0, 0, -1, 2**31])
def test_invalid_save_expectation_cannot_mutate_the_stored_row(value, django_assert_num_queries):
    row = RevisionRow.objects.create()
    row.title = "refused"
    with django_assert_num_queries(0), pytest.raises(ValidationError):
        row.save(expected_revision=value)
    row.refresh_from_db()
    assert (row.title, row.revision) == ("original", 1)


def test_expected_revision_requires_a_persisted_row():
    with pytest.raises(ValidationError, match="existing row"):
        RevisionRow().save(expected_revision=1)
    assert not RevisionRow.objects.exists()


def test_parent_compare_and_swap_keeps_revision_predicate_on_one_update():
    row = RevisionChild.objects.create(id=11, child_id=77)
    row.detail = "updated"
    with CaptureQueriesContext(connection) as captured:
        row.save(update_fields=["detail"], expected_revision=1)
    updates = [q["sql"] for q in captured if q["sql"].startswith("UPDATE")]
    cas = [sql for sql in updates if '"revision" = (' in sql]
    assert len(cas) == 1
    assert '"scopedemo_revisionrow"' in cas[0]
    assert '"id" = 11' in cas[0] and '"revision" = 1' in cas[0]
    assert "SELECT" not in cas[0]
    row.refresh_from_db()
    assert (row.detail, row.revision, row.id, row.pk) == ("updated", 2, 11, 77)


def test_failed_child_save_rolls_back_the_parent_revision():
    row = RevisionChild.objects.create(id=11, child_id=77)
    row.detail = None
    with pytest.raises(IntegrityError), transaction.atomic():
        row.save(expected_revision=1)
    assert row.revision == 1
    row.refresh_from_db()
    assert (row.revision, row.detail) == (1, "child")


def test_unguarded_save_signals_receive_expression_then_committed_integer():
    row = RevisionRow.objects.create()
    observed = []

    def capture(sender, instance, **kwargs):
        observed.append(instance.revision)

    pre_save.connect(capture, sender=RevisionRow)
    post_save.connect(capture, sender=RevisionRow)
    try:
        row.save()
    finally:
        pre_save.disconnect(capture, sender=RevisionRow)
        post_save.disconnect(capture, sender=RevisionRow)
    assert isinstance(observed[0], CombinedExpression)
    assert observed[1] == row.revision == 2


@POSTGRES_LOCK
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("model", [RevisionRow, RevisionChild])
def test_two_writers_with_the_same_revision_commit_exactly_once(model):
    row = model.objects.create(**({"id": 11, "child_id": 77} if model is RevisionChild else {}))
    start = Barrier(2)

    def write(title):
        close_old_connections()
        try:
            candidate = model.objects.get(pk=row.pk)
            candidate.title = title
            start.wait(timeout=5)
            try:
                candidate.save(expected_revision=1)
            except StaleRevisionError as error:
                return ("stale", error.expected, error.current)
            return ("saved", title, candidate.revision)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, title) for title in ("first", "second")]
        results = [future.result(timeout=10) for future in futures]
    assert ("stale", 1, 2) in results
    [winner] = [result for result in results if result[0] == "saved"]
    row.refresh_from_db()
    assert (row.title, row.revision) == (winner[1], 2)
