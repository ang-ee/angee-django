import pytest
from django.db import IntegrityError, models, transaction
from django.test import override_settings
from simple_history.signals import post_create_historical_record
from simple_history.utils import bulk_update_with_history

from angee.provenance.engine.context import ChangesetRequired, changeset
from tests.provenance_engine.capture_app.models import Child, DetailedNote, Explicit, Note, NoteProxy
from tests.provenance_engine.storage.models import Changeset

pytestmark = pytest.mark.django_db(transaction=True)


def test_save_expression_and_partial_stored_values():
    with changeset(actor="import", change_reason="initial"):
        note = Note.objects.create(title="first", number=2)
        note.title = "unsaved"
        note.number = models.F("number") + 3
        note.save(update_fields=["number"])
    versions = list(note.history.order_by("history_id"))
    assert [(row.title, row.number) for row in versions] == [("first", 2), ("first", 5)]
    receipt = Changeset.objects.get()
    assert receipt.actor_ref == "import"
    assert receipt.change_reason == "initial"
    assert receipt.counts["capture_test.note"] == 2


def test_strict_before_write_and_disabled_history():
    with pytest.raises(ChangesetRequired):
        Note.objects.create(title="denied")
    assert not Note.objects.exists()
    with override_settings(SIMPLE_HISTORY_ENABLED=False):
        note = Note.objects.create(title="disabled")
    assert not note.history.exists()


def test_bulk_and_queryset_expressions():
    with changeset():
        notes = Note.objects.bulk_create([Note(title="one"), Note(title="two")])
        Note.objects.update(number=models.F("number") + 1)
        for note in notes:
            note.title = "bulk"
        Note.objects.bulk_update(notes, ["title"])
    assert Note.history.count() == 6
    assert set(Note.history.filter(history_type="~").values_list("number", flat=True)) == {1}


def test_cascade_stored_deletion_and_metadata():
    with changeset():
        note = Note.objects.create(title="stored")
        child = Child.objects.create(note=note, title="child")
    with changeset():
        note.title = "unsaved"
        note._change_reason = "native"
        note.delete()
    assert Note.history.filter(history_type="-").get().title == "stored"
    assert Note.history.filter(history_type="-").get().history_change_reason == "native"
    assert Child.history.filter(id=child.pk, history_type="-").count() == 1


def test_explicit_registration_excluded_only_updates():
    with changeset():
        note = Explicit.objects.create(title="one")
        Explicit.objects.update(counter=4)
        Explicit.objects.update(title="two")
    assert note.history.count() == 2


def test_proxy_and_multi_table_bulk_capture():
    with changeset():
        note = DetailedNote.objects.create(title="parent", detail="child")
        Note.objects.filter(pk=note.pk).update(title="updated")
        proxy = NoteProxy.objects.get(pk=note.pk)
        proxy.title = "proxy"
        proxy.save()
    assert note.history.count() == 2
    assert Note.history.filter(id=note.pk).count() == 3


def test_native_signal_and_bulk_defaults():
    seen = []

    def receiver(sender, history_instance, **kwargs):
        seen.append(history_instance.pk)

    post_create_historical_record.connect(receiver, sender=Note.history.model)
    try:
        with changeset():
            note = Note.objects.create(title="one")
            note.title = "two"
            bulk_update_with_history([note], Note, ["title"], default_change_reason="bulk native")
        assert seen
        assert note.history.first().history_change_reason == "bulk native"
    finally:
        post_create_historical_record.disconnect(receiver, sender=Note.history.model)


def test_failed_insert_leaves_no_attribution():
    with changeset():
        with pytest.raises(IntegrityError), transaction.atomic():
            Note.objects.create(title=None)
    assert not Changeset.objects.exists()


@pytest.mark.django_db(databases=["default", "other"], transaction=True)
def test_explicit_database_binding():
    with changeset(actor="database"):
        note = Note.objects.using("other").create(title="other")
        Note.objects.using("other").update(title="changed")
    # Native history defaults to its router; explicitly bind the native owner.
    assert note._state.db == "other"
    assert Note.objects.using("other").get().title == "changed"
    assert not Note.objects.exists()
    assert set(Note.history.values_list("source_database", flat=True)) == {"other"}


def test_deferred_instance_does_not_write_unsaved_projection():
    with changeset():
        note = Note.objects.create(title="stored", number=4)
        deferred = Note.objects.only("number").get(pk=note.pk)
        deferred.number = 8
        deferred.save()
    assert note.history.first().title == "stored"
    assert note.history.first().number == 8


