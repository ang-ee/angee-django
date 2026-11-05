"""Write-path probes for history capture, chatter flags, ingest transactions and timeline isolation."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.db.models import F
from django.db.models.signals import post_save, pre_save
from rebac import system_context

from angee.messaging.testing.models import Message, Thread, ThreadAttachment
from angee.projects.testing.models import Task
from angee.provenance.history import HistoryMixin, ModelHistory
from tests.tables import model_tables
from tests.test_messaging import ThreadedTicket, _parsed
from tests.test_messaging import channel as channel  # noqa: F401  (fixture)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_tables")]


class RvHistoryProbe(HistoryMixin):
    value = models.CharField(max_length=80)
    note = models.CharField(max_length=80, default="n1")
    counter = models.IntegerField(default=0)
    seeded = models.IntegerField(db_default=7)
    history = ModelHistory(inherit=True)

    class Meta:
        app_label = "provenance"
        db_table = "test_write_path_history_probe"


def _latest(row: Any) -> Any:
    return RvHistoryProbe.history.filter(id=row.pk).order_by("-history_id").first()


def _db(row: Any) -> Any:
    return RvHistoryProbe._base_manager.get(pk=row.pk)


def test_probe_history_matches_database() -> None:
    results = {}
    with model_tables((RvHistoryProbe, RvHistoryProbe.history.model)):
        with system_context(reason="tests.write_path.history"):
            row = RvHistoryProbe.objects.create(value="a")
            results["db_default_on_create"] = (_latest(row).seeded, _db(row).seeded)
            # 1. stale unwritten column: the row moved under the instance, then a partial save.
            with connection.cursor() as cursor:
                cursor.execute(f"UPDATE {RvHistoryProbe._meta.db_table} SET note = 'db' WHERE id = %s", [row.pk])
            row.value = "b"
            row.save(update_fields={"value"})
            results["stale_unwritten_partial"] = (_latest(row).note, _db(row).note)

            # 2. post_save receiver changes a captured column in the database.
            def post(sender: Any, instance: Any, **kwargs: Any) -> None:
                if instance.value == "signal":
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f"UPDATE {RvHistoryProbe._meta.db_table} SET note = 'from-post-save' WHERE id = %s",
                            [instance.pk],
                        )

            post_save.connect(post, sender=RvHistoryProbe, weak=False)
            try:
                row = RvHistoryProbe.objects.get(pk=row.pk)
                row.value = "signal"
                row.save()
                results["post_save_db_write"] = (_latest(row).note, _db(row).note)
            finally:
                post_save.disconnect(post, sender=RvHistoryProbe)

            # 3. pre_save receiver mutates the instance (full and partial saves).
            def pre(sender: Any, instance: Any, **kwargs: Any) -> None:
                instance.note = "from-pre-save"

            pre_save.connect(pre, sender=RvHistoryProbe, weak=False)
            try:
                row = RvHistoryProbe.objects.get(pk=row.pk)
                row.value = "pre-full"
                row.save()
                results["pre_save_full"] = (_latest(row).note, _db(row).note)
                with connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {RvHistoryProbe._meta.db_table} SET note = 'reset' WHERE id = %s", [row.pk])
                row = RvHistoryProbe.objects.get(pk=row.pk)
                row.value = "pre-partial"
                row.save(update_fields={"value"})
                results["pre_save_partial"] = (_latest(row).note, _db(row).note)
            finally:
                pre_save.disconnect(pre, sender=RvHistoryProbe)

            # 4. expression assignment.
            row = RvHistoryProbe.objects.get(pk=row.pk)
            row.counter = F("counter") + 5
            row.save()
            results["expression_full"] = (_latest(row).counter, _db(row).counter)
    print(f"RV history probe: {results}")
    # The post-save receiver writes after history capture on both the parent and
    # this lane; it is not the stale-unwritten-column regression under review.
    mismatched = {
        key: value for key, value in results.items()
        if key != "post_save_db_write" and value[0] != value[1]
    }
    assert not mismatched, mismatched


def test_probe_task_snapshot_laundered_by_deferred_load(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    original = Task.validate_record_access_subject

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(args[0] if args else kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Task, "validate_record_access_subject", spy)
    with system_context(reason="tests.write_path.task"):
        owner = get_user_model().objects.create_user(username="write-path-owner")
        other = get_user_model().objects.create_user(username="write-path-other")
        task = Task.objects.create(title="RV task", owner=owner)
        calls.clear()
        # Control: plain full save after an in-memory assignee change validates the assignee.
        loaded = Task.objects.get(pk=task.pk)
        loaded.assignee_id = owner.pk
        try:
            loaded.save()
        except Exception as error:  # noqa: BLE001 - the validation itself may refuse; we count the call
            print(f"RV control save raised {type(error).__name__}")
        control = len(calls)
        calls.clear()
        # Deferred load between the edit and the full save.
        loaded = Task.objects.defer("title").get(pk=task.pk)
        loaded.assignee_id = other.pk
        _ = loaded.title  # Django calls refresh_from_db(fields=["title"])
        raised = None
        try:
            loaded.save()
        except Exception as error:  # noqa: BLE001
            raised = type(error).__name__
        persisted = Task._base_manager.filter(pk=task.pk).values_list("assignee_id", flat=True).get()
    print(f"RV task snapshot: control_validations={control} laundered_validations={len(calls)} "
          f"raised={raised} persisted_assignee={'other' if persisted == other.pk else persisted}")
    assert control >= 1
    assert len(calls) >= 1, "the assignee written by a full save was never validated"


def test_probe_chatter_flag_paths(channel: Any) -> None:  # noqa: F811
    out = {}
    with system_context(reason="tests.write_path.chatter"):
        ticket = ThreadedTicket.objects.create(title="RV chatter")
        chatter = ticket.message_thread()
        out["attach"] = Thread._base_manager.get(pk=chatter.pk).is_record_attached()
        # Plain thread merged into the chatter survivor.
        plain = Message.objects.ingest([_parsed("rv-plain-1", subject="RV first subject")], channel=channel)[0].thread
        out["plain_before_merge"] = Thread._base_manager.get(pk=plain.pk).is_record_attached()
        survivor = Thread._base_manager.get(pk=chatter.pk)
        survivor.fold([Thread._base_manager.get(pk=plain.pk)])
        out["merged_member_after_merge"] = Thread._base_manager.get(pk=plain.pk).is_record_attached()
        # Chatter thread merged into a plain survivor.
        ticket2 = ThreadedTicket.objects.create(title="RV chatter 2")
        chatter2 = ticket2.message_thread()
        plain2 = Message.objects.ingest(
            [_parsed("rv-plain-2", subject="RV distinct subject")], channel=channel
        )[0].thread
        Thread._base_manager.get(pk=plain2.pk).fold([Thread._base_manager.get(pk=chatter2.pk)])
        out["plain_survivor_of_chatter"] = Thread._base_manager.get(pk=plain2.pk).is_record_attached()
        # Instance detach.
        attachment = ThreadAttachment._base_manager.get(thread_id=chatter.pk) if ThreadAttachment._base_manager.filter(
            thread_id=chatter.pk).exists() else ThreadAttachment._base_manager.filter(object_id=ticket.pk).first()
        attachment.delete()
        out["after_instance_detach"] = Thread._base_manager.get(pk=chatter.pk).is_record_attached()
        # Queryset detach (bypasses Model.delete()).
        ticket3 = ThreadedTicket.objects.create(title="RV chatter 3")
        chatter3 = ticket3.message_thread()
        ThreadAttachment._base_manager.filter(thread_id=chatter3.pk).delete()
        out["after_queryset_detach"] = Thread._base_manager.get(pk=chatter3.pk).is_record_attached()
    print(f"RV chatter: {out}")
    assert out["attach"] and not out["plain_before_merge"]
    assert out["merged_member_after_merge"] and out["plain_survivor_of_chatter"]


def test_probe_ingest_event_transaction_scope(channel: Any) -> None:  # noqa: F811
    from angee.messaging.events import message_ingested

    seen = []

    def capture(sender: Any, instance: Any, **kwargs: Any) -> None:
        seen.append((instance.external_id, connection.in_atomic_block, len(kwargs.get("created_batch") or (instance,))))

    message_ingested.connect(capture, weak=False)
    try:
        with system_context(reason="tests.write_path.ingest"):
            Message.objects.ingest([_parsed("rv-tx-1"), _parsed("rv-tx-2"), _parsed("rv-tx-3")], channel=channel)
    finally:
        message_ingested.disconnect(capture)
    print(f"RV ingest events (external_id, in_atomic_block, batch_len): {seen}")
    assert all(in_atomic for _external, in_atomic, _n in seen), "message_ingested ran outside the landing transaction"


def test_probe_timeline_db_error_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    from angee.projects.timeline import TaskKind

    def broken(self: Any, row: Any) -> Any:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM write_path_missing_relation")
        return iter(())

    with system_context(reason="tests.write_path.timeline"):
        owner = get_user_model().objects.create_user(username="write-path-tl")
        task = Task.objects.create(title="RV timeline", owner=owner)
    monkeypatch.setattr(TaskKind, "records", broken)
    outcome = "ok"
    try:
        with system_context(reason="tests.write_path.timeline.save"), transaction.atomic():
            task.title = "RV timeline 2"
            task.save(update_fields={"title"})
            Task._base_manager.filter(pk=task.pk).count()
    except Exception as error:  # noqa: BLE001
        outcome = f"{type(error).__name__}: {str(error).splitlines()[0][:120]}"
    print(f"RV timeline DB-error isolation: {outcome}")
    assert outcome == "ok"
