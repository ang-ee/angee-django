"""Record history and timeline share closed and readable-open document changesets."""

import pytest
from django.db import transaction
from django.test import override_settings
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.provenance.context import changeset, changeset_model, current_changeset
from tests.conftest import create_user
from tests.test_provenance_graphql import _history, _schema, _titled_page
from tests.test_timeline import read_timeline

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_tables")]


@override_settings(ANGEE_TIMELINE_KINDS=["angee.provenance.timeline.timeline_kinds"])
def test_record_viewer_gets_closed_changesets_in_history_and_timeline():
    owner, viewer = create_user("changeset-owner"), create_user("changeset-viewer")
    vault, page = _titled_page(owner)
    write_relationships([RelationshipTuple(to_object_ref(vault), "viewer", to_subject_ref(viewer))])
    with actor_context(owner), changeset(fresh=True, change_reason="Unpublished"):
        page.title = "Unpublished title"
        page.save(update_fields=["title"])
        unpublished = current_changeset()
    assert unpublished is not None
    changesets = changeset_model()
    with system_context(reason="test.unpublished_changeset"):
        changesets._base_manager.filter(pk=unpublished.pk).update(state=changesets.State.OPEN)
    assert changesets.objects.with_actor(owner).filter(pk=unpublished.pk).exists()
    assert not changesets.objects.with_actor(viewer).filter(pk=unpublished.pk).exists()
    history = _history(_schema(), viewer, "knowledge.Page", page)
    timeline = read_timeline(page, viewer)["timeline"]["entries"]
    assert history["total"] == 2
    assert {entry["change_reason"] for entry in history["entries"]} == {"Draft", "Rename"}
    assert {entry["detail"] for entry in timeline if entry["kind"] == "provenance.change"} == {
        "Draft", "Rename",
    }


@override_settings(ANGEE_TIMELINE_KINDS=["angee.provenance.timeline.timeline_kinds"])
def test_author_sees_open_edit_inside_transaction_without_exposing_it_to_record_viewer():
    owner, viewer = create_user("pending-owner"), create_user("pending-viewer")
    vault, page = _titled_page(owner)
    write_relationships([RelationshipTuple(to_object_ref(vault), "viewer", to_subject_ref(viewer))])

    with transaction.atomic():
        with actor_context(owner), changeset(fresh=True, change_reason="Edit pending title"):
            page.title = "Pending title"
            page.save(update_fields=["title"])
            pending = current_changeset()
        assert pending is not None
        assert changeset_model()._base_manager.get(pk=pending.pk).state == pending.State.OPEN

        for actor, expected in ((owner, True), (viewer, False)):
            history = _history(_schema(), actor, "knowledge.Page", page)
            timeline = read_timeline(page, actor)["timeline"]["entries"]
            assert ("Edit pending title" in {entry["change_reason"] for entry in history["entries"]}) is expected
            assert (
                "Edit pending title" in {
                    entry["detail"] for entry in timeline if entry["kind"] == "provenance.change"
                }
            ) is expected

    assert changeset_model()._base_manager.get(pk=pending.pk).state == pending.State.CLOSED
    assert "Edit pending title" in {
        entry["change_reason"] for entry in _history(_schema(), viewer, "knowledge.Page", page)["entries"]
    }
    assert "Edit pending title" in {
        entry["detail"]
        for entry in read_timeline(page, viewer)["timeline"]["entries"]
        if entry["kind"] == "provenance.change"
    }