def test_ignored_conflicts_fail_before_insertion():
    from django.db import NotSupportedError

    with changeset(), pytest.raises(NotSupportedError):
        Note.objects.bulk_create([Note(title="no")], ignore_conflicts=True)
    assert not Note.objects.exists()


@pytest.mark.django_db(databases=["default", "other"], transaction=True)
def test_explicit_alias_colocates_attribution_and_history():
    from tests.provenance_engine.capture_app.models import LocalNote

    with changeset(actor="other"):
        row = LocalNote.objects.using("other").create(title="start")
        LocalNote.objects.using("other").update(title="end")
    assert LocalNote.history.using("other").filter(id=row.pk).count() == 2
    receipt = Changeset.objects.using("other").get()
    assert receipt.actor_ref == "other"
    assert receipt.counts["capture_test.localnote"] == 2
    assert not Changeset.objects.exists()


def test_native_upsert_retains_ambiguous_history_and_replay_refuses_atomically(actor):
    from django.core.exceptions import ValidationError

    with changeset(actor=actor):
        row = Note.objects.create(title="existing")
    with changeset(actor=actor):
        Note.objects.bulk_create(
            [Note(pk=row.pk, title="updated"), Note(pk=row.pk + 1, title="inserted")],
            update_conflicts=True,
            update_fields=["title"],
            unique_fields=["id"],
        )
        receipt = Note.history.first().history_changeset
    assert set(Note.history.filter(history_changeset=receipt).values_list("history_type", flat=True)) == {"~"}
    before = list(Note.objects.order_by("pk").values_list("title", flat=True))
    boundaries = Changeset.objects.count()
    with pytest.raises(ValidationError):
        receipt.revert(actor=actor)
    assert list(Note.objects.order_by("pk").values_list("title", flat=True)) == before
    assert Changeset.objects.count() == boundaries


def test_explicit_registration_requires_boundary_before_sql():
    with pytest.raises(ChangesetRequired):
        Explicit.objects.create(title="must not persist")
    assert not Explicit.objects.exists()


def test_existing_primary_key_proxy_and_mti_use_native_update_marker():
    with changeset():
        original = DetailedNote.objects.create(title="first", detail="one")
    with changeset():
        NoteProxy(pk=original.pk, title="proxy", number=0).save()
        DetailedNote(pk=original.pk, title="mti", detail="two", number=0).save()
    assert list(Note.history.order_by("history_id").values_list("history_type", flat=True)) == [
        "+",
        "~",
        "~",
    ]
    assert list(DetailedNote.history.order_by("history_id").values_list("history_type", flat=True)) == ["+", "~"]


def test_custom_history_without_source_identity_requires_explicit_colocation(monkeypatch):
    from types import SimpleNamespace

    from django.core.exceptions import ImproperlyConfigured
    from django.test.utils import isolate_apps

    from angee.provenance.engine.base import ChangesetAttribution
    from angee.provenance.engine.history import ModelHistory

    with isolate_apps():

        class LegacyHistory(ChangesetAttribution):
            class Meta:
                app_label = "legacy"

        with pytest.raises(ImproperlyConfigured, match="use_base_model_db=True"):
            ModelHistory().source_database_attributes(LegacyHistory, using="other")
        assert ModelHistory(use_base_model_db=True).source_database_attributes(LegacyHistory, using="other") == {}
        monkeypatch.setattr(
            "angee.provenance.engine.history.get_history_manager_for_model",
            lambda model: SimpleNamespace(model=LegacyHistory),
        )
        with pytest.raises(ValueError, match="must remain on its source database"):
            ModelHistory(use_base_model_db=True).destination(LegacyHistory, "other", explicit="default")
        assert ModelHistory().source_database_attributes(Note.history.model, using="other") == {
            "source_database": "other"
        }


@pytest.mark.django_db(databases=["default", "other"], transaction=True)
@pytest.mark.parametrize("using", ["default", "other"])
def test_mti_parent_capture_uses_its_own_identity(using):
    from tests.provenance_engine.capture_app.models import IndependentNote

    with changeset():
        row = IndependentNote.objects.using(using).create(id=11, child_key=71, title="parent", detail="child")
        row.title = "changed"
        row.save()
    assert row.pk == 71
    assert row._state.db == using
    assert set(row.history.values_list("source_database", flat=True)) == {using}
    assert list(Note.history.filter(id=11).order_by("history_id").values_list("title", "history_type")) == [
        ("parent", "+"),
        ("changed", "~"),
    ]
    assert not Note.history.filter(id=71).exists()
    assert row.history.count() == 2


