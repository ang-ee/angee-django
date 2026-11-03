import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.db import connections, router, transaction

from angee.provenance.engine.context import (
    actor_context,
    actor_reference,
    changeset,
    current_actor,
    current_changeset,
    current_reason,
    is_current_changeset,
)
from tests.provenance_engine.capture_app.models import LocalNote, Note
from tests.provenance_engine.storage.models import Changeset

pytestmark = pytest.mark.django_db(transaction=True)


def test_actor_context_restores():
    with actor_context("outer", "why"):
        assert current_actor() == "outer"
        assert current_reason() == "why"
        with actor_context("inner"):
            assert current_actor() == "inner"
    assert current_actor() is None
    assert actor_reference(None) == ""


def test_empty_nested_and_conflicting_boundaries():
    with changeset(fresh=True):
        with changeset(activity="chosen"):
            assert current_changeset().activity == "chosen"
        with pytest.raises(ValueError), changeset(activity="conflict"):
            pass
    assert not Changeset.objects.exists()


def test_savepoint_rollback_recreates_row():
    with changeset():
        with pytest.raises(RuntimeError), transaction.atomic():
            Note.objects.create(title="rollback")
            raise RuntimeError
        Note.objects.create(title="keep")
    assert Note.objects.count() == 1
    assert Changeset.objects.count() == 1
    assert Changeset.objects.get().counts["capture_test.note"] == 1


def test_outer_commit_and_handled_failure():
    with transaction.atomic():
        with changeset() as failed:
            Note.objects.create(title="written")
            failed()
        receipt = Changeset.objects.get()
        assert receipt.outcome != "failure"
    receipt.refresh_from_db()
    assert receipt.outcome == "failure"


def test_fresh_and_actor_changes():
    with changeset(actor="one"):
        Note.objects.create(title="one")
        with actor_context("two"):
            Note.objects.create(title="two")
        with changeset(actor="three", fresh=True):
            Note.objects.create(title="three")
    assert set(Changeset.objects.values_list("actor_ref", flat=True)) == {"one", "two", "three"}


def test_async_boundary():
    async def run():
        try:
            async with changeset(actor="async"):
                await sync_to_async(Note.objects.create)(title="async")
        finally:
            await sync_to_async(connections.close_all)()

    asyncio.run(run())
    assert Changeset.objects.get().actor_ref == "async"


def test_context_copy_threads_have_distinct_committed_rows():
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from django.db import connections

    def write(title):
        try:
            Note.objects.create(title=title)
        finally:
            for connection in connections.all():
                if connection.connection is not None:
                    connection.connection.close()
                    connection.connection = None

    with changeset(actor="copied"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copy_context().run, write, "thread").result()
        Note.objects.create(title="main")
    assert Changeset.objects.count() == 2
    assert set(Changeset.objects.values_list("actor_ref", flat=True)) == {"copied"}


def test_process_boundary_preserves_actor_without_context_copy():
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    def write():
        try:
            Note.objects.create(title="process")
        finally:
            for connection in connections.all():
                if connection.connection is not None:
                    connection.connection.close()
                    connection.connection = None

    with changeset(actor="process-owner", process=True):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(write).result()
    assert Changeset.objects.get().actor_ref == "process-owner"


def test_actor_can_be_explicitly_cleared_inside_boundary():
    with changeset(actor="outer"):
        with actor_context(None):
            Note.objects.create(title="unattributed")
    assert Changeset.objects.get().actor_ref == ""


def test_configured_storage_owns_ambient_attribution_and_strictness(monkeypatch):
    monkeypatch.setattr(Changeset, "context_required", classmethod(lambda cls: False))
    assert current_changeset(required=True) is None
    monkeypatch.setattr(Changeset, "ambient_attribution", classmethod(lambda cls: ("host:actor", "host reason")))
    with changeset(actor="ignored-by-owner"):
        Note.objects.create(title="owned")
    receipt = Changeset.objects.get()
    assert receipt.actor_ref == "host:actor"
    assert receipt.change_reason == "host reason"


def test_process_thread_history_user_uses_boundary_principal(actor):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    def write():
        try:
            Note.objects.create(title="principal")
        finally:
            for connection in connections.all():
                if connection.connection is not None:
                    connection.connection.close()
                    connection.connection = None

    with changeset(actor=actor, process=True):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(write).result()
    assert Note.history.get().history_user_id == actor.pk


def test_fresh_boundary_entrypoint_defaults_can_be_refined():
    with changeset(fresh=True, activity="import"):
        with changeset(activity="manual"):
            assert current_changeset().activity == "manual"


@pytest.mark.django_db(transaction=True, databases=["default", "other"])
def test_explicit_database_binding_refuses_conflicting_capture_and_join():
    with changeset(actor="bound", fresh=True, using="other"):
        row = current_changeset(required=True, record=True)
        assert row._state.db == "other"
        with pytest.raises(ValueError, match="selected database"):
            current_changeset(required=True, record=True, using="default")
        with pytest.raises(ValueError, match="existing database"):
            with changeset(using="default"):
                pass
    assert Changeset.objects.using("default").count() == 0
    assert Changeset.objects.using("other").get(pk=row.pk).state == "closed"


def test_unconfigured_storage_fails_closed(settings):
    from angee.provenance.engine.context import ChangesetRequired

    settings.PROVENANCE_CHANGESET_MODEL = ""
    with changeset(fresh=True):
        assert current_changeset() is None
        with pytest.raises(ChangesetRequired):
            current_changeset(required=True)
    del settings.PROVENANCE_CHANGESET_MODEL
    assert current_changeset() is None
    with pytest.raises(ChangesetRequired):
        current_changeset(required=True)


def test_current_storage_identity_does_not_allocate_or_match_equal_rows():
    assert not is_current_changeset(Changeset())
    with changeset(fresh=True):
        assert not is_current_changeset(Changeset())
        assert not Changeset.objects.exists()
        current = current_changeset(required=True, record=True)
        assert is_current_changeset(current)
        assert not is_current_changeset(Changeset(pk=current.pk))
    assert not is_current_changeset(current)


@pytest.mark.django_db(transaction=True, databases=["default", "other"])
def test_direct_delete_uses_signal_database_inside_unbound_boundary(monkeypatch):
    class ElsewhereRouter:
        def db_for_write(self, model, **hints):
            return "other"

    with changeset(actor="capture:initial"):
        note = LocalNote.objects.create(title="selected deletion")
    monkeypatch.setattr(router, "routers", [ElsewhereRouter()])
    with changeset(actor="capture:delete", fresh=True):
        note.delete(using="default")
    assert LocalNote.history.using("default").filter(history_type="-").count() == 1
    assert Changeset.objects.using("default").filter(actor_ref="capture:delete").count() == 1
    assert not Changeset.objects.using("other").exists()
    assert not LocalNote.history.using("other").exists()


def test_unrecorded_changeset_does_not_touch_its_selected_database(monkeypatch):
    from django.db import router

    from angee.provenance.engine.context import current_changeset

    native = router.db_for_write
    monkeypatch.setattr(
        router,
        "db_for_write",
        lambda model, **hints: "unconfigured" if model is Changeset else native(model, **hints),
    )
    with changeset(fresh=True):
        row = current_changeset()
        assert row._state.db == "unconfigured"
        assert row.pk is None
    assert not Changeset.objects.exists()
