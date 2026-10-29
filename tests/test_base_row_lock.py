"""System row locks apply equally to native collection persistence."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from threading import Event
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, models
from django.test.utils import isolate_apps
from rebac import actor_context, system_context

from angee.base.models.locks import RowLockMixin
from tests.conftest import create_user
from tests.linesdemo.models import Document, PinnedLine


@pytest.mark.django_db(transaction=True)
def test_collection_writes_cannot_mutate_or_create_system_rows(composed_tables: None) -> None:
    user = create_user("row-lock-writer")
    with system_context(reason="test.row_lock.seed"):
        parent = Document.objects.create(title="Document")
        row = PinnedLine.objects.create(document=parent, label="System", pinned=True)
    with actor_context(user):
        with pytest.raises(ValidationError):
            PinnedLine.objects.filter(pk=row.pk).update(label="Changed")
        with pytest.raises(ValidationError):
            PinnedLine.objects.filter(pk=row.pk).delete()
        with pytest.raises(ValidationError):
            PinnedLine.objects.bulk_create([PinnedLine(document=parent, label="New system", pinned=True)])
        PinnedLine.objects.filter(pk=row.pk).update(quantity=models.F("quantity") + 1)
    row.refresh_from_db()
    assert (row.label, row.quantity) == ("System", 2)


@isolate_apps()
def test_row_lock_model_requires_collection_guard_on_default_and_base_managers() -> None:
    class Unguarded(RowLockMixin, models.Model):
        objects = models.Manager()
        _angee_base = models.Manager()

        class Meta:
            app_label = "base"
            base_manager_name = "_angee_base"

    errors = {error.id for error in Unguarded.check()}
    assert {"angee.E048", "angee.E049"} <= errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("positional", (False, True))
def test_unfiltered_conflict_updates_cannot_mutate_system_rows(composed_tables: None, positional: bool) -> None:
    """The unfiltered writer checks native positional and keyword conflict flags."""

    with system_context(reason="test.row_lock.conflict_seed"):
        parent = Document.objects.create(title="Document")
        row = PinnedLine.objects.create(document=parent, label="System", pinned=True)
    candidate = copy(row)
    candidate.pinned = False
    candidate.label = "Changed"
    with pytest.raises(ValidationError, match="Row-locked conflict updates"):
        if positional:
            PinnedLine._base_manager.bulk_create([candidate], None, False, True, ["label"], ["pk"])
        else:
            PinnedLine._base_manager.bulk_create(
                [candidate], update_conflicts=True, update_fields=["label"], unique_fields=["pk"],
            )
    row.refresh_from_db()
    assert row.label == "System"


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real READ COMMITTED interleaving is required.")
@pytest.mark.parametrize("manager_path", ("default", "base"))
def test_collection_write_excludes_rows_entering_predicate_after_validation(
    composed_tables: None, manager_path: str,
) -> None:
    """A newly matching system row is outside the locked and validated write set."""

    user = create_user("row-lock-concurrent-writer")
    with system_context(reason="test.row_lock.concurrent_seed"):
        parent = Document.objects.create(title="Document")
        editable = PinnedLine.objects.create(document=parent, label="Selected editable")
    ready, proceed = Event(), Event()

    def update() -> int:
        close_old_connections()

        def pause_mutation(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
            if sql.lstrip().upper().startswith("UPDATE ") and PinnedLine._meta.db_table in sql:
                ready.set()
                if not proceed.wait(timeout=10):
                    raise TimeoutError("Competing insertion did not complete.")
            return execute(sql, params, many, context)

        try:
            manager = PinnedLine.objects if manager_path == "default" else PinnedLine._base_manager
            with actor_context(user), connection.execute_wrapper(pause_mutation):
                return manager.using("default").filter(label__startswith="Selected ").update(label="Changed")
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        mutation = pool.submit(update)
        try:
            assert ready.wait(timeout=10), "Writer did not finish validation and reach its UPDATE."
            with system_context(reason="test.row_lock.concurrent_insert"):
                entered = PinnedLine.objects.create(document=parent, label="Selected system", pinned=True)
        finally:
            proceed.set()
        assert mutation.result(timeout=10) == 1
    editable.refresh_from_db()
    entered.refresh_from_db()
    assert editable.label == "Changed"
    assert entered.label == "Selected system"