def test_bulk_history_of_attributed_rows_uses_current_capture_attribution(actor):
    from angee.provenance.engine.context import current_changeset
    from tests.provenance_engine.capture_app.models import AttributedNote

    with changeset():
        row = AttributedNote.objects.create(title="before", changeset=current_changeset(record=True))
    original = row.changeset_id
    previous = row.history.values().get()
    with changeset():
        row.title = "after"
        bulk_update_with_history([row], AttributedNote, ["title"])
    latest = row.history.first()
    row.refresh_from_db()
    assert latest.title == "after"
    assert latest.history_changeset_id != original
    assert latest.changeset_id == original
    assert row.changeset_id == original
    assert row.history.values().get(history_id=previous["history_id"]) == previous
    latest.history_changeset.revert(actor=actor)
    row.refresh_from_db()
    assert row.title == "before"
    assert row.history.values().get(history_id=previous["history_id"]) == previous


def test_mti_parent_bulk_capture_uses_child_link_instead_of_child_pk():
    from tests.provenance_engine.capture_app.models import IndependentNote

    with changeset():
        row = IndependentNote.objects.create(id=11, child_key=71, title="before")
    with changeset():
        Note.objects.filter(pk=11).update(title="after")
    assert row.history.first().title == "after"
    assert row.history.count() == 2


def test_custom_history_attributes_cannot_override_tracked_stored_values():
    with changeset():
        row = Note.objects.create(title="stored")
    before = list(row.history.values())
    with changeset(), pytest.raises(TypeError, match="multiple values"), transaction.atomic():
        row.history.bulk_history_create([row], custom_historical_attrs={"title": "forged"})
    assert list(row.history.values()) == before


def test_distinct_mti_history_scope_and_inverse_isolate_colliding_child_ids():
    from tests.provenance_engine.capture_app.models import IndependentNote

    with changeset():
        row = IndependentNote.objects.create(id=11, child_key=71, title="target")
        decoy = IndependentNote.objects.create(id=71, child_key=11, title="decoy")
    with changeset():
        parent = Note.objects.get(pk=11)
        parent.title = "changed parent"
        parent.save()
        receipt = parent.history.first().history_changeset
    scopes = row.history_scopes()
    assert set(Note.history.filter(scopes[Note]).values_list("id", flat=True)) == {11}
    assert receipt in Changeset.objects.touching(row)
    assert receipt not in Changeset.objects.touching(decoy)
    touched = set(Changeset.objects.filter(pk=receipt.pk).records_touched())
    assert (IndependentNote, row.pk) in touched
    assert (IndependentNote, decoy.pk) not in touched


def test_explicit_history_views_use_common_ancestor_identity_without_expanding_defaults():
    from tests.provenance_engine.capture_app.models import IndependentNote

    class NoteView(Note):
        class Meta:
            app_label = "capture_test"
            proxy = True

        def history_tables(self):
            return Note, IndependentNote, DetailedNote

    class IndependentView(IndependentNote):
        class Meta:
            app_label = "capture_test"
            proxy = True

        def history_tables(self):
            return Note, IndependentNote, DetailedNote

    with changeset():
        IndependentNote.objects.create(id=11, child_key=71, title="target")
        IndependentNote.objects.create(id=71, child_key=11, title="decoy")
        DetailedNote.objects.create(id=11, title="target", detail="sibling")
        DetailedNote.objects.create(id=71, title="decoy", detail="other sibling")
    parent = NoteView.objects.get(pk=11)
    sibling = IndependentView.objects.get(pk=71)
    assert Note.objects.get(pk=11).history_tables() == (Note,)
    for record in (parent, sibling):
        scopes = record.history_scopes()
        assert set(IndependentNote.history.filter(scopes[IndependentNote]).values_list("child_key", flat=True)) == {71}
        assert set(DetailedNote.history.filter(scopes[DetailedNote]).values_list("id", flat=True)) == {11}


def test_native_history_opt_out_retains_model_save_behavior():
    with changeset():
        row = Note.objects.create(title="captured")
    row.title = "opted out"
    row.save_without_historical_record(update_fields=["title"])
    row.refresh_from_db()
    assert row.title == "opted out"
    assert row.history.count() == 1
