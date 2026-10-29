"""Durable merge attribution contracts before the framework delegates its lifecycle."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import SubjectRef, actor_context, system_context

from angee.provenance.context import changeset, changeset_model_label, current_changeset
from angee.provenance.contrib.angee.context import merge_context
from tests.core_persistence import OwnedRow

pytestmark = [pytest.mark.entrypoint_boundaries, pytest.mark.django_db(transaction=True)]


def survivor() -> OwnedRow:
    """Persist the operational record independently of the merge under test."""

    with changeset(fresh=True), system_context(reason="tests.merge.survivor"):
        return OwnedRow.objects.create(title="survivor")


@pytest.mark.parametrize("join", [False, True])
def test_merge_context_preserves_existing_boundary_or_opens_a_fresh_one(join: bool) -> None:
    record = survivor()
    with changeset(fresh=True, activity="system", change_reason="caller"):
        parent = current_changeset(record=True)
        assert parent is not None
        with merge_context(record, join=join):
            selected = current_changeset(record=True)
            assert selected is not None
            assert (selected is parent) is join
        assert current_changeset() is parent
    stored = apps.get_model(changeset_model_label())._base_manager.get(pk=selected.pk)
    assert stored.state == "closed"
    assert stored.outcome == "success"
    assert stored.touched == [record._meta.label_lower]
    if join:
        assert stored.activity == "system"
        assert stored.change_reason == "caller"
    else:
        assert stored.activity == "merge"
        assert stored.origin == record


def test_merge_context_opens_fresh_attribution_when_no_boundary_exists() -> None:
    record = survivor()
    assert current_changeset() is None
    with merge_context(record, join=True):
        selected = current_changeset(record=True)
        assert selected is not None
    stored = apps.get_model(changeset_model_label())._base_manager.get(pk=selected.pk)
    assert (stored.activity, stored.outcome, stored.touched) == ("merge", "success", [record._meta.label_lower])


def test_merge_context_refuses_joining_a_different_database_before_entering() -> None:
    record = survivor()
    record._state.db = "different-database"
    with changeset(fresh=True):
        parent = current_changeset(record=True)
        with pytest.raises(ValidationError, match="A fold must share the database of its changeset"):
            with merge_context(record, join=True):
                pytest.fail("A cross-database fold entered its persistence body.")
        assert current_changeset() is parent


def test_failed_merge_context_keeps_failure_and_does_not_mark_an_unchanged_survivor() -> None:
    record = survivor()
    selected: Any = None
    with pytest.raises(RuntimeError, match="fold failed"):
        with merge_context(record):
            selected = current_changeset(record=True)
            raise RuntimeError("fold failed")
    stored = apps.get_model(changeset_model_label())._base_manager.get(pk=selected.pk)
    assert (stored.outcome, stored.touched) == ("failure", [])


def test_storage_attribution_hooks_preserve_native_actor_reason_and_strictness(settings) -> None:
    model = apps.get_model(changeset_model_label())
    actor = SubjectRef.of("auth/user", "7")
    with actor_context(actor), system_context(reason="tests.provenance.policy"):
        assert model.ambient_attribution() == (str(actor), "tests.provenance.policy")
    settings.REBAC_STRICT_MODE = False
    assert model.context_required() is False
    settings.REBAC_STRICT_MODE = True
    assert model.context_required() is True


def test_boundary_uses_the_storage_owned_ambient_attribution(monkeypatch) -> None:
    model = apps.get_model(changeset_model_label())
    monkeypatch.setattr(model, "ambient_attribution", classmethod(lambda cls: ("auth/user:17", "storage.reason")))
    with changeset(fresh=True):
        selected = current_changeset(required=True)
        assert selected is not None
        assert (selected.actor_ref, selected.change_reason) == ("auth/user:17", "storage.reason")


def test_storage_required_context_error_preserves_both_public_catch_contracts() -> None:
    from rebac.errors import MissingActorError

    from angee.provenance.context import ChangesetRequired

    model = apps.get_model(changeset_model_label())
    assert issubclass(model.required_context_error, ChangesetRequired)
    assert issubclass(model.required_context_error, MissingActorError)


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_real_changeset_storage_keeps_explicit_boundary_database(
    tmp_path, monkeypatch, django_db_blocker, outcome,
) -> None:
    """The native storage fixture retains its own save/close/export methods."""
    from django.contrib.contenttypes.models import ContentType
    from django.db import connections, router, transaction
    from rebac.backends import reset_backend
    from rebac.models.generation import SchemaGeneration
    from rebac.testing import install_schema

    from angee.provenance import models as sources
    from angee.provenance.testing.models import Changeset

    assert Changeset.close is sources.Changeset.close
    assert Changeset.save is sources.Changeset.save
    alias = "provenance_explicit_storage"

    class DefaultRouter:
        def db_for_read(self, model, **hints):
            return "default"

        db_for_write = db_for_read

    monkeypatch.setattr(router, "routers", [DefaultRouter()])
    connections.databases[alias] = {
        **connections.databases["default"],
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(tmp_path / "provenance-storage.sqlite3"),
        "OPTIONS": {},
        "CONN_MAX_AGE": 0,
        "TEST": {},
    }
    database = connections[alias]
    try:
        with django_db_blocker.unblock():
            with database.schema_editor() as editor:
                for model in (ContentType, SchemaGeneration, Changeset):
                    editor.create_model(model)
            install_schema("definition provenance/changeset {}", using=alias)
            with system_context(reason="tests.provenance.storage_alias"):
                with transaction.atomic(using=alias):
                    try:
                        with changeset(fresh=True, using=alias):
                            row = current_changeset(required=True, record=True)
                            assert isinstance(row, Changeset)
                            assert row._state.db == alias
                            # Matching PKs make a wrong-default close visible.
                            sentinel = Changeset(pk=row.pk, change_reason="default sentinel")
                            sentinel.save(force_insert=True, using="default")
                            if outcome == "failure":
                                raise ValueError("handled operation failure")
                    except ValueError:
                        pass
                    assert row.outcome is None
                stored = Changeset._base_manager.using(alias).get(pk=row.pk)
                assert stored.state == Changeset.State.CLOSED
                assert stored.outcome == outcome
                sentinel.refresh_from_db(using="default")
                assert sentinel.state == Changeset.State.OPEN
                assert sentinel.outcome is None
                assert stored.document()
                assert Changeset._base_manager.using(alias).get(pk=row.pk).hash == stored.hash
                sentinel.refresh_from_db(using="default")
                assert sentinel.hash == ""
                stored.scope, stored.key = "storage", "created"
                stored.record_created(stored)
                assert Changeset._base_manager.using(alias).get(pk=row.pk).created_object_id == str(row.pk)
                sentinel.refresh_from_db(using="default")
                assert sentinel.created_object_id is None
    finally:
        reset_backend()
        database.close()
        del connections[alias]
        connections.databases.pop(alias)
