"""The trash mixin soft-removes rows with who, when and why, and restores them."""

import pytest
from django.core.exceptions import ValidationError
from rebac import actor_context

from angee.base.mixins import TRASH_REASON_MAX_LENGTH
from tests.conftest import create_user
from tests.core_persistence import TrashRow

pytestmark = pytest.mark.django_db


def test_trash_stamps_the_actor_time_and_reason_and_restore_clears_them():
    manager = create_user("trash-manager")
    row = TrashRow.objects.create()
    before = row.updated_at
    with actor_context(manager):
        row.trash(reason="  Off topic  ")
    row.refresh_from_db()
    assert row.is_trashed
    assert row.trashed_by == manager
    assert row.trashed_at is not None
    assert row.trash_reason == "Off topic"
    assert row.updated_at > before

    row.restore()
    row.refresh_from_db()
    assert (row.is_trashed, row.trashed_at, row.trashed_by_id, row.trash_reason) == (False, None, None, "")


def test_trash_and_restore_refuse_a_change_that_does_nothing():
    row = TrashRow.objects.create()
    with pytest.raises(ValidationError) as restoring:
        row.restore()
    assert set(restoring.value.message_dict) == {"is_trashed"}
    row.trash()
    stale = TrashRow.objects.get(pk=row.pk)
    stale.is_trashed = False
    with pytest.raises(ValidationError) as trashing:
        stale.trash(reason="again")
    assert set(trashing.value.message_dict) == {"is_trashed"}
    row.refresh_from_db()
    assert row.is_trashed and row.trash_reason == ""


def test_trash_refuses_an_oversized_reason_without_changing_the_row():
    row = TrashRow.objects.create()
    row.trash(reason="x" * TRASH_REASON_MAX_LENGTH)
    row.restore()
    with pytest.raises(ValidationError) as refused:
        row.trash(reason="x" * (TRASH_REASON_MAX_LENGTH + 1))
    assert set(refused.value.message_dict) == {"reason"}
    row.refresh_from_db()
    assert not row.is_trashed


def test_querysets_scope_trashed_rows_and_bulk_trash_skips_them():
    manager = create_user("bulk-trash-manager")
    kept, removed, swept = (TrashRow.objects.create(title=title) for title in ("kept", "removed", "swept"))
    removed.trash(reason="first")
    assert set(TrashRow.objects.trashed()) == {removed}
    assert set(TrashRow.objects.untrashed()) == {kept, swept}
    assert set(TrashRow.objects.trash_targets()) == {kept, removed, swept}

    with actor_context(manager):
        assert TrashRow.objects.exclude(pk=kept.pk).trash(reason="vanished") == 1
    removed.refresh_from_db()
    swept.refresh_from_db()
    assert removed.trash_reason == "first"
    assert (swept.is_trashed, swept.trashed_by, swept.trash_reason) == (True, manager, "vanished")
    assert list(TrashRow.objects.untrashed()) == [kept]

    assert TrashRow.objects.filter(pk=removed.pk).restore() == 1
    assert TrashRow.objects.filter(pk=removed.pk).restore() == 0
    removed.refresh_from_db()
    assert (removed.is_trashed, removed.trashed_at, removed.trash_reason) == (False, None, "")


def test_bulk_trash_stamps_rows_trashed_together():
    first, second = TrashRow.objects.create(), TrashRow.objects.create()
    first.trash()
    TrashRow.objects.filter(pk=second.pk).trash(at=first.trashed_at)
    second.refresh_from_db()
    assert second.trashed_at == first.trashed_at
