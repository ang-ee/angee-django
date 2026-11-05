"""Focused query contracts for native write paths."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.test.utils import CaptureQueriesContext
from rebac import system_context
from simple_history.utils import get_history_manager_for_model

from angee.projects.testing.models import Task
from angee.provenance.context import changeset
from angee.provenance.history import CaptureHistoryMixin, ModelHistory
from tests.tables import model_tables

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_tables")]


def test_task_save_query_cost_follows_written_fields() -> None:
    with system_context(reason="tests.write_path.task.setup"):
        owner = get_user_model().objects.create_user(username="write-path-task-owner")
        task = Task.objects.create(title="Write-path task", owner=owner)

    counts = {}
    with system_context(reason="tests.write_path.task.save"):
        for field in ("title", "status", "assignee", "full"):
            samples = []
            for count in (1, 10):
                with CaptureQueriesContext(connection) as queries:
                    for index in range(count):
                        if field == "title":
                            task.title = f"Write-path title {count}-{index}"
                        elif field == "status":
                            task.status = "open"
                        elif field == "assignee":
                            task.assignee = owner if index % 2 else None
                        else:
                            task.title = f"Write-path full {count}-{index}"
                        if field == "status":
                            with task._work_verb_write():
                                task.save(update_fields={field})
                        else:
                            task.save(update_fields=None if field == "full" else {field})
                samples.append(len(queries))
            counts[field] = tuple(samples)
    print(f"task statements: {counts}")
    # Title and status measured 21 in round 2 (20 after F2); 24 leaves CI
    # headroom and still catches the parent path (26 and 29 statements).
    for field, ceiling in (("title", 24), ("status", 24), ("assignee", 27), ("full", 36)):
        assert counts[field][0] <= ceiling
        assert counts[field][1] <= ceiling * 10
    for field in ("title", "status", "full"):
        assert counts[field][1] == counts[field][0] * 10


def test_history_capture_does_not_reselect_the_written_row(monkeypatch: pytest.MonkeyPatch) -> None:
    class HistoryProbe(CaptureHistoryMixin):
        value = models.CharField(max_length=80)
        history = ModelHistory(inherit=True)

        class Meta:
            app_label = "provenance"
            db_table = "test_write_paths_history_probe"

    with model_tables((HistoryProbe, HistoryProbe.history.model)):
        with system_context(reason="tests.write_path.history.setup"):
            row = HistoryProbe.objects.create(value="before")
        from angee.provenance import history as history_owner

        checks = []
        current_changeset = history_owner.current_changeset

        def count_changeset(*args, **kwargs):
            checks.append(None)
            return current_changeset(*args, **kwargs)

        monkeypatch.setattr(history_owner, "current_changeset", count_changeset)
        with system_context(reason="tests.write_path.history.save"), CaptureQueriesContext(connection) as queries:
            row.value = "after"
            row.save(update_fields={"value"})
        sql = [query["sql"] for query in queries]
        print(f"history statements: {len(sql)}")
        print(f"history changeset checks: {len(checks)}")
        # Pre-save checks the boundary before SQL; post-save records capture on it.
        assert len(checks) == 2
        assert not any(
            statement.lstrip().upper().startswith("SELECT") and HistoryProbe._meta.db_table in statement
            for statement in sql
        )
        callbacks = []
        real_on_commit = transaction.on_commit

        def count_commit(func, *args, **kwargs):
            if getattr(func, "__qualname__", "").startswith("ModelHistory.record_rows."):
                callbacks.append(func)
            return real_on_commit(func, *args, **kwargs)

        monkeypatch.setattr(transaction, "on_commit", count_commit)
        with system_context(reason="tests.write_path.history.batch"), changeset(fresh=True), transaction.atomic():
            for index in range(10):
                HistoryProbe.objects.create(value=f"batch-{index}")
        print(f"history callbacks: {len(callbacks)}")
        assert len(callbacks) == 1
        row_pk = row.pk
        row.value = "unsaved delete value"
        with system_context(reason="tests.write_path.history.delete"):
            row.delete()
        assert HistoryProbe.history.filter(id=row_pk, history_type="-").get().value == "after"


def test_history_counts_only_models_written_since_previous_commit() -> None:
    class CountA(CaptureHistoryMixin):
        value = models.CharField(max_length=80)
        history = ModelHistory(inherit=True)

        class Meta:
            app_label = "provenance"
            db_table = "test_write_paths_count_a"

    class CountB(CaptureHistoryMixin):
        value = models.CharField(max_length=80)
        history = ModelHistory(inherit=True)

        class Meta:
            app_label = "provenance"
            db_table = "test_write_paths_count_b"

    class CountC(CaptureHistoryMixin):
        value = models.CharField(max_length=80)
        note = models.CharField(max_length=80, default="original")
        history = ModelHistory(inherit=True)

        class Meta:
            app_label = "provenance"
            db_table = "test_write_paths_count_c"

    def measure(write: Any) -> tuple[int, int]:
        with CaptureQueriesContext(connection) as queries:
            write()
        statements = [query["sql"] for query in queries]
        counts = sum(
            "COUNT(" in statement.upper() and "history_changeset_id" in statement
            for statement in statements
        )
        return len(statements), counts

    history_models = tuple(get_history_manager_for_model(model).model for model in (CountA, CountB, CountC))
    with model_tables((CountA, CountB, CountC, *history_models)):
        with system_context(reason="tests.write_path.history.multi_commit"), changeset(fresh=True):
            CountA.objects.create(value="a")
            CountB.objects.create(value="b")
            row = CountC.objects.create(value="c")

            def partial() -> None:
                row.value = "partial"
                row.save(update_fields={"value"})

            def full() -> None:
                row.value = "full"
                row.save()

            measured = {
                "partial": measure(partial),
                "full": measure(full),
                "delete": measure(row.delete),
            }
            # One sync changeset with a commit per record must issue only one
            # history COUNT at each commit, even as more models are touched.
            cycle = (CountA, CountB, CountC)
            sync = [
                measure(lambda index=index: cycle[index % 3].objects.create(value=f"sync-{index}"))
                for index in range(9)
            ]
    print(f"history three paths: {measured}; sync-run: {sync}")
    assert {name: count for name, (_statements, count) in measured.items()} == {
        "partial": 1, "full": 1, "delete": 1,
    }
    assert [count for _statements, count in sync] == [1] * 9
    # The prior retained-model COUNTs added two statements to each path.
    for name, ceiling in (("partial", 7), ("full", 6), ("delete", 26)):
        assert measured[name][0] <= ceiling
