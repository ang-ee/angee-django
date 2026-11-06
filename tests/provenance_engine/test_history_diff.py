"""Native boundary diffs retain deletion and net-empty write semantics."""

import pytest

from angee.provenance.context import changeset
from angee.provenance.querysets import model_history
from tests.provenance_engine.capture_app.models import Note


@pytest.mark.django_db
def test_diff_uses_predecessor_before_the_boundary_and_supplied_predecessor(actor):
    with changeset(actor=actor):
        note = Note.objects.create(title="before")
    with changeset(actor=actor):
        note.title = "after"
        note.save(update_fields=["title"])
    first, last = list(note.history.order_by("history_id"))
    owner = model_history(Note)
    delta = owner.diff(last, last)
    assert [(change.field, change.old, change.new) for change in delta.changes] == [("title", "before", "after")]
    supplied = owner.diff(last, last, previous=first)
    assert supplied.changes == delta.changes


@pytest.mark.django_db
def test_creation_and_deletion_inside_one_boundary_has_no_net_field_diff(actor):
    with changeset(actor=actor):
        note = Note.objects.create(title="temporary")
        identity = note.pk
        note.delete()
    versions = list(Note.history.filter(id=identity).order_by("history_id"))
    assert [version.history_type for version in versions] == ["+", "-"]
    assert model_history(Note).diff(versions[0], versions[-1]).changes == []
